"""Retrieval interface (PDF s.8): lexical, vector and hybrid behind one function, always over the accepted corpus.

    Query + filters + client context -> lexical retrieval (FTS) -> vector retrieval -> rank fusion -> optional re-rank
    -> results with provenance

* Filters run BEFORE ranking and are identical for both retrievers (``apply_filters``).
* Fusion is reciprocal rank fusion with configurable weights; the optional re-rank (client relevance, source reliability,
  recency) is off unless its weight is set.
* Semantic search only ranks items inside the accepted corpus. It never turns a rejected item into a result.
* If vectors are unavailable (no provider, provider error, nothing embedded) the answer is the lexical one and the
  response says so (``fallback_reason``).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.models.architecture import DocumentChunk
from app.models.content import Article
from app.models.sources import Source
from app.schemas.article_search import ArticleSearchHit, ArticleSearchResponse
from app.services import embeddings as emb
from app.services.article_search import apply_filters, matched_query_terms, search_articles, suggested_client_filters
from app.services.articles import _relevance_evidence
from app.services.markup import strip_markup

MODES = ("lexical", "semantic", "hybrid")
_TIER_RELIABILITY = {"TIER_1": 1.0, "TIER_2": 0.75, "TIER_3": 0.5, "TIER_4": 0.25}


async def _vector_ids(session: AsyncSession, query: str, provider: emb.EmbeddingProvider, qvec: list[float], *, limit: int,
                      filters: dict) -> list[tuple]:
    """(article_id, similarity, field, start, end) best chunk per article, most similar first."""
    distance = literal_column(f"(document_chunks.embedding <=> '{emb.vector_literal(qvec)}'::vector)")
    statement = (
        select(Article.id, distance.label("dist"), DocumentChunk.field, DocumentChunk.char_start, DocumentChunk.char_end)
        .join(DocumentChunk, DocumentChunk.article_id == Article.id)
        .outerjoin(Source, Article.source_id == Source.id)
        .where(Article.processing_status == "accepted", Article.quality_gate_passed.is_(True))
        .where(DocumentChunk.embedding_model == provider.model, literal_column("document_chunks.embedding").is_not(None))
    )
    statement = apply_filters(statement, **filters).order_by("dist", Article.id, DocumentChunk.chunk_index).limit(limit * 6)
    best: dict = {}
    for article_id, dist, field, start, end in (await session.execute(statement)).all():
        if article_id not in best:
            best[article_id] = (article_id, 1.0 - float(dist), field, start, end)
    return sorted(best.values(), key=lambda r: (-r[1], str(r[0])))[:limit]


async def _hits_for(session: AsyncSession, ordered: list, query: str) -> dict:
    ids = [r[0] for r in ordered]
    if not ids:
        return {}
    rows = (await session.execute(
        select(Article, Source.name, Source.source_type, Source.tier).outerjoin(Source, Article.source_id == Source.id).where(Article.id.in_(ids))
    )).all()
    out = {}
    for article, source_name, source_type, tier in rows:
        evidence = _relevance_evidence(article.score_reason)
        out[article.id] = ArticleSearchHit(
            article_id=article.id, title=strip_markup(article.title), summary=strip_markup(article.summary),
            url=article.canonical_url or article.url, published_at=article.published_at, client=(evidence or {}).get("entity"),
            source=source_name, source_class=(evidence or {}).get("source_type") or source_type, tier=tier,
            matched_terms=matched_query_terms(query, article.title, article.summary), relevance=evidence, score=0.0,
        )
    return out


def _rerank(hit: ArticleSearchHit, score: float, now: datetime) -> float:
    s = get_settings()
    if not (s.rerank_weight_client or s.rerank_weight_reliability or s.rerank_weight_recency):
        return score
    client = float((hit.relevance or {}).get("relevance_score") or 0.0)
    reliability = _TIER_RELIABILITY.get(hit.tier or "TIER_3", 0.5)
    age_days = max(0.0, (now - hit.published_at).total_seconds() / 86400) if hit.published_at else 365.0
    return score * (1.0 + s.rerank_weight_client * client + s.rerank_weight_reliability * reliability
                    + s.rerank_weight_recency * (1.0 / (1.0 + age_days / 30.0)))


async def search(session: AsyncSession, query: str, *, mode: str = "lexical", limit: int = 20, **filters) -> ArticleSearchResponse:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    filter_keys = ("entity_key", "source", "date_from", "date_to", "tier", "source_class", "topic", "label", "person", "technology")
    filters = {k: filters.get(k) for k in filter_keys}
    query = " ".join(query.split())
    if not query:
        raise ValueError("query must not be blank")
    lexical_limit = limit if mode == "lexical" else max(limit * 5, 50)
    lexical = await search_articles(session, query, limit=lexical_limit, **filters)
    if mode == "lexical":
        return lexical.model_copy(update={"mode": "lexical", "mode_requested": "lexical"})

    def fallback(reason: str) -> ArticleSearchResponse:
        return lexical.model_copy(update={"results": lexical.results[:limit], "count": len(lexical.results[:limit]), "mode": "lexical",
                                          "mode_requested": mode, "fallback_reason": reason})

    provider = emb.get_provider()
    if provider is None:
        return fallback("no embedding provider is configured")
    try:
        (qvec,), _p = await emb.embed_texts(session, [query], operation="embedding:query")
        await session.commit()
        vector = await _vector_ids(session, query, provider, qvec, limit=lexical_limit, filters=filters)
    except emb.EmbeddingUnavailable as exc:
        await session.commit()
        return fallback(f"embedding unavailable: {exc}")
    if not vector:
        return fallback("no embedded chunks in the filtered corpus")

    hits = {h.article_id: h for h in lexical.results}
    vector_info = {r[0]: r for r in vector}
    extra = await _hits_for(session, [r for r in vector if r[0] not in hits], query)
    hits.update(extra)
    s = get_settings()
    lexical_rank = {h.article_id: i + 1 for i, h in enumerate(lexical.results)}
    vector_rank = {r[0]: i + 1 for i, r in enumerate(vector)}
    now = datetime.now(timezone.utc)
    fused: dict = {}
    for article_id, hit in hits.items():
        score = 0.0
        if mode == "hybrid" and article_id in lexical_rank:
            score += s.retrieval_weight_lexical / (s.retrieval_rrf_k + lexical_rank[article_id])
        if article_id in vector_rank:
            score += (s.retrieval_weight_vector if mode == "hybrid" else 1.0) / (s.retrieval_rrf_k + vector_rank[article_id])
        if score:
            fused[article_id] = _rerank(hit, score, now)
    ordered = sorted(fused, key=lambda i: (-fused[i], str(i)))[:limit]
    results = []
    for article_id in ordered:
        info = vector_info.get(article_id)
        retrieval = {"lexical_rank": lexical_rank.get(article_id), "vector_rank": vector_rank.get(article_id),
                     "vector_similarity": round(info[1], 4) if info else None,
                     "best_chunk": {"field": info[2], "start": info[3], "end": info[4]} if info else None, "model": provider.model}
        results.append(hits[article_id].model_copy(update={"score": round(fused[article_id], 6), "retrieval": retrieval}))
    return ArticleSearchResponse(query=query, count=len(results), results=results, mode=mode, mode_requested=mode,
                                 suggested_filters={} if filters.get("entity_key") else suggested_client_filters(query))


async def similar_articles(session: AsyncSession, article_id, *, limit: int = 5) -> dict:
    """Related items (PDF s.10 'similar articles, related research'): accepted articles whose title/summary chunks are nearest to
    this article's, over the same corpus. Needs embedded chunks; without them the answer says so."""
    from sqlalchemy import text as sql_text
    rows = (await session.execute(sql_text(
        "select c2.article_id, max(1 - (c1.embedding <=> c2.embedding)) as sim, c1.embedding_model "
        "from document_chunks c1 join document_chunks c2 on c2.embedding_model = c1.embedding_model and c2.article_id <> c1.article_id "
        "join articles a on a.id = c2.article_id and a.processing_status = 'accepted' and a.quality_gate_passed "
        "where c1.article_id = :a and c1.embedding is not null and c2.embedding is not null and c1.field in ('title', 'summary') "
        "group by c2.article_id, c1.embedding_model order by sim desc, c2.article_id limit :n"), {"a": article_id, "n": limit})).all()
    if not rows:
        return {"status": "NO_EMBEDDINGS", "detail": "This article has no embedded chunks (no provider configured, or it is not yet embedded).", "results": []}
    hits = await _hits_for(session, [(r[0],) for r in rows], "")
    return {"status": "ok", "model": rows[0][2], "results": [
        {**hits[r[0]].model_dump(mode="json"), "similarity": round(float(r[1]), 4)} for r in rows if r[0] in hits]}


