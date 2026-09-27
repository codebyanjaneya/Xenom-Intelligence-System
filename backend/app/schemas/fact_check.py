"""News verification (fake-news check): given a pasted headline/text/URL, tell the user whether it is
corroborated by stored coverage, and why — never a fabricated verdict."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class FactCheckRequest(BaseModel):
    input: str  # a headline, a paragraph of text, a URL, or a source name


class MatchedCoverage(BaseModel):
    article_id: uuid.UUID
    title: str | None
    url: str
    source: str | None
    tier: str | None
    published_at: datetime | None


class ExternalCoverage(BaseModel):
    title: str
    url: str
    source: str | None = None
    snippet: str | None = None
    published_at: datetime | None = None
    provider: str


class FactCheckResult(BaseModel):
    input: str
    verdict: str  # LIKELY_REAL | LIKELY_FAKE | UNVERIFIED
    reasoning: str
    matched_coverage: list[MatchedCoverage]
    external_coverage: list[ExternalCoverage] = Field(default_factory=list)
    checked_against: int  # how many stored articles were searched for corroboration
    llm_used: bool
    note: str
    external_source: dict | None = None  # set when the input was a URL outside Xenom's own corpus and was fetched live:
    # {"domain": str, "title": str|None, "published_at": str|None, "author": str|None, "fetch_status": "ok"|"error", "error": str|None}
