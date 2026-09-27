"""Search -> evidence: verbatim quote spans from STORED article text for the articles a search returns.

Nothing is generated or paraphrased. Every span is a slice of a stored column (title, summary or
raw_text) with offsets, so `text[start:end] == quote` can always be re-checked. Terms come from the
query plus the matched terms already stored in `score_reason`. Ranking is whatever search_articles returns.
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Article
from app.services.markup import has_markup, strip_markup  # noqa: F401  (re-exported)
from app.schemas.article_evidence import ArticleEvidence, ArticleEvidenceResponse, EvidenceSpan
from app.services.retrieval import search as retrieve

FIELDS = ("title", "summary", "raw_text")
MAX_SPANS = 3
MAX_QUOTE = 300
_BOUNDARY = ".!?\n"
_STOP = frozenset("the and for with from that this are was were has have had its into over about after new of in on to is at by an as it or be".split())


def collect_terms(query: str, relevance: dict | None) -> list[str]:
    terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) >= 2 and t not in _STOP}
    for holder in [relevance or {}, *((relevance or {}).get("entities") or [])]:
        terms.update(str(t).lower() for t in holder.get("matched_terms") or [] if str(t).strip())
    return sorted(terms)


def _window(text: str, start: int, end: int) -> tuple[int, int]:
    """Sentence around the match, clamped to MAX_QUOTE chars, trimmed of whitespace."""
    lo = max((text.rfind(c, 0, start) for c in _BOUNDARY), default=-1) + 1
    highs = [i for i in (text.find(c, end) for c in _BOUNDARY) if i != -1]
    hi = min(highs) + 1 if highs else len(text)
    if hi - lo > MAX_QUOTE:
        lo, hi = max(lo, start - (MAX_QUOTE - (end - start)) // 2), 0
        hi = min(len(text), lo + MAX_QUOTE)
    while lo < hi and text[lo].isspace():
        lo += 1
    while hi > lo and text[hi - 1].isspace():
        hi -= 1
    return lo, hi


def find_spans(fields: dict[str, str | None], terms: list[str], max_spans: int = MAX_SPANS) -> list[EvidenceSpan]:
    """Deterministic: fields in FIELDS order, then text position. Overlaps and repeated quotes are skipped."""
    if not terms:
        return []
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in sorted(terms, key=lambda t: (-len(t), t))) + r")\b",
                         re.IGNORECASE)
    spans: list[EvidenceSpan] = []
    seen_quotes: set[str] = set()
    for name in FIELDS:
        text = fields.get(name) or ""
        taken: list[tuple[int, int]] = []
        for m in pattern.finditer(text):
            if len(spans) >= max_spans:
                return spans
            lo, hi = _window(text, m.start(), m.end())
            if any(lo < b and a < hi for a, b in taken):
                continue
            quote = text[lo:hi]
            if not quote or quote in seen_quotes or has_markup(quote):
                continue
            found = sorted({t for t in terms if re.search(r"\b" + re.escape(t) + r"\b", quote, re.IGNORECASE)})
            spans.append(EvidenceSpan(field=name, start=lo, end=hi, quote=quote, terms=found))
            taken.append((lo, hi))
            seen_quotes.add(quote)
    return spans


async def search_with_evidence(
    session: AsyncSession,
    query: str,
    *,
    entity_key: str | None = None,
    source: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    tier: str | None = None,
    source_class: str | None = None,
    topic: str | None = None,
    label: str | None = None,
    person: str | None = None,
    technology: str | None = None,
    mode: str = "lexical",
    limit: int = 10,
) -> ArticleEvidenceResponse:
    found = await retrieve(
        session, query, mode=mode, entity_key=entity_key, source=source, date_from=date_from, date_to=date_to,
        tier=tier, source_class=source_class, topic=topic, label=label, person=person, technology=technology, limit=limit,
    )
    ids = [h.article_id for h in found.results]
    rows = {}
    if ids:
        result = await session.execute(select(Article.id, Article.title, Article.summary, Article.raw_text).where(Article.id.in_(ids)))
        rows = {r[0]: {"title": r[1], "summary": r[2], "raw_text": r[3]} for r in result.all()}
    out = []
    for h in found.results:
        fields = rows.get(h.article_id, {})
        spans = find_spans(fields, collect_terms(found.query, h.relevance))
        chunk = (h.retrieval or {}).get("best_chunk")
        if chunk and not spans:
            # A semantic hit may share no literal term with the query: its evidence is the best-matching stored chunk itself.
            spans = _chunk_span(fields, chunk)
        out.append(ArticleEvidence(
            article_id=h.article_id, url=h.url, title=h.title, published_at=h.published_at, client=h.client,
            source=h.source, source_class=h.source_class, tier=h.tier, relevance=h.relevance, score=h.score,
            retrieval=h.retrieval, spans=spans,
        ))
    return ArticleEvidenceResponse(query=found.query, count=len(out), results=out, suggested_filters=found.suggested_filters,
                                   mode=found.mode, mode_requested=found.mode_requested, fallback_reason=found.fallback_reason)


def _chunk_span(fields: dict, chunk: dict) -> list[EvidenceSpan]:
    """The stored chunk as a verbatim, markup-free span (offsets index the stored column; long chunks keep their start)."""
    text_ = fields.get(chunk["field"]) or ""
    start, end = int(chunk["start"]), min(int(chunk["end"]), int(chunk["start"]) + MAX_QUOTE)
    quote = text_[start:end]
    if not quote.strip() or has_markup(quote):
        return []
    return [EvidenceSpan(field=chunk["field"], start=start, end=end, quote=quote, terms=[])]
