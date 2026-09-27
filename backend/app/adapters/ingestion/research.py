"""Research-source adapters (PDF s.11): arXiv (papers + authors), the Hugging Face model hub (open-source
models), and OpenAlex (papers + authors + institutions across all publishers, not just arXiv preprints).

All are public APIs that state what they return; nothing is inferred. ``parse_*`` are pure functions (unit-tested
against recorded payloads); ``fetch`` performs the request. A failed request or an unexpected payload raises
``AdapterError`` so the source is recorded as failed, never as "zero results".
"""
from __future__ import annotations

import uuid
import asyncio
from datetime import datetime, timezone
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx

from app.config.settings import get_settings
from .base import AdapterError, NormalizedDocument, clean_text, retrieval_time

ARXIV_API = "https://export.arxiv.org/api/query"
HF_API = "https://huggingface.co/api/models"
OPENALEX_API = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"
CROSSREF_API = "https://api.crossref.org/works"
_HEADERS = {"User-Agent": "XENOM-research-adapter/1.0 (public API, low volume)"}
# OpenAlex's "polite pool" (a contact mailto) gets higher, steadier rate limits than anonymous requests;
# no account/key is required, this is just an identifying query param OpenAlex documents publicly.
_OPENALEX_MAILTO = "research@xenom-intel.example"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_arxiv(xml_text: str, *, source_id: uuid.UUID, retrieved_at: datetime | None = None) -> list[NormalizedDocument]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise AdapterError("arXiv response is not valid Atom XML") from exc
    if not root.tag.endswith("feed"):
        raise AdapterError("arXiv response has no Atom feed element")
    documents: list[NormalizedDocument] = []
    for entry in root.findall(f"{_ATOM}entry"):
        url = (entry.findtext(f"{_ATOM}id") or "").strip()
        title = clean_text(entry.findtext(f"{_ATOM}title") or "")
        summary = clean_text(entry.findtext(f"{_ATOM}summary") or "")
        if not url or not title:
            continue
        authors = []
        for position, author in enumerate(entry.findall(f"{_ATOM}author")):
            name = clean_text(author.findtext(f"{_ATOM}name") or "")
            if name:
                affiliation = clean_text(author.findtext(f"{_ARXIV}affiliation") or "") or None
                authors.append({"name": name, "affiliation": affiliation, "position": position})
        categories = [c.get("term") for c in entry.findall(f"{_ATOM}category") if c.get("term")]
        content = f"{title}. {summary}"
        documents.append(NormalizedDocument(
            source_id=source_id, canonical_url=url, title=title, published_at=_parse_iso(entry.findtext(f"{_ATOM}published")),
            retrieved_at=retrieval_time(retrieved_at), raw_content=content, normalized_content=content,
            content_type="application/atom+xml",
            metadata={"adapter": "arxiv_api", "authors": authors, "categories": categories, "publisher_domain": "arxiv.org",
                      "author": ", ".join(a["name"] for a in authors[:6]) or None},
        ))
    return documents


def parse_huggingface(payload: object, *, source_id: uuid.UUID, retrieved_at: datetime | None = None) -> list[NormalizedDocument]:
    if not isinstance(payload, list):
        raise AdapterError("Hugging Face response is not a list of models")
    documents: list[NormalizedDocument] = []
    for model in payload:
        if not isinstance(model, dict):
            continue
        model_id = model.get("id") or model.get("modelId")
        if not model_id:
            continue
        author = model.get("author") or (model_id.split("/")[0] if "/" in model_id else None)
        task = model.get("pipeline_tag")
        tags = [t for t in (model.get("tags") or []) if isinstance(t, str)][:12]
        parts = [f"Model {model_id}"] + ([f"published by {author}"] if author else []) + ([f"task: {task}"] if task else [])
        if tags:
            parts.append("tags: " + ", ".join(tags))
        content = "; ".join(parts) + "."
        documents.append(NormalizedDocument(
            source_id=source_id, canonical_url=f"https://huggingface.co/{model_id}", title=str(model_id),
            published_at=_parse_iso(model.get("createdAt") or model.get("lastModified")),
            retrieved_at=retrieval_time(retrieved_at), raw_content=content, normalized_content=content,
            content_type="application/json",
            metadata={"adapter": "huggingface_hub", "authors": [], "publisher_domain": "huggingface.co", "author": author,
                      "downloads": model.get("downloads"), "likes": model.get("likes"), "pipeline_tag": task, "tags": tags,
                      "source_metadata": {"downloads": model.get("downloads"), "likes": model.get("likes"), "pipeline_tag": task, "tags": tags}},
        ))
    return documents


