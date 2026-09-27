"""Client-specific relevance engine.

The strict entity matcher proposes candidate clients. This module decides, per
client and with an explainable evidence trail, whether an article is actually
ABOUT that client. A registered source, a broad keyword or a generic industry
term is never enough on its own.

Evidence categories (weights in ``WEIGHTS``):

    strong name        unambiguous entity name (title > repeated in body > once in body)
    acronym/ambiguous  ACE / JCB / VIPS / ISM / "Vikas Group": counts ONLY next to a context keyword
    compound rule      e.g. India + semiconductor + policy for the India Semiconductor Mission
    executive          named executive, only with entity or context evidence
    product, industry, context, location, topic   support signals, only with entity evidence
    source trust       official domain / trusted publisher, only with entity evidence
    negatives          soft (subtract) and hard (reject unless a strong name is in the title)

Decision = entity evidence present AND total >= ACCEPT_THRESHOLD AND no hard negative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.config.entity_config import ENTITY_CONFIGS, EntityConfig

__all__ = [
    "ACCEPT_THRESHOLD",
    "RelevanceDecision",
    "evaluate_relevance",
    "evaluate_all_clients",
    "publisher_domain",
]

ACCEPT_THRESHOLD = 45
MIN_ENTITY_EVIDENCE = 25

WEIGHTS = {
    "strong_title": 50,
    "strong_body_repeated": 40,   # 3+ mentions
    "strong_body_once": 35,
    "acronym_context_title": 40,
    "acronym_context_body": 28,
    "executive_with_context": 25,
    "executive_bonus": 12,
    "product": 8,
    "industry_each": 4,
    "industry_cap": 8,
    "context_each": 3,
    "context_cap": 9,
    "location": 4,
    "topic_each": 3,
    "topic_cap": 9,
    "soft_negative": 25,
}


@dataclass
class Evidence:
    category: str
    term: str
    where: str  # title | body | domain
    points: int

    def as_dict(self) -> dict:
        return {"category": self.category, "term": self.term, "where": self.where, "points": self.points}


@dataclass
class RelevanceDecision:
    entity_key: str
    decision: str  # accepted | rejected
    relevance_score: float  # 0..1
    entity_points: int = 0
    matched_terms: list[str] = field(default_factory=list)
    matched_topics: list[str] = field(default_factory=list)
    source_type: str = "news"
    source_domain: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    negatives_hit: list[str] = field(default_factory=list)
    rejection_reason: str | None = None
    product_hit: bool = False
    industry_hits: int = 0
    context_hits: int = 0

    @property
    def accepted(self) -> bool:
        return self.decision == "accepted"

    def as_dict(self) -> dict:
        return {
            "entity": self.entity_key,
            "decision": self.decision,
            "relevance_score": round(self.relevance_score, 3),
            "matched_terms": self.matched_terms,
            "matched_topics": self.matched_topics,
            "source_type": self.source_type,
            "source_domain": self.source_domain,
            "negatives_hit": self.negatives_hit,
            "rejection_reason": self.rejection_reason,
            "evidence": [e.as_dict() for e in self.evidence],
        }


def publisher_domain(url_or_domain: str | None) -> str:
    """Bare lower-case host without a leading ``www.``."""
    if not url_or_domain:
        return ""
    value = url_or_domain.strip().lower()
    host = urlparse(value if "//" in value else f"//{value}").hostname or ""
    return host[4:] if host.startswith("www.") else host


def _domain_matches(domain: str, candidates: list[str]) -> bool:
    if not domain:
        return False
    for cand in candidates:
        cand = publisher_domain(cand)
        if cand and (domain == cand or domain.endswith("." + cand)):
            return True
    return False


def _pattern(term: str, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", flags)


def _count(text: str, term: str, case_sensitive: bool = False) -> int:
    return len(_pattern(term, case_sensitive).findall(text)) if text else 0


def _first_hit(text: str, terms: list[str], case_sensitive: bool = False) -> str | None:
    for term in terms:
        if _count(text, term, case_sensitive):
            return term
    return None


def _all_hits(text: str, terms: list[str]) -> list[str]:
    return [t for t in terms if _count(text, t)]


def _source_type(domain: str, cfg: EntityConfig, source_kind: str | None, owned: bool) -> str:
    """``owned`` = the source was registered for THIS client (a VIPS notice page is not official for JCB).
    A source registered with source_kind="research" (papers, labs, institutions) yields the "research" class; such
    articles still have to pass the client-relevance gate before they are accepted."""
    if source_kind == "research":
        return "research"
    if source_kind == "technology":
        return "technology"
    if _domain_matches(domain, cfg.official_domains):
        return "official"
    if owned and source_kind in {"official", "directory", "reference"}:
        return source_kind
    if _domain_matches(domain, cfg.trusted_sources):
        return "trusted_news"
    return "news"


def evaluate_relevance(
    entity_key: str,
    *,
    title: str | None,
    body: str | None = None,
    source_domain: str | None = None,
    source_kind: str | None = None,
    source_client: str | None = None,
    config: EntityConfig | None = None,
) -> RelevanceDecision:
    cfg = config or ENTITY_CONFIGS[entity_key]
    title = title or ""
    body = body or ""
    full = f"{title} {body}"
    domain = publisher_domain(source_domain)
    src_type = _source_type(domain, cfg, source_kind, owned=source_client == entity_key)
    ev: list[Evidence] = []
    matched_terms: list[str] = []

    def add(category: str, term: str, where: str, points: int) -> None:
        ev.append(Evidence(category, term, where, points))
        if term and term not in matched_terms and category in {
            "strong_name", "acronym_with_context", "ambiguous_name_with_context", "compound_rule", "executive", "official_source",
        }:
            matched_terms.append(term)

    # ---- context keywords (needed by acronym/ambiguous/executive logic) ----
    context_hits = _all_hits(full, cfg.context_keywords)

    # ---- hard negatives ----------------------------------------------------
    strong_in_title = _first_hit(title, cfg.strong_keywords)
    hard_hits = _all_hits(full, cfg.hard_negative_keywords)
    if hard_hits and not strong_in_title:
        return RelevanceDecision(
            entity_key, "rejected", 0.0, source_type=src_type, source_domain=domain,
            negatives_hit=hard_hits, rejection_reason="HARD_NEGATIVE",
            evidence=[Evidence("hard_negative", h, "text", 0) for h in hard_hits],
        )

    # ---- entity evidence: take the strongest single path -------------------
    candidates: list[tuple[int, Evidence]] = []

    for term in cfg.strong_keywords:
        n_title, n_body = _count(title, term), _count(body, term)
        if n_title:
            candidates.append((WEIGHTS["strong_title"], Evidence("strong_name", term, "title", WEIGHTS["strong_title"])))
        elif n_body >= 3:
            candidates.append((WEIGHTS["strong_body_repeated"], Evidence("strong_name", term, "body", WEIGHTS["strong_body_repeated"])))
        elif n_body:
            candidates.append((WEIGHTS["strong_body_once"], Evidence("strong_name", term, "body", WEIGHTS["strong_body_once"])))

    weak_no_context: list[str] = []
    for term in cfg.case_sensitive_acronyms:
        in_title, in_body = _count(title, term, True), _count(body, term, True)
        if in_title or in_body:
            if context_hits:
                where = "title" if in_title else "body"
                pts = WEIGHTS["acronym_context_title" if in_title else "acronym_context_body"]
                candidates.append((pts, Evidence("acronym_with_context", term, where, pts)))
            else:
                weak_no_context.append(term)

    ambiguous_ctx_hits = _all_hits(full, cfg.ambiguous_context) if cfg.ambiguous_context else context_hits
    for term in cfg.ambiguous_names:
        in_title, in_body = _count(title, term), _count(body, term)
        if in_title or in_body:
            if ambiguous_ctx_hits:
                where = "title" if in_title else "body"
                pts = WEIGHTS["acronym_context_title" if in_title else "acronym_context_body"] + 2
                candidates.append((pts, Evidence("ambiguous_name_with_context", term, where, pts)))
            else:
                weak_no_context.append(term)

    for rule in cfg.compound_rules:
        groups = rule.groups[1:] if (rule.official_implies_first_group and src_type == "official") else rule.groups
        if all(_first_hit(full, list(group)) for group in groups):
            in_title = all(_first_hit(title, list(group)) for group in groups)
            pts = rule.weight + (5 if in_title else 0)
            candidates.append((pts, Evidence("compound_rule", rule.name, "title" if in_title else "body", pts)))

    entity_points = 0
    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        entity_points, best = candidates[0]
        add(best.category, best.term, best.where, best.points)

    # The client's own official source supplies identity, but never relevance by itself: a topical
    # signal (announcement / admissions / placement / launch ...) is still required.
    if not candidates and src_type == "official":
        topical = [t for t, words in cfg.topic_keywords.items() if _first_hit(full, words)]
        if topical:
            pts = 30
            candidates.append((pts, Evidence("official_source", domain or "official source", "domain", pts)))
            entity_points = pts
            add("official_source", domain or "official source", "domain", pts)

    exec_hit = _first_hit(full, cfg.executive_keywords)
    if exec_hit:
        if entity_points:
            entity_points += WEIGHTS["executive_bonus"]
            add("executive", exec_hit, "text", WEIGHTS["executive_bonus"])
        elif context_hits:
            entity_points = WEIGHTS["executive_with_context"]
            add("executive", exec_hit, "text", entity_points)

    entity_points = min(entity_points, 60)
    has_entity_evidence = entity_points >= MIN_ENTITY_EVIDENCE

    total = entity_points
    matched_topics: list[str] = []
    product_hit = False
    industry_hits: list[str] = []

    if has_entity_evidence:
        # products
        prod = _first_hit(full, cfg.products)
        if prod:
            product_hit = True
            total += WEIGHTS["product"]
            add("product", prod, "text", WEIGHTS["product"])
        # industry
        industry_hits = _all_hits(full, cfg.industry_terms)
        ipts = min(WEIGHTS["industry_cap"], WEIGHTS["industry_each"] * len(industry_hits))
        if ipts:
            total += ipts
            add("industry", ", ".join(industry_hits[:3]), "text", ipts)
        # context
        cpts = min(WEIGHTS["context_cap"], WEIGHTS["context_each"] * len(context_hits))
        if cpts:
            total += cpts
            add("context", ", ".join(context_hits[:3]), "text", cpts)
        # location
        loc = _first_hit(full, cfg.location_keywords)
        if loc:
            total += WEIGHTS["location"]
            add("location", loc, "text", WEIGHTS["location"])
        # topics / business actions
        for topic, words in cfg.topic_keywords.items():
            if _first_hit(full, words):
                matched_topics.append(topic)
        tpts = min(WEIGHTS["topic_cap"], WEIGHTS["topic_each"] * len(matched_topics))
        if tpts:
            total += tpts
            add("topic", ", ".join(matched_topics[:3]), "text", tpts)
        # source trust
        if src_type == "official":
            pts = cfg.source_priority.get("official", 15)
            total += pts
            add("source_trust", domain, "domain", pts)
        elif src_type == "trusted_news":
            pts = cfg.source_priority.get("trusted", 8)
            total += pts
            add("source_trust", domain, "domain", pts)

    # soft negatives
    negatives = _all_hits(full, cfg.negative_keywords)
    if negatives and has_entity_evidence:
        penalty = WEIGHTS["soft_negative"] * min(2, len(negatives))
        total -= penalty
        add("soft_negative", ", ".join(negatives[:3]), "text", -penalty)

    total = max(0, min(100, total))

    reason = None
    if not has_entity_evidence:
        reason = "WEAK_ACRONYM_NO_CONTEXT" if weak_no_context else "NO_ENTITY_EVIDENCE"
    elif total < ACCEPT_THRESHOLD:
        reason = "BELOW_RELEVANCE_THRESHOLD"

    decision = "accepted" if reason is None else "rejected"
    return RelevanceDecision(
        entity_key=entity_key,
        decision=decision,
        relevance_score=total / 100.0,
        entity_points=entity_points,
        matched_terms=matched_terms,
        matched_topics=matched_topics,
        source_type=src_type,
        source_domain=domain,
        evidence=ev,
        negatives_hit=negatives,
        rejection_reason=reason,
        product_hit=product_hit,
        industry_hits=len(industry_hits),
        context_hits=len(context_hits),
    )


def evaluate_all_clients(
    *,
    title: str | None,
    body: str | None = None,
    source_domain: str | None = None,
    source_kind: str | None = None,
    source_client: str | None = None,
) -> dict[str, RelevanceDecision]:
    return {
        key: evaluate_relevance(
            key, title=title, body=body, source_domain=source_domain, source_kind=source_kind,
            source_client=source_client,
        )
        for key in ENTITY_CONFIGS
    }
