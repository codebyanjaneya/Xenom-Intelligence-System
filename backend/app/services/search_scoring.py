from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.providers.search import SearchResult

_STOP_WORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
_OFFICIAL_SUFFIXES = {".gov", ".gov.in", ".edu"}
_KNOWN_NEWS = {"reuters.com", "apnews.com", "bbc.com", "nytimes.com", "wsj.com"}
_KNOWN_TECH = {"arxiv.org", "ieee.org", "nature.com", "technologyreview.com"}


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    entity_match: float
    topic_match: float
    recency: float
    source_quality: float
    content_signal: float


def score_result(query: str, result: SearchResult, now: datetime | None = None) -> ScoreBreakdown:
    now = now or datetime.now(timezone.utc)
    terms = [term for term in re.findall(r"[\w]+", query.lower()) if term not in _STOP_WORDS]
    title = result.title.lower()
    snippet = result.snippet.lower()
    domain = result.domain.lower()
    entity = 30.0 if terms and any(term in title or term in domain for term in terms) else 0.0
    matched = sum(1 for term in terms if term in title or term in snippet or term in domain)
    topic = 35.0 * matched / len(terms) if terms else 0.0
    recency = _recency_score(result.published_at, now)
    source = _source_quality(domain)
    content = 5.0 if result.title.strip() and result.snippet.strip() else 0.0
    total = entity + topic + recency + source + content
    return ScoreBreakdown(total, entity, topic, recency, source, content)


def _recency_score(published_at: datetime | None, now: datetime) -> float:
    if published_at is None:
        return 0.0
    published = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600)
    if age_hours <= 24:
        return 20.0
    if age_hours <= 24 * 7:
        return 15.0
    if age_hours <= 24 * 30:
        return 8.0
    return 2.0


def _source_quality(domain: str) -> float:
    if any(domain == suffix.lstrip(".") or domain.endswith(suffix) for suffix in _OFFICIAL_SUFFIXES):
        return 10.0
    if domain in _KNOWN_NEWS:
        return 9.0
    if domain in _KNOWN_TECH:
        return 8.0
    return 3.0