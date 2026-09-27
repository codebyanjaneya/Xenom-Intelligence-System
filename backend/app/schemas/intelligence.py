from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class EntityType(str, Enum):
    ORG = "ORG"
    PERSON = "PERSON"
    PRODUCT = "PRODUCT"
    TECHNOLOGY = "TECHNOLOGY"
    INDUSTRY = "INDUSTRY"


class Evidence(BaseModel):
    source_url: str
    provider: str
    title: str
    snippet: str
    published_at: datetime | None
    discovered_at: datetime
    relevance_score: float
    page_title: str | None = None
    content_span: str | None = None


class Entity(BaseModel):
    canonical_name: str
    canonical_key: str
    entity_type: EntityType
    mention: str
    mentions: list[str] = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)
    verification_status: str = "unverified"


class Event(BaseModel):
    event_key: str
    title: str
    event_type: str
    evidence: list[Evidence] = Field(min_length=1)
    event_time: datetime | None = None
    event_time_precision: Literal["exact", "day", "unknown"] = "unknown"
    event_time_source: Literal["article_text", "unknown"] = "unknown"
    verification_status: str = "unverified"


class EntityEventRelationship(BaseModel):
    entity_key: str
    event_key: str
    relationship_type: str
    evidence: list[Evidence] = Field(min_length=1)


class TemporalRelationship(BaseModel):
    earlier_event_key: str
    later_event_key: str
    relationship_type: Literal["BEFORE", "AFTER", "SAME_TIME"]
    evidence: list[Evidence] = Field(min_length=1)


class TimelineEntry(BaseModel):
    event_key: str
    position: int
    ordering_time: datetime
    ordering_time_source: Literal["event_time", "publication_fallback", "discovery_fallback"]


class IntelligenceResponse(BaseModel):
    query: str
    provider: str
    source_result_count: int
    entities: list[Entity]
    events: list[Event]
    relationships: list[EntityEventRelationship]
    timeline: list[TimelineEntry]
    temporal_relationships: list[TemporalRelationship]


class IntelligenceRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500, pattern=r"^[^\x00]+$")
    limit: int = Field(default=10, ge=1, le=50)