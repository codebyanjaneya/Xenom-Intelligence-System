from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.schemas.domain import SignalRef

_TIER_SCORE = {"TIER_1": 1.0, "TIER_2": 0.75, "TIER_3": 0.5, "TIER_4": 0.25}
_ROUNDUP_WORDS = re.compile(r"\b(?:daily|weekly) roundup\b|\bnews and updates\b|\broundup\b", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class DigestCandidate:
    signal: SignalRef
    theme: str
    tier: str
    reliability: float
    title_summary_match: int
    score: float = 0.0


def _tokens(title: str | None) -> set[str]:
    return set(_TOKEN.findall((title or "").lower()))


def near_duplicate(left: DigestCandidate, right: DigestCandidate) -> bool:
    if left.signal.url == right.signal.url:
        return True
    a, b = _tokens(left.signal.title), _tokens(right.signal.title)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.8


def rank_candidate(candidate: DigestCandidate, now: datetime | None = None) -> DigestCandidate:
    now = now or datetime.now(timezone.utc)
    published = candidate.signal.published_at
    age_days = max(0.0, (now - published).total_seconds() / 86400) if published else 365.0
    freshness = math.exp(-age_days / 14.0)
    source_score = 0.7 * _TIER_SCORE.get(candidate.tier, 0.5) + 0.3 * max(0.0, min(1.0, candidate.reliability))
    roundup_penalty = 0.7 if _ROUNDUP_WORDS.search(candidate.signal.title or "") else 1.0
    score = (0.45 * candidate.title_summary_match + 0.35 * source_score + 0.20 * freshness) * roundup_penalty
    return DigestCandidate(**{**candidate.__dict__, "score": score})


def select_diverse(candidates: Iterable[DigestCandidate], limit: int, *, max_per_publisher: int = 2,
                   already_selected: list[DigestCandidate] | None = None) -> list[DigestCandidate]:
    ranked = sorted(candidates, key=lambda c: (-c.score, c.signal.published_at is None,
                                                -(c.signal.published_at.timestamp() if c.signal.published_at else 0),
                                                c.signal.url))
    selected = list(already_selected or [])
    publisher_counts: dict[str, int] = {}
    result: list[DigestCandidate] = []
    publishers = {c.signal.publisher or c.signal.source or "unknown" for c in ranked}
    target_publishers = min(3, limit, len(publishers))

    def add(candidate: DigestCandidate) -> None:
        publisher = candidate.signal.publisher or candidate.signal.source or "unknown"
        publisher_counts[publisher] = publisher_counts.get(publisher, 0) + 1
        result.append(candidate)

    for require_new_publisher in (True, False):
        for candidate in ranked:
            if len(result) >= limit:
                break
            if any(near_duplicate(candidate, prior) for prior in [*selected, *result]):
                continue
            publisher = candidate.signal.publisher or candidate.signal.source or "unknown"
            if publisher_counts.get(publisher, 0) >= max_per_publisher:
                continue
            if require_new_publisher:
                chosen_publishers = {c.signal.publisher or c.signal.source or "unknown" for c in result}
                if publisher in chosen_publishers or len(chosen_publishers) >= target_publishers:
                    continue
            add(candidate)
    # Diversity is a soft preference, not a hard cutoff: if the cap left the section under-filled while
    # strong candidates remain (e.g. one publisher dominates the corpus), fill the rest by rank, skipping
    # only near-duplicates already selected.
    if len(result) < limit:
        for candidate in ranked:
            if len(result) >= limit:
                break
            if any(near_duplicate(candidate, prior) for prior in [*selected, *result]):
                continue
            add(candidate)
    return result
