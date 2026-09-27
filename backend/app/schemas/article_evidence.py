from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class EvidenceSpan(BaseModel):
    field: str  # "title" | "summary" | "raw_text": which stored column the quote comes from
    start: int  # offsets into that stored column: text[start:end] == quote (verbatim)
    end: int
    quote: str
    terms: list[str]  # query / stored-evidence terms found inside the quote


class ArticleEvidence(BaseModel):
    article_id: uuid.UUID
    url: str
    title: str | None
    published_at: datetime | None
    client: str | None
    source: str | None
    source_class: str | None
    tier: str | None
    relevance: dict | None  # stored score_reason evidence, unmodified
    score: float
    retrieval: dict | None = None
    spans: list[EvidenceSpan]


class ArticleEvidenceResponse(BaseModel):
    query: str
    count: int
    suggested_filters: dict[str, list[str]] = {}
    mode: str = "lexical"
    mode_requested: str = "lexical"
    fallback_reason: str | None = None
    results: list[ArticleEvidence]
