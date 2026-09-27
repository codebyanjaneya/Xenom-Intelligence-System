"""Source registry schemas.

Source types and tiers are validated as enums. URLs are validated for SYNTAX
only — no network requests are made (a syntactically valid URL does not imply
the site exists).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceType(str, Enum):
    RSS = "RSS"
    HTML = "HTML"
    OFFICIAL_NEWSROOM = "OFFICIAL_NEWSROOM"
    PRESS_RELEASE = "PRESS_RELEASE"
    GOVERNMENT = "GOVERNMENT"
    REGULATORY = "REGULATORY"
    OTHER_PUBLIC = "OTHER_PUBLIC"
    RESEARCH_API = "RESEARCH_API"
    MODEL_HUB = "MODEL_HUB"


class SourceTier(str, Enum):
    # TIER_1 = primary/official; TIER_2 = strong reputable secondary;
    # TIER_3 = lower-confidence/general (treat with more caution).
    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"
    TIER_4 = "TIER_4"  # forums / user-generated content: discovery signal only, never evidence


class SourceClass(str, Enum):
    official = "official"
    trusted_news = "trusted_news"
    news = "news"
    directory = "directory"
    reference = "reference"
    research = "research"
    forum = "forum"


class FetchConfig(BaseModel):
    """How a manager-defined source is fetched. The same adapters and the same pipeline as the code registry."""

    model_config = ConfigDict(extra="forbid")

    fetch_mode: Literal["rss", "seed_article", "link_listing", "table_rows", "arxiv_api", "huggingface_hub", "openalex_api"]
    link_pattern: str | None = Field(default=None, max_length=300)
    render: bool = False
    require_date: bool = True
    source_kind: Literal["official", "directory", "reference", "research"] | None = None
    query: str | None = Field(default=None, max_length=300)  # research APIs: the search expression
    max_results: int = Field(default=25, ge=1, le=100)
    managed: bool = True  # set by the admin API: registry sync must not overwrite manager edits

    @field_validator("link_pattern")
    @classmethod
    def _valid_regex(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                re.compile(v)
            except re.error as exc:
                raise ValueError(f"invalid regular expression: {exc}")
        return v


_MAX_NAME = 200
_MAX_URL = 2048


def _validate_url_syntax(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must be a valid http(s) URL")
    return value.strip()


class SourceCreate(BaseModel):
    name: str
    url: str
    source_type: SourceType
    tier: SourceTier
    feed_url: str | None = None
    fixture_path: str | None = None
    is_active: bool = True
    source_class: SourceClass | None = None
    reliability_score: float | None = Field(default=None, ge=0, le=1)
    priority: int = Field(default=0, ge=0, le=100)
    config: FetchConfig | None = None
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > _MAX_NAME:
            raise ValueError(f"name must be 1..{_MAX_NAME} characters")
        return v

    @field_validator("url")
    @classmethod
    def _url_ok(cls, v: str) -> str:
        if len(v) > _MAX_URL:
            raise ValueError("url too long")
        return _validate_url_syntax(v)

    @field_validator("feed_url")
    @classmethod
    def _feed_url_ok(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if len(v) > _MAX_URL:
            raise ValueError("feed_url too long")
        return _validate_url_syntax(v)


class SourceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    source_type: SourceType | None = None
    tier: SourceTier | None = None
    feed_url: str | None = None
    fixture_path: str | None = None
    is_active: bool | None = None
    source_class: SourceClass | None = None
    reliability_score: float | None = Field(default=None, ge=0, le=1)
    priority: int | None = Field(default=None, ge=0, le=100)
    config: FetchConfig | None = None
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v or len(v) > _MAX_NAME:
            raise ValueError(f"name must be 1..{_MAX_NAME} characters")
        return v

    @field_validator("url", "feed_url")
    @classmethod
    def _urls_ok(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if len(v) > _MAX_URL:
            raise ValueError("url too long")
        return _validate_url_syntax(v)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    canonical_url: str
    feed_url: str | None
    fixture_path: str | None
    source_type: str | None
    tier: str
    is_active: bool
    source_class: str | None = None
    reliability_score: float | None = None
    priority: int = 0
    config: dict | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class SourceHealthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    checked_at: datetime
    status: str
    latency_ms: int | None
    error_message: str | None
    created_at: datetime


class SourceHealthResponse(BaseModel):
    source_id: uuid.UUID
    latest: SourceHealthOut | None
