"""Deterministic event extraction + conservative cross-article merging from STORED article text.

No LLM, no fixtures. An event exists only when ONE stored sentence has
  (1) an entity linked to that article in `entity_mentions`, mentioned BEFORE
  (2) a strong action construction (table below) within a short distance, and
  (3) that construction is not inside a quotation, an attribution line ("Name | Title"), or markup.
The sentence is the event title and its only evidence (a verbatim slice with offsets). Nothing is inferred
beyond it: no dates, no participants other than entities that precede the cue in the sentence, no outcomes.
The cue table detects that an action is *stated*, not that it is true.

`merge_occurrences` groups the same underlying event reported by several articles. It is deliberately
conservative: same event type, same entity set, near-identical normalised headline (token overlap >= 0.9 and
Jaccard >= 0.6, at least 5 tokens each), and publication dates within 14 days. False merges are worse than leftover duplicates.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from app.services.article_evidence import FIELDS, MAX_QUOTE
from app.services.stored_dates import _FUTURE as FUTURE_TENSE
from app.services.stored_dates import EventDate, extract_event_date

# (event_type, regex, max characters between the entity mention and the cue). Priority order: first row wins.
_OBJ = r"(?:\S+\s+){0,6}?"
CUES: tuple[tuple[str, str, int], ...] = (
    ("acquisition", r"acquir(?:e|es|ed|ing)\b|acquisition of\b|takeover of\b|merges? with\b|merged with\b|buys\b|bought\b", 120),
    ("funding", r"raise[sd]?\s+" + _OBJ + r"(?:funding|capital|round|crore|million|billion|\$|₹|rs\.?\s?\d)|"
                r"(?:secures?|secured|closes?|closed)\s+" + _OBJ + r"(?:funding|round)|series [a-e]\b", 80),
    ("investment", r"invests?\s+(?:\S+\s+){0,4}?in\b|invested\s+(?:\S+\s+){0,4}?in\b|to invest\b|investment of\b|investment in\b", 40),
    ("partnership", r"partner(?:s|ed|ing)?\s+(?:with|up)\b|partnership\s+(?:with|between)\b|collaborat\w*\s+(?:with|between|on)\b|"
                    r"joint venture\b|\bjv\b|tie-?ups?\b|alliance\s+(?:with|between)\b", 80),
    ("agreement", r"\bmou\b|agreement\b|\bpact\b|(?:signs?|signed|signing)\s+" + _OBJ + r"(?:mou|agreement|pact|deal|contract)\b", 60),
    ("contract", r"contract\b|order win\b|wins?\s+(?:an?\s+)?orders?\b|bags?\s+(?:an?\s+)?orders?\b|awarded\b", 80),
    ("appointment", r"appoint(?:s|ed|ing|ment)\b|takes? (?:the )?helm\b|names?\s+\S+(?:\s+\S+)?\s+as\b|joins? as\b", 80),
    ("hiring", r"hir(?:es|ed|ing)\s+(?:\S+\s+){0,4}?(?:engineers?|staff|employees|workers|talent|team|people)\b|"
               r"(?:to\s+)?add[s]?\s+(?:\S+\s+){0,4}?(?:jobs|positions|headcount)\b|"
               r"recruit(?:s|ed|ing|ment)\b|job\s+(?:fair|openings?)\b|hiring\s+(?:spree|drive)\b", 80),
    ("procurement", r"(?:issues?|floats?|invites?)\s+(?:\S+\s+){0,4}?tender\b|\btender\b|"
                    r"request for proposals?\b|\brfp\b|procure(?:s|d|ment)\b|purchase order\b", 80),
    ("government_initiative", r"(?:government|ministry|centre|state)\s+(?:\S+\s+){0,4}?(?:scheme|policy|initiative|mission|programme|program)\b|"
                              r"\bpli\s+scheme\b|\bsubsid(?:y|ies)\b|"
                              r"(?:approves?|approved|clears?|cleared)\s+(?:\S+\s+){0,4}?(?:scheme|policy|subsidy|mission)\b", 100),
    ("market_entry", r"enters?\s+(?:the\s+)?(?:\S+\s+){0,4}?market\b|foray(?:s|ed|ing)?\s+into\b|debuts?\s+in\b|"
                     r"(?:to\s+)?focus(?:es|ed|ing)?\s+on\b|bets?\s+on\b|eyes\b|diversif(?:y|ies|ied|ying)\s+into\b", 80),
    ("competitor_weakness", r"(?:recalls?|recalled)\b|(?:profit|sales|revenue|market share)\s+(?:falls?|fell|declin\w*|drops?|dropped|slumps?|slumped)\b|"
                            r"(?:posts?|reports?)\s+(?:\S+\s+){0,4}?loss(?:es)?\b|"
                            r"(?:faces?|hit by|files?\s+for)\s+(?:\S+\s+){0,4}?(?:lawsuit|investigation|bankruptcy|insolvency)\b|"
                            r"lays?\s+off\b|layoffs?\b|shuts?\s+down\b|plant closure\b", 100),
    ("new_customer_segment", r"target(?:s|ed|ing)?\s+(?:\S+\s+){0,4}?(?:segment|customers?)\b|"
                            r"expand(?:s|ed|ing)?\s+(?:\S+\s+){0,4}?(?:customer base|clientele)\b|"
                            r"offer(?:s|ed|ing)?\s+(?:\S+\s+){0,4}?(?:solutions?|services?)\s+for\b", 80),
    ("research_breakthrough", r"\bbreakthrough\b|discover(?:s|ed|y)\b|"
                             r"(?:publish(?:es|ed)|peer[- ]reviewed)\s+(?:\S+\s+){0,4}?(?:paper|study|research)\b|"
                             r"achieves?\s+(?:\S+\s+){0,4}?(?:state-of-the-art|sota)\b", 100),
    ("launch", r"launch(?:es|ed|ing)?\b|unveil(?:s|ed|ing)?\b|introduc(?:es|ed|ing)\b|rolls? out\b|rolled out\b|debut(?:s|ed)?\b", 120),
    ("expansion", r"expan(?:ds|ded|ding)\s+(?:(?:its|their|the|a|an|new)\s+)?(?:\S+\s+){0,3}?(?:plant|facility|facilities|factory|factories|capacity|footprint|presence|operations|network|centre|center|showroom|outlet|office|campus|dealership|unit)\b|"
                  r"inaugurat(?:es|ed|ing)\s+(?:\S+\s+){0,5}?(?:plant|facility|facilities|factory|factories|capacity|footprint|presence|operations|network|centre|center|showroom|outlet|office|campus|dealership|unit)\b|"
                  r"(?:opens?|opened)\s+(?:(?:an?|its|new)\s+)?(?:\S+\s+){0,3}?(?:plant|facility|facilities|factory|factories|capacity|footprint|presence|operations|network|centre|center|showroom|outlet|office|campus|dealership|unit)\b|"
                  r"new (?:plant|facility|factory|centre|center)\b", 80),
    ("announcement", r"announc(?:es|ed|ing)\b", 40),
)
_CUE_RES = tuple((t, re.compile(r"\b(?:" + p + r")", re.IGNORECASE), gap) for t, p, gap in CUES)
_SENTENCE_END = ".!?\n"
MAX_EVENTS_PER_ARTICLE = 3


@dataclass(frozen=True)
class EventCandidate:
    event_key: str
    event_type: str
    field: str
    start: int
    end: int
    quote: str
    entity_keys: tuple  # entities that precede the cue within its distance limit, sorted
    event_date: EventDate | None = None  # explicit date stated in the same sentence, else None


def sentences(text: str):
    """Yield (start, end) of trimmed sentences; offsets index into `text`. Sentences over MAX_QUOTE are skipped."""
    start = 0
    for i, ch in enumerate(text + "\n"):
        if ch in _SENTENCE_END:
            lo, hi = start, i + (1 if ch != "\n" else 0)
            while lo < hi and text[lo].isspace():
                lo += 1
            while hi > lo and text[hi - 1].isspace():
                hi -= 1
            if 0 < hi - lo <= MAX_QUOTE:
                yield lo, hi
            start = i + 1


_BYLINE = re.compile(r"\b(?:published|updated|posted)\s*(?::|on)\b|\bread time\b", re.IGNORECASE)


def _skippable(sentence: str) -> bool:
    """Markup residue or an attribution line: never an event."""
    return "<" in sentence or ">" in sentence or " | " in sentence or "&lt;" in sentence or "&gt;" in sentence         or "class=" in sentence or sentence.lower().startswith("read this article in")         or bool(_BYLINE.search(sentence))


def mask_quotes(sentence: str) -> str:
    """Blank out text inside double quotes (same length, offsets preserved). An unmatched opening quote masks the
    rest of the sentence, because the quotation continues past the sentence boundary."""
    out, inside = [], False
    for ch in sentence:
        if ch == '"':
            inside = not inside
            out.append(" ")
        elif ch == "“":
            inside = True
            out.append(" ")
        elif ch == "”":
            inside = False
            out.append(" ")
        else:
            out.append(" " if inside else ch)
    return "".join(out)


def event_type_for(sentence: str) -> str | None:
    """Cue detection only (no entity association); quotations are ignored."""
    masked = mask_quotes(sentence)
    for event_type, rx, _gap in _CUE_RES:
        if rx.search(masked):
            return event_type
    return None


def _term_re(terms) -> re.Pattern | None:
    terms = sorted({t.strip() for t in terms if t and t.strip()}, key=lambda t: (-len(t), t.lower()))
    if not terms:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b", re.IGNORECASE)


_PAST_YEAR = re.compile(r"^.{0,30}?\b(?:in|since|during|back in)\s+((?:19|20)\d\d)\b")


def _is_background(masked: str, cue: re.Match, published_year: int | None) -> bool:
    """The cue is followed by 'in <year>' with a year earlier than the article's publication year: background."""
    if published_year is None:
        return False
    m = _PAST_YEAR.match(masked[cue.end():cue.end() + 60])
    return bool(m) and int(m.group(1)) < published_year


