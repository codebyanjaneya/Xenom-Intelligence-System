"""Structural quality checks run BEFORE relevance/scoring.

These never lower any threshold; they only reject records that are malformed
or obviously junk, and they say why (problem codes are stored in diagnostics).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

__all__ = ["validate_record"]

_MIN_TITLE_CHARS = 10
_MIN_TITLE_WORDS = 3
_MIN_CONTENT_CHARS = 25
_EARLIEST = datetime(2000, 1, 1, tzinfo=timezone.utc)
_SPAM = re.compile(
    r"\b(casino|betting tips|viagra|cialis|lottery winner|payday loan|click here to win|"
    r"buy followers|free crypto|adult dating|escort service|xxx)\b",
    re.I,
)


def validate_record(
    *,
    title: str | None,
    url: str | None,
    published_at: datetime | None,
    text: str | None,
    require_date: bool = True,
    now: datetime | None = None,
) -> list[str]:
    """Return problem codes; an empty list means structurally valid."""
    now = now or datetime.now(timezone.utc)
    problems: list[str] = []
    clean_title = (title or "").strip()

    if not clean_title:
        problems.append("EMPTY_TITLE")
    else:
        total_len = len(clean_title) + len((text or "").strip())
        # 2-word titles are fine when the record carries real content (e.g. "LinkedIn Workshop" + speaker/aim);
        # bare fragments such as "News", "Read More" or "#Dussehra" are not.
        if len(clean_title) < _MIN_TITLE_CHARS or (len(clean_title.split()) < _MIN_TITLE_WORDS and total_len < 60):
            problems.append("TITLE_TOO_SHORT")
        letters = [c for c in clean_title if c.isalpha()]
        if len(letters) >= 20 and sum(c.isupper() for c in letters) / len(letters) > 0.85:
            problems.append("ALL_CAPS_TITLE")
        if _SPAM.search(clean_title):
            problems.append("SPAM_PATTERN")

    if not (url or "").strip():
        problems.append("EMPTY_URL")
    else:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or "." not in parsed.hostname:
            problems.append("INVALID_URL")

    if published_at is None:
        if require_date:
            problems.append("MISSING_DATE")
    else:
        aware = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
        if aware > now + timedelta(days=1):
            problems.append("FUTURE_DATE")
        elif aware < _EARLIEST:
            problems.append("IMPLAUSIBLE_DATE")

    if len(clean_title) + len((text or "").strip()) < _MIN_CONTENT_CHARS:
        problems.append("CONTENT_TOO_SHORT")

    return problems
