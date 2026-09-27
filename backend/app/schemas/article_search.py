from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ArticleSearchHit(BaseModel):
    article_id: uuid.UUID
    title: str | None
    summary: str | None
    url: str
    published_at: datetime | None
    client: str | None  # from stored score_reason; None for rows without pipeline evidence
    source: str | None
    source_class: str | None
    tier: str | None  # source trust tier (TIER_1 official ... TIER_3)
    matched_terms: list[str] = []  # query terms literally present in the stored title/summary (why it matched)
    relevance: dict | None  # stored score_reason evidence, unmodified
    score: float  # lexical: ts_rank_cd over title (A) + summary (B); semantic/hybrid: fused rank score
    retrieval: dict | None = None  # semantic/hybrid only: lexical_rank, vector_rank, vector_similarity, best_chunk, model


class ArticleSearchResponse(BaseModel):
    query: str
    count: int
    suggested_filters: dict[str, list[str]] = {}  # e.g. {"client": ["jcb"]}: named in the query, NOT applied
    mode: str = "lexical"  # the retrieval actually used
    mode_requested: str = "lexical"
    fallback_reason: str | None = None  # set when semantic/hybrid was requested but lexical answered
    results: list[ArticleSearchHit]
