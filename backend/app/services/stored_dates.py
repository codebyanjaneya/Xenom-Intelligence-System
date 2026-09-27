"""Explicit event dates from the event sentence itself (the same verbatim evidence sentence).

A date is returned only when the sentence states one unambiguously:
  * full dates ("15 September 2026", "September 15, 2026", "2026-09-15"), or
  * day + month without a year ("on 15 September"); the year is then taken from the article's publication
    date (the nearest occurrence not after it, at most 120 days earlier) and `year_inferred` is True.
Nothing else is a date: no publication-date fallback, no relative words ("yesterday"), no future or planned
dates (the sentence is rejected when it is future-tense or the date lies after publication), and no sentence
with more than one distinct date. Callers keep `publication_fallback` ordering when this returns None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_M = r"(?P<{n}>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_D = r"(?P<{n}>\d{{1,2}})(?:\s?(?:st|nd|rd|th))?"
_DMY = re.compile(rf"\b{_D.format(n='d')}\s+(?:of\s+)?{_M.format(n='m')},?\s+(?P<y>\d{{4}})\b", re.IGNORECASE)
_MDY = re.compile(rf"\b{_M.format(n='m')}\s+{_D.format(n='d')},?\s+(?P<y>\d{{4}})\b", re.IGNORECASE)
_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_DM = re.compile(rf"\b{_D.format(n='d')}\s+(?:of\s+)?{_M.format(n='m')}\b(?!\.?,?\s*\d{{4}})", re.IGNORECASE)
_MD = re.compile(rf"\b{_M.format(n='m')}\s+{_D.format(n='d')}\b(?!\d)(?!,?\s*\d{{4}})", re.IGNORECASE)
_FUTURE = re.compile(r"\b(will|shall|scheduled|upcoming|slated|set to|plans? to|to be (?:launched|held|unveiled|signed)|"
                     r"is expected|are expected|expected to)\b", re.IGNORECASE)
MAX_LOOKBACK_DAYS = 120
_META = re.compile(r"(?:published|updated|posted|last updated|modified)\s*(?:on|at|:|-)?\s*$", re.IGNORECASE)


def _is_meta(sentence: str, pos: int) -> bool:
    """A date right after Published/Updated/Posted is page metadata (the article date), never an event date."""
    return bool(_META.search(sentence[max(0, pos - 25):pos]))


@dataclass(frozen=True)
class EventDate:
    when: datetime          # midnight UTC of the stated day
    text: str               # the exact date text as written in the sentence
    year_inferred: bool


def _month(token: str) -> int | None:
    """Month number; lowercase 'may' is the modal verb, not the month."""
    if token == "may":
        return None
    return _MONTHS[token.lower().rstrip(".")]


def _mk(y: int, m: int | None, d: int) -> date | None:
    if m is None:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def extract_event_date(sentence: str, published_at: datetime | None) -> EventDate | None:
    if _FUTURE.search(sentence):
        return None
    found: dict[date, tuple[str, bool]] = {}
    mentions: set[int] = set()  # start offsets of every date-like expression, accepted or not
    for rx in (_DMY, _MDY):
        for m in rx.finditer(sentence):
            d = _mk(int(m["y"]), _month(m["m"]), int(m["d"]))
            if d and not _is_meta(sentence, m.start()):
                mentions.add(m.start())
                found.setdefault(d, (m.group(0), False))
    for m in _ISO.finditer(sentence):
        d = _mk(int(m["y"]), int(m["m"]), int(m["d"]))
        if d and not _is_meta(sentence, m.start()):
            mentions.add(m.start())
            found.setdefault(d, (m.group(0), False))
    pub = published_at.date() if published_at else None
    for rx in (_DM, _MD):
        for m in rx.finditer(sentence):
            if pub is None or _is_meta(sentence, m.start()):
                continue
            month, day = _month(m["m"]), int(m["d"])
            if month:
                mentions.add(m.start())
            cand = _mk(pub.year, month, day)
            if cand and cand > pub + timedelta(days=1):
                cand = _mk(pub.year - 1, month, day)
            if cand and 0 <= (pub - cand).days + 1 <= MAX_LOOKBACK_DAYS + 1:
                found.setdefault(cand, (m.group(0), True))
    if len(found) != 1 or len(mentions) != 1:
        return None                                    # none, or ambiguous
    (d, (text, inferred)), = found.items()
    if pub is not None and d > pub + timedelta(days=1):
        return None                                    # dated after publication: planned, not a happened event
    return EventDate(datetime(d.year, d.month, d.day, tzinfo=timezone.utc), text, inferred)