def parse_openalex(payload: object, *, source_id: uuid.UUID, retrieved_at: datetime | None = None) -> list[NormalizedDocument]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise AdapterError("OpenAlex response is not a work-list object")
    documents: list[NormalizedDocument] = []
    for work in payload["results"]:
        if not isinstance(work, dict):
            continue
        title = clean_text(work.get("title") or work.get("display_name") or "")
        work_id = work.get("id")  # e.g. "https://openalex.org/W2741809807"
        if not title or not work_id:
            continue
        location = work.get("primary_location") or {}
        url = location.get("landing_page_url") or (f"https://doi.org/{work['doi'].removeprefix('https://doi.org/')}" if work.get("doi") else work_id)
        authors = []
        for position, ship in enumerate(work.get("authorships") or []):
            name = clean_text(((ship or {}).get("author") or {}).get("display_name") or "")
            if not name:
                continue
            insts = [i.get("display_name") for i in (ship.get("institutions") or []) if i.get("display_name")]
            authors.append({"name": name, "affiliation": clean_text(insts[0]) if insts else None, "position": position})
        venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
        cited_by = work.get("cited_by_count")
        abstract = _openalex_abstract(work.get("abstract_inverted_index"))
        content = f"{title}. {abstract}".strip()
        documents.append(NormalizedDocument(
            source_id=source_id, canonical_url=url, title=title, published_at=_parse_iso(work.get("publication_date")),
            retrieved_at=retrieval_time(retrieved_at), raw_content=content, normalized_content=content,
            content_type="application/json",
            metadata={"adapter": "openalex_api", "authors": authors, "publisher_domain": "openalex.org", "venue": venue,
                      "author": ", ".join(a["name"] for a in authors[:6]) or None, "openalex_id": work_id,
                      "source_metadata": {"cited_by_count": cited_by, "venue": venue}},
        ))
    return documents


def parse_semantic_scholar(payload: object, *, source_id: uuid.UUID, retrieved_at: datetime | None = None) -> list[NormalizedDocument]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise AdapterError("Semantic Scholar response is not a paper-list object")
    documents: list[NormalizedDocument] = []
    for paper in payload["data"]:
        if not isinstance(paper, dict) or not paper.get("title"):
            continue
        paper_id = str(paper.get("paperId") or "")
        url = ((paper.get("openAccessPdf") or {}).get("url") or
               paper.get("url") or (f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else ""))
        if not url:
            continue
        authors = [{"name": clean_text(a.get("name") or ""), "affiliation": None, "position": i}
                   for i, a in enumerate(paper.get("authors") or []) if isinstance(a, dict) and a.get("name")]
        title = clean_text(str(paper["title"]))
        abstract = clean_text(str(paper.get("abstract") or ""))
        content = f"{title}. {abstract}".strip()
        documents.append(NormalizedDocument(
            source_id=source_id, canonical_url=url, title=title,
            published_at=_parse_iso(paper.get("publicationDate")), retrieved_at=retrieval_time(retrieved_at),
            raw_content=content, normalized_content=content, content_type="application/json",
            metadata={"adapter": "semantic_scholar_api", "authors": authors, "publisher_domain": "semanticscholar.org",
                      "author": ", ".join(a["name"] for a in authors[:6]) or None,
                      "source_metadata": {"citation_count": paper.get("citationCount"), "venue": paper.get("venue")}},
        ))
    return documents


def parse_crossref(payload: object, *, source_id: uuid.UUID, retrieved_at: datetime | None = None) -> list[NormalizedDocument]:
    if not isinstance(payload, dict) or not isinstance((payload.get("message") or {}).get("items"), list):
        raise AdapterError("Crossref response is not a work-list object")
    documents: list[NormalizedDocument] = []
    for work in payload["message"]["items"]:
        title = clean_text(str((work.get("title") or [""])[0])) if isinstance(work, dict) else ""
        doi = work.get("DOI") if isinstance(work, dict) else None
        if not title or not doi:
            continue
        authors = [{"name": clean_text(f'{a.get("given", "")} {a.get("family", "")}'),
                    "affiliation": clean_text((a.get("affiliation") or [{}])[0].get("name", "")) or None,
                    "position": i} for i, a in enumerate(work.get("author") or []) if a.get("family")]
        date_parts = ((work.get("published") or {}).get("date-parts") or [[]])[0]
        published = datetime(*([int(x) for x in date_parts] + [1, 1])[:3], tzinfo=timezone.utc) if date_parts else None
        url = work.get("URL") or f"https://doi.org/{doi}"
        content = f"{title}. Venue: {clean_text((work.get('container-title') or [''])[0])}."
        documents.append(NormalizedDocument(
            source_id=source_id, canonical_url=url, title=title, published_at=published,
            retrieved_at=retrieval_time(retrieved_at), raw_content=content, normalized_content=content,
            content_type="application/json", metadata={"adapter": "crossref_api", "authors": authors,
            "publisher_domain": "doi.org", "author": ", ".join(a["name"] for a in authors[:6]) or None,
            "source_metadata": {"doi": doi, "venue": (work.get("container-title") or [None])[0]}},
        ))
    return documents


