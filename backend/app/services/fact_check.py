"""News verification check: search the stored, evidenced corpus for coverage of a pasted headline, claim, or
URL. The URL can be anything — not only sources already registered in Xenom — in which case the page is
fetched live (same fetcher used for article ingestion) so its own title/author/date are real, not guessed, and
included as context. An LLM verdict (if configured) is grounded only in that stored corroboration or a clear
internal red flag; it never uses outside/trained knowledge to declare something true or false."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.fact_check import ExternalCoverage, FactCheckResult, MatchedCoverage
from app.services import llm
from app.services.article_page import fetch_article_page
from app.services.retrieval import search as retrieval_search
from app.providers.search import SearchProviderError
from app.schemas.search import SearchRequest
from app.services.search import SearchService

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_GENERIC_URL_WORDS = {"article", "news", "story", "post", "page", "index", "amp", "html", "www"}


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _query_text(raw: str, fetched_title: str | None = None) -> str:
    """A bare URL has no keywords to search on; use the live-fetched title if we have one, else its path segments."""
    if fetched_title:
        return fetched_title[:300]
    text = raw.strip()
    if _URL_RE.match(text):
        path = re.sub(r"https?://[^/]+/?", "", text)
        words = re.split(r"[-_/?&=.]+", path)
        meaningful = [w for w in words if len(w) > 2 and w.casefold() not in _GENERIC_URL_WORDS]
        return " ".join(meaningful)[:300]
    return text[:300]


async def _external_coverage(query: str) -> list[ExternalCoverage]:
    try:
        response = await SearchService().search(SearchRequest(query=query, limit=8, search_type="news"))
    except SearchProviderError:
        return []
    return [ExternalCoverage(title=item.title, url=item.url, source=item.domain, snippet=item.snippet,
                             published_at=item.published_at, provider=item.provider)
            for item in response.results]


async def check_news(session: AsyncSession, raw_input: str) -> FactCheckResult:
    clean = raw_input.strip()
    external: dict | None = None

    if _URL_RE.match(clean):
        page = await fetch_article_page(clean)
        if page.error:
            external = {"domain": _domain(clean), "title": None, "published_at": None, "author": None,
                        "fetch_status": "error", "error": page.error}
        else:
            external = {"domain": _domain(page.final_url or clean), "title": page.title,
                        "published_at": page.published_at.isoformat() if page.published_at else None,
                        "author": page.author, "fetch_status": "ok", "error": None}
        query = _query_text(clean, fetched_title=page.title if not page.error else None)
    else:
        query = _query_text(clean)

    response = await retrieval_search(session, query, mode="lexical", limit=8) if query.strip() else None
    external_hits = await _external_coverage(query) if query.strip() else []
    hits = response.results if response else []
    matched = [MatchedCoverage(article_id=h.article_id, title=h.title, url=h.url, source=h.source,
                                tier=h.tier, published_at=h.published_at) for h in hits]
    note = ("Checked against Xenom's own stored, evidenced corpus" +
            (f", plus the source page fetched live from {external['domain']}" if external and external["fetch_status"] == "ok" else
             f" (the page at {external['domain']} could not be fetched: {external['error']})" if external else "") +
            " — never a guess from outside knowledge.")

    if not matched and not external_hits:
        if external and external["fetch_status"] == "ok":
            when = f" (published {external['published_at']})" if external["published_at"] else ""
            reasoning = (f"No stored article covers this. The page itself was fetched from {external['domain']}{when}, "
                        "but that only confirms the page exists, not that its claims are true — it is not corroborated by any other tracked source.")
        elif external:
            reasoning = f"No stored article covers this, and the source URL could not be fetched ({external['error']}) to check its own content."
        else:
            reasoning = "No stored article covers this claim, so it cannot be corroborated or contradicted from the tracked corpus."
        return FactCheckResult(input=raw_input, verdict="UNVERIFIED", reasoning=reasoning, matched_coverage=[], external_coverage=[],
                       checked_against=len(hits), llm_used=False, note=note, external_source=external)

    evidence = "\n".join(f"- {m.title or m.url} | source: {m.source or 'unknown'} | tier: {m.tier or 'unknown'}" for m in matched)
    external_evidence = "\n".join(f"- {m.title} | source: {m.source or 'unknown'} | url: {m.url} | snippet: {m.snippet or ''}" for m in external_hits)
    context = f"CLAIM / SOURCE: {raw_input}\n"
    if external:
        context += f"FETCHED SOURCE PAGE: domain={external['domain']}, title={external['title']}, status={external['fetch_status']}\n"
    context += f"STORED COVERAGE FOUND:\n{evidence}"
    context += f"\nEXTERNAL SEARCH COVERAGE FOUND:\n{external_evidence}"
    try:
        verdict = await llm.run_structured(session, "fact_check", context, llm.FactCheckVerdict)
        return FactCheckResult(input=raw_input, verdict=verdict.verdict, reasoning=verdict.reasoning,
                               matched_coverage=matched, external_coverage=external_hits, checked_against=len(hits), llm_used=True,
                               note=note, external_source=external)
    except (llm.LLMUnavailable, llm.LLMOutputInvalid):
        if not matched:
            return FactCheckResult(
                input=raw_input, verdict="UNVERIFIED",
                reasoning=f"External coverage was found from {len(external_hits)} result(s), but no configured verifier was available to assess whether those reports corroborate the claim.",
                matched_coverage=[], external_coverage=external_hits, checked_against=len(hits), llm_used=False,
                note=note, external_source=external,
            )
        return FactCheckResult(
            input=raw_input, verdict="LIKELY_REAL",
            reasoning=f"{len(matched)} stored source(s) cover this claim (LLM unavailable, so this is a coverage match only, not a reasoned verdict).",
            matched_coverage=matched, external_coverage=external_hits, checked_against=len(hits), llm_used=False, note=note, external_source=external,
        )
