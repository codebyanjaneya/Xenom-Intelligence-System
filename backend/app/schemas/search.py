from __future__ import annotations

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)
    search_type: Literal["web", "news"] = "web"
    freshness: str | None = Field(default=None, max_length=20)
    domains: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("query must not be blank")
        return value


class SearchResultOut(BaseModel):
    title: str
    url: str
    snippet: str
    domain: str
    discovered_at: datetime
    published_at: datetime | None
    provider: str
    relevance_score: float
    score_breakdown: dict[str, float]


class SearchResponse(BaseModel):
    query: str
    provider: str
    results: list[SearchResultOut]
    count: int
    rejected: dict[str, int] = Field(default_factory=dict)