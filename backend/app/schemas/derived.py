"""Derived intelligence objects. Every object carries its full trace:
article_ids -> evidence (verbatim stored spans) -> event_keys / entity_keys -> the derived object."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.intelligence import Evidence


class Insight(BaseModel):
    insight_id: str
    status: Literal["draft"] = "draft"  # rule-generated; a human reviews before anything is published
    insight_type: Literal["multi_source_event", "activity_cluster", "new_activity_type", "co_involvement"]
    rule_id: str
    client_key: str
    client_name: str
    title: str
    explanation: str  # why this was derived: rule + the numbers that triggered it
    entity_keys: list[str]
    event_keys: list[str]
    article_ids: list[str]
    evidence: list[Evidence] = Field(min_length=1)
    strength: float = Field(ge=0, le=1)
    recency_days: int | None  # days between the supporting events and `as_of`
    basis: dict
    unavailable: list[str]  # fields of the stored Insight model that cannot be derived from stored data
    limitations: list[str]  # what this insight does NOT establish


class Opportunity(BaseModel):
    opportunity_id: str
    client_key: str
    client_name: str
    title: str
    rationale: str
    rule_id: str
    interpretation: str  # explicit: rule-based reading of a stated event, not a fact about the client's needs
    status: Literal["POTENTIAL", "QUALIFIED", "DISMISSED"] = "POTENTIAL"
    capability_match: Literal["VERIFIED", "UNVERIFIED"] = "UNVERIFIED"
    capability: str | None = None  # no verified Xenom capability data is stored, so never set here
    opportunity_score: float = Field(ge=0, le=1)
    score_components: dict
    recommended_action: str  # rule template; always starts with verifying the cited event
    action_basis: str  # the rule id and event the action derives from
    review_status: Literal["needs_review"] = "needs_review"  # a human decides; nothing is auto-published
    insight_ids: list[str]
    event_keys: list[str]
    entity_keys: list[str]
    article_ids: list[str]
    evidence: list[Evidence] = Field(min_length=1)


class Alert(BaseModel):
    alert_id: str
    rule_id: str
    severity: Literal["info", "warning", "critical"]
    client_key: str
    client_name: str
    title: str
    body: str
    triggered_because: dict
    event_keys: list[str]
    insight_ids: list[str]
    article_ids: list[str]
    evidence: list[Evidence] = Field(min_length=1)
    as_of: datetime | None


class BriefingEvent(BaseModel):
    event_key: str
    event_type: str
    title: str
    when: datetime | None
    when_source: str  # event_time (stated in the article) | publication_fallback | discovery_fallback
    publishers: list[str]
    article_ids: list[str]
    entity_keys: list[str]
    evidence: list[Evidence]


class Briefing(BaseModel):
    scope: dict  # {"client": ...} or {"query": ...}
    as_of: datetime | None
    as_of_basis: str
    generated_from: dict
    summary: str
    key_events: list[BriefingEvent]
    entities: list[dict]
    timeline: list[dict]
    sources: list[dict]
    insights: list[Insight]
    opportunities: list[Opportunity]
    alerts: list[Alert]
    limitations: list[str]