async def near_duplicates(session: AsyncSession, *, threshold: float = 0.93, limit: int = 50) -> dict:
    """Possible near-duplicates by title-chunk embedding similarity (PDF s.10). REPORT ONLY: a duplicate never discards other
    articles, so nothing is merged or rejected here; a person decides."""
    from sqlalchemy import text as sql_text
    rows = (await session.execute(sql_text(
        "select c1.article_id, c2.article_id, 1 - (c1.embedding <=> c2.embedding) as sim, c1.embedding_model "
        "from document_chunks c1 join document_chunks c2 on c2.embedding_model = c1.embedding_model and c1.article_id < c2.article_id "
        "join articles a1 on a1.id = c1.article_id and a1.processing_status = 'accepted' join articles a2 on a2.id = c2.article_id and a2.processing_status = 'accepted' "
        "where c1.field = 'title' and c2.field = 'title' and c1.embedding is not null and c2.embedding is not null "
        "and 1 - (c1.embedding <=> c2.embedding) >= :t order by sim desc, c1.article_id, c2.article_id limit :n"), {"t": threshold, "n": limit})).all()
    ids = {r[0] for r in rows} | {r[1] for r in rows}
    hits = await _hits_for(session, [(i,) for i in ids], "")
    return {"threshold": threshold, "pairs": [{"similarity": round(float(r[2]), 4), "model": r[3], "a": hits[r[0]].model_dump(mode="json", include={"article_id", "title", "source", "url", "published_at"}),
                                               "b": hits[r[1]].model_dump(mode="json", include={"article_id", "title", "source", "url", "published_at"})}
                                              for r in rows if r[0] in hits and r[1] in hits],
            "note": "Report only. Nothing is merged or rejected automatically."}
