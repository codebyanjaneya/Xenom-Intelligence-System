"""Freshness scoring for intelligence pipeline.

Freshness is ONE COMPONENT of the total 0-100 relevance score.
It boosts otherwise relevant information but cannot make irrelevant articles pass the quality gate.

Freshness component is configurable and time-based:
- 0-6 hours: full freshness points
- 6-24 hours: reduced freshness points
- 1-3 days: lower freshness points
- >3 days: minimal/zero freshness points
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

__all__ = ["FreshnessConfig", "calculate_freshness_score", "get_freshness_label"]


class FreshnessConfig:
    """Configurable freshness scoring thresholds."""

    # Freshness component maximum points (out of 100 total)
    MAX_FRESHNESS_POINTS = 10

    # Time thresholds for freshness calculation
    FRESHNESS_WINDOWS = [
        # (hours_ago, points)
        (6, 10),  # 0-6 hours: full 10 points
        (24, 7),  # 6-24 hours: 7 points
        (72, 3),  # 1-3 days: 3 points
        (float("inf"), 0),  # >3 days: 0 points
    ]

    @classmethod
    def get_points_for_age(cls, published_at: datetime | None) -> int:
        """Calculate freshness points based on article age.

        Args:
            published_at: Publication timestamp. If None, returns 0.

        Returns:
            Freshness points (0-10).
        """
        if not published_at:
            return 0

        try:
            now = datetime.now(timezone.utc)
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)

            age = now - published_at
            hours_ago = age.total_seconds() / 3600

            # Freshness windows (inclusive on upper bound for boundary cases)
            # 0-6 hours: 10 points
            # 6-24 hours: 7 points
            # 24-72 hours: 3 points
            # 72+ hours: 0 points
            if hours_ago <= 6:
                return 10
            elif hours_ago <= 24:
                return 7
            elif hours_ago <= 72:
                return 3
            else:
                return 0
        except Exception:
            return 0


def calculate_freshness_score(published_at: datetime | None) -> int:
    """Calculate freshness component of relevance score.

    Freshness is ONE of the 10 components that make up the total 0-100 score.
    It boosts otherwise relevant articles but does not overcome low entity relevance.

    Args:
        published_at: Publication timestamp.

    Returns:
        Freshness points (0-10, out of 100 total score).
    """
    return FreshnessConfig.get_points_for_age(published_at)


def get_freshness_label(published_at: datetime | None) -> str | None:
    """Get human-readable freshness label for display.

    Examples: "12 min ago", "3 hrs ago", "Yesterday", "2 days ago".

    Args:
        published_at: Publication timestamp.

    Returns:
        Human-readable label or None if date is unknown.
    """
    if not published_at:
        return None

    try:
        now = datetime.now(timezone.utc)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        age = now - published_at
        minutes_ago = age.total_seconds() / 60
        hours_ago = minutes_ago / 60
        days_ago = hours_ago / 24

        if minutes_ago < 1:
            return "Just now"
        elif minutes_ago < 60:
            m = int(minutes_ago)
            return f"{m} min ago" if m == 1 else f"{m} mins ago"
        elif hours_ago < 24:
            h = int(hours_ago)
            return f"{h} hr ago" if h == 1 else f"{h} hrs ago"
        elif days_ago < 2:
            return "Yesterday"
        elif days_ago < 7:
            d = int(days_ago)
            return f"{d} days ago"
        else:
            return published_at.strftime("%b %d")
    except Exception:
        return None