def _openalex_abstract(inverted_index: dict | None) -> str:
    """OpenAlex stores abstracts as a word -> [positions] inverted index (to respect publisher copyright);
    reassemble the plain text from it, or '' if none is stored."""
    if not isinstance(inverted_index, dict) or not inverted_index:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            slots[pos] = word
    return " ".join(slots[i] for i in sorted(slots))[:1500]


class OpenAlexAdapter:
    def __init__(self) -> None:
        self.errors: list[str] = []

    async def fetch(self, query: str, *, source_id: uuid.UUID, retrieved_at: datetime | None = None, max_results: int = 25) -> list[NormalizedDocument]:
        settings = get_settings()
        params = {"search": query, "per_page": str(min(max_results, 200)), "mailto": _OPENALEX_MAILTO}
        if settings.openalex_api_key:
            params["api_key"] = settings.openalex_api_key
        async with httpx.AsyncClient(timeout=30, headers=_HEADERS, follow_redirects=True) as http:
            response = await http.get(OPENALEX_API, params=params)
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            detail = response.json().get("message", "")
            raise AdapterError(f"OpenAlex API is rate-limiting (HTTP 429, retry-after={retry_after or 'unknown'}): {detail}".strip())
        if response.status_code != 200:
            raise AdapterError(f"OpenAlex API returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError("OpenAlex response is not JSON") from exc
        return parse_openalex(payload, source_id=source_id, retrieved_at=retrieved_at)


class ArxivAdapter:
    def __init__(self) -> None:
        self.errors: list[str] = []

    async def fetch(self, query: str, *, source_id: uuid.UUID, retrieved_at: datetime | None = None, max_results: int = 25) -> list[NormalizedDocument]:
        # arXiv's legacy export endpoint 406s on a percent-encoded ':' in search_query (e.g. cat%3Acs.RO); the field
        # prefixes (cat:, abs:, ti:, ...) must stay literal, so ':' is kept out of quote_plus's escaped set. sortBy/
        # sortOrder are omitted: arXiv's default (relevance) ordering is sufficient and keeps the query minimal.
        url = f"{ARXIV_API}?search_query={quote_plus(query, safe=':')}&start=0&max_results={max_results}"
        async with httpx.AsyncClient(timeout=30, headers=_HEADERS, follow_redirects=True) as http:
            response = await http.get(url)
        if response.status_code != 200:
            raise AdapterError(f"arXiv API returned HTTP {response.status_code}")
        return parse_arxiv(response.text, source_id=source_id, retrieved_at=retrieved_at)


class SemanticScholarAdapter:
    def __init__(self) -> None:
        self.errors: list[str] = []

    async def fetch(self, query: str, *, source_id: uuid.UUID, retrieved_at: datetime | None = None, max_results: int = 25) -> list[NormalizedDocument]:
        settings = get_settings()
        headers = dict(_HEADERS)
        if settings.semantic_scholar_api_key:
            headers["x-api-key"] = settings.semantic_scholar_api_key
        params = {"query": query, "limit": str(min(max_results, 100)), "fields": "title,abstract,year,authors,venue,url,openAccessPdf,citationCount,publicationDate"}
        async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as http:
            response = await http.get(SEMANTIC_SCHOLAR_API, params=params)
        if response.status_code == 429:
            raise AdapterError(f"Semantic Scholar API is rate-limiting (HTTP 429, retry-after={response.headers.get('retry-after', 'unknown')})")
        if response.status_code != 200:
            raise AdapterError(f"Semantic Scholar API returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError("Semantic Scholar response is not JSON") from exc
        return parse_semantic_scholar(payload, source_id=source_id, retrieved_at=retrieved_at)


class CrossrefAdapter:
    async def fetch(self, query: str, *, source_id: uuid.UUID, retrieved_at: datetime | None = None, max_results: int = 25) -> list[NormalizedDocument]:
        params = {"query.title": query, "rows": str(min(max_results, 100)), "select": "DOI,title,author,published,URL,container-title"}
        async with httpx.AsyncClient(timeout=30, headers={**_HEADERS, "User-Agent": _HEADERS["User-Agent"]}) as http:
            response = await http.get(CROSSREF_API, params=params)
        if response.status_code != 200:
            raise AdapterError(f"Crossref API returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError("Crossref response is not JSON") from exc
        return parse_crossref(payload, source_id=source_id, retrieved_at=retrieved_at)


class HuggingFaceAdapter:
    def __init__(self) -> None:
        self.errors: list[str] = []

    async def fetch(self, query: str, *, source_id: uuid.UUID, retrieved_at: datetime | None = None, max_results: int = 25) -> list[NormalizedDocument]:
        params = {"search": query, "sort": "createdAt", "direction": "-1", "limit": str(max_results)}
        async with httpx.AsyncClient(timeout=30, headers=_HEADERS, follow_redirects=True) as http:
            response = await http.get(HF_API, params=params)
        if response.status_code != 200:
            raise AdapterError(f"Hugging Face API returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError("Hugging Face response is not JSON") from exc
        return parse_huggingface(payload, source_id=source_id, retrieved_at=retrieved_at)