def _associate(sentence: str, matchers: dict, published_year: int | None = None) -> tuple[str | None, tuple]:
    """First cue type (priority order) whose cue is preceded, within its distance limit, by a registered entity."""
    masked = mask_quotes(sentence)
    for event_type, rx, gap in _CUE_RES:
        for cue in rx.finditer(masked):
            if _is_background(masked, cue, published_year):
                continue
            keys = tuple(
                k for k, ent in matchers.items()
                if ent and any(0 <= cue.start() - m.end() <= gap for m in ent.finditer(masked))
            )
            if keys:
                return event_type, keys
    return None, ()


def extract_events(article_id, fields: dict, entities: dict, published_year: int | None = None,
                   published_at=None) -> list[EventCandidate]:
    """entities: canonical_key -> iterable of surface terms (mention text, canonical name) for THIS article."""
    matchers = {k: _term_re(v) for k, v in sorted(entities.items())}
    if published_year is None and published_at is not None:
        published_year = published_at.year
    out: list[EventCandidate] = []
    seen: set = set()
    for name in FIELDS:
        text = fields.get(name) or ""
        for lo, hi in sentences(text):
            quote = text[lo:hi]
            if quote in seen or _skippable(quote) or FUTURE_TENSE.search(quote):  # planned/future is not a stated event
                continue
            event_type, keys = _associate(quote, matchers, published_year)
            if not event_type:
                continue
            seen.add(quote)
            key = "ev-" + hashlib.sha1(f"{article_id}|{name}|{lo}|{hi}|{event_type}".encode()).hexdigest()[:12]
            out.append(EventCandidate(key, event_type, name, lo, hi, quote, keys,
                                      extract_event_date(quote, published_at)))
            if len(out) >= MAX_EVENTS_PER_ARTICLE:
                return out
    return out


# ---- cross-article merging ------------------------------------------------------------------------------------

_STOP = frozenset("a an the of to in on at for and or with by is are was were its it this that as from".split())
_PUBLISHER_SUFFIX = re.compile(r"\s+[-–|]\s+[^-–|]{1,60}$")
MERGE_JACCARD = 0.6
MERGE_OVERLAP = 0.9
MERGE_MAX_DAYS = 14
MIN_MERGE_TOKENS = 5


def _stem(token: str) -> str:
    """Tiny deterministic suffix stripper so launch/launches/launched compare equal."""
    if len(token) > 4:
        for suffix in ("ing", "ed", "es", "s"):
            if token.endswith(suffix):
                return token[: -len(suffix)]
    return token


def headline_tokens(quote: str) -> frozenset:
    """Lower-cased word set with a trailing ' - Publisher' removed and stopwords dropped."""
    text = _PUBLISHER_SUFFIX.sub("", quote.strip())
    return frozenset(_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP)


@dataclass(frozen=True)
class Occurrence:
    candidate: EventCandidate
    article_id: object
    published_at: datetime | None


def _same_event(a: Occurrence, b: Occurrence) -> bool:
    ca, cb = a.candidate, b.candidate
    if ca.event_type != cb.event_type or ca.entity_keys != cb.entity_keys:
        return False
    if a.published_at is None or b.published_at is None or abs((a.published_at - b.published_at).days) > MERGE_MAX_DAYS:
        return False
    ta, tb = headline_tokens(ca.quote), headline_tokens(cb.quote)
    if len(ta) < MIN_MERGE_TOKENS or len(tb) < MIN_MERGE_TOKENS:
        return False
    shared = len(ta & tb)
    return shared / min(len(ta), len(tb)) >= MERGE_OVERLAP and shared / len(ta | tb) >= MERGE_JACCARD


def _primary_rank(o: Occurrence):
    """Headline-like first: title field, then shortest sentence, then stable tie-breaks."""
    c = o.candidate
    return (c.field != "title", len(c.quote), c.event_key)


def merge_occurrences(occurrences: list[Occurrence]) -> list[list[Occurrence]]:
    """Group occurrences (given in search-rank order) into events. Each occurrence joins the first group whose
    first member it matches; every group is returned primary-first, groups ordered by first appearance."""
    groups: list[list[Occurrence]] = []
    for occ in occurrences:
        for g in groups:
            if _same_event(g[0], occ):
                g.append(occ)
                break
        else:
            groups.append([occ])
    return [sorted(g, key=_primary_rank) for g in groups]
