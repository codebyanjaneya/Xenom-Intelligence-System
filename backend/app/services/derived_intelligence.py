"""Deterministic, evidence-backed insights, opportunities, alerts and briefings.

Pure functions over a stored-article intelligence response (entities, events, relationships, timeline,
articles). No database access, no clock, no LLM: the same input always yields the same output, and every
derived object references the events, articles and verbatim evidence that triggered it. `as_of` defaults to
the latest event date in the input (never "now"), because the corpus is a stored snapshot.

Rules are the small tables below; they are meant to be read and reviewed, and each output names its rule.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

from app.schemas.derived import Alert, Briefing, BriefingEvent, Insight, Opportunity

CLUSTER_MIN_EVENTS = 3
CLUSTER_WINDOW_DAYS = 90
NEW_TYPE_WINDOW_DAYS = 30
NEW_TYPE_MIN_BASELINE = 2
ALERT_WINDOW_DAYS = 30
MIN_OPPORTUNITY_SCORE = 0.4
INSIGHT_UNAVAILABLE = ["business_impact", "urgency", "client_entity_id (persistence not performed)"]
BASE_LIMITATIONS = ["Events are sentences that state an action; reported does not mean verified.",
                    "Computed only from stored articles; activity outside the stored corpus is unknown."]
RULE_LIMITATIONS = {
    "I1_multi_source_event": ["Publisher independence is judged by publisher domain only; republished wire copy would count twice."],
    "I2_activity_cluster": ["Statements not merged by the conservative de-duplication may describe one story."],
    "I3_new_activity_type": ["Novelty is relative to the stored baseline, not to the real world."],
    "I4_co_involvement": ["Actors are the registered entities that precede the cue in one sentence; roles are not resolved."],
}

# event_type -> (base score, label, rule rationale). Rule-based reading of a *stated* event.
# Covers the PDF's opportunity signal set (Section 9): funding, partnership, expansion, hiring, new
# product/technology, procurement, government initiative, market entry, competitor weakness, new customer
# segment, research breakthrough. Every entry stays POTENTIAL/UNVERIFIED unless a Xenom capability is
# independently confirmed (see capability_match in derive_opportunities below).
OPPORTUNITY_RULES: dict[str, tuple[float, str, str]] = {
    "funding": (0.8, "funding event", "a funding event usually opens a budget window for technology and programme spend"),
    "investment": (0.7, "investment", "an announced investment usually signals capacity or capability build-out"),
    "expansion": (0.7, "expansion", "an expansion usually creates infrastructure and technology needs"),
    "partnership": (0.7, "partnership", "a new partnership or joint venture usually creates integration and delivery needs"),
    "acquisition": (0.7, "acquisition", "an acquisition usually creates integration needs"),
    "contract": (0.7, "contract or order", "a contract or order win usually requires delivery scale-up"),
    "agreement": (0.5, "agreement", "a signed agreement usually precedes execution work"),
    "appointment": (0.5, "leadership change", "a leadership change often opens a strategy-review window"),
    "launch": (0.3, "product launch", "a product launch can create go-to-market and channel needs"),
    "hiring": (0.4, "hiring", "a hiring drive usually signals capacity or capability build-out"),
    "procurement": (0.6, "procurement", "a tender or procurement process usually opens a supplier or delivery window"),
    "government_initiative": (0.6, "government initiative", "a government scheme or policy can open funding, compliance or delivery needs"),
    "market_entry": (0.6, "market entry", "entering a new market usually creates technology, distribution or partner needs"),
    "competitor_weakness": (0.5, "competitor weakness", "a stated setback for a competitor can open a relative opening for the client"),
    "new_customer_segment": (0.5, "new customer segment", "targeting a new segment usually creates supporting technology or process needs"),
    "research_breakthrough": (0.5, "research breakthrough", "a stated breakthrough can signal an emerging technology or capability worth tracking"),
}
# event_type -> what a human should assess after verifying the event. Deliberately generic: no client-specific
# business claim is made; every action starts with verification against the cited source.
ACTION_RULES: dict[str, str] = {
    "funding": "assess whether the stated use of funds relates to a Xenom capability",
    "investment": "assess whether the investment relates to a Xenom capability",
    "expansion": "assess the technology or infrastructure needs the expansion may create",
    "partnership": "assess the integration or delivery needs the partnership may create",
    "acquisition": "assess the integration needs the acquisition may create",
    "contract": "assess the delivery scale-up the contract may require",
    "agreement": "assess the execution work the agreement may lead to",
    "appointment": "consider whether the leadership change opens a strategy-review window",
    "launch": "consider the go-to-market or channel support the launch may need",
    "hiring": "assess whether the hiring drive points to a capability or capacity gap worth engaging",
    "procurement": "assess whether the tender or procurement need matches a Xenom capability",
    "government_initiative": "assess whether the scheme or policy creates a funding or compliance opening",
    "market_entry": "assess the technology, distribution or partner needs the market entry may create",
    "competitor_weakness": "assess whether the competitor's setback opens a relative opportunity for the client",
    "new_customer_segment": "assess the supporting technology or process needs of the new segment",
    "research_breakthrough": "assess whether the breakthrough is relevant to the client's technology roadmap",
}
OPPORTUNITY_INTERPRETATION = (
    "Rule-based interpretation of an event the article states; it is not a statement of the client's needs and "
    "no Xenom capability is matched (no verified capability data is stored)."
)

# rule_id -> (event types, severity, description)
ALERT_RULES: dict[str, tuple[frozenset, str, str]] = {
    "A1_capital_or_control_event": (frozenset({"acquisition", "funding"}), "warning",
                                    "acquisition or funding event within the alert window"),
    "A2_leadership_change": (frozenset({"appointment"}), "warning", "leadership change within the alert window"),
    "A3_commercial_event": (frozenset({"contract", "expansion", "partnership", "agreement", "investment"}), "info",
                            "contract, expansion, partnership, agreement or investment within the alert window"),
    "A4_widely_reported_event": (frozenset(), "warning", "any event reported by 3 or more publishers within the alert window"),
}


def _id(prefix: str, *parts) -> str:
    return prefix + "-" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


@dataclass(frozen=True)
class EventFact:
    event: object
    entity_keys: tuple
    when: datetime | None
    when_source: str
    article_ids: tuple
    publishers: tuple


def publisher_hosts(resp) -> dict:
    """article url -> publisher host. Aggregator URLs (e.g. news.google.com) hide the real publisher, so the
    publisher_url the pipeline stored in score_reason is preferred; otherwise the article URL host is used."""
    out = {}
    for a in resp.articles:
        stored = (a.relevance or {}).get("publisher_url") if isinstance(a.relevance, dict) else None
        out[a.url] = _host(stored or a.url)
    return out


def event_facts(resp) -> list[EventFact]:
    """One fact per event: actor entities (from relationships), best-known time, articles and publisher hosts."""
    hosts = publisher_hosts(resp)
    actors: dict = defaultdict(set)
    for r in resp.relationships:
        actors[r.event_key].add(r.entity_key)
    timeline = {t.event_key: t for t in resp.timeline}
    facts = []
    for e in resp.events:
        t = timeline.get(e.event_key)
        facts.append(EventFact(
            event=e, entity_keys=tuple(sorted(actors.get(e.event_key, ()))),
            when=t.ordering_time if t else None, when_source=t.ordering_time_source if t else "unknown",
            article_ids=tuple(str(a) for a in e.article_ids),
            publishers=tuple(sorted({hosts.get(ev.source_url) or _host(ev.source_url) for ev in e.evidence})),
        ))
    return facts


def _names(resp) -> dict:
    return {e.canonical_key: e.canonical_name for e in resp.entities}


def default_as_of(facts: list[EventFact]) -> datetime | None:
    whens = [f.when for f in facts if f.when]
    return max(whens) if whens else None


def _days(a: datetime | None, b: datetime | None) -> int | None:
    return (a - b).days if a and b else None


def _evidence_of(items: list[EventFact]) -> list:
    seen, out = set(), []
    for f in items:
        for ev in f.event.evidence:
            k = (ev.source_url, ev.content_span)
            if k not in seen:
                seen.add(k)
                out.append(ev)
    return out


def _fmt(d: datetime | None) -> str:
    return d.date().isoformat() if d else "unknown date"


# ---- insights -----------------------------------------------------------------------------------------------

def derive_insights(resp, as_of: datetime | None = None) -> list[Insight]:
    facts = event_facts(resp)
    as_of = as_of or default_as_of(facts)
    names = _names(resp)
    out: list[Insight] = []

    def make(kind, rule, client, items, title, why, strength, basis, entity_keys=None):
        ev_keys = sorted(f.event.event_key for f in items)
        arts = sorted({a for f in items for a in f.article_ids})
        latest = max((f.when for f in items if f.when), default=None)
        out.append(Insight(
            insight_id=_id("in", kind, client, *ev_keys), insight_type=kind, rule_id=rule, client_key=client,
            client_name=names.get(client, client), title=title, explanation=why,
            entity_keys=entity_keys or [client], event_keys=ev_keys, article_ids=arts, evidence=_evidence_of(items),
            strength=round(min(1.0, max(0.0, strength)), 3), recency_days=_days(as_of, latest), basis=basis,
            unavailable=INSIGHT_UNAVAILABLE, limitations=BASE_LIMITATIONS + RULE_LIMITATIONS.get(rule, []),
        ))

    keys = sorted({k for f in facts for k in f.entity_keys})
    for client in keys:
        mine = [f for f in facts if client in f.entity_keys]
        cname = names.get(client, client)
        # I1: the same event reported by several independent publishers
        for f in mine:
            n = len(f.publishers)
            if n >= 2:
                make("multi_source_event", "I1_multi_source_event", client, [f],
                     f"{cname}: {f.event.event_type} event reported by {n} publishers",
                     f"{n} distinct publishers ({', '.join(f.publishers)}) state the same {f.event.event_type} event: "
                     f"\"{f.event.title}\". Rule I1: an event merged from 2 or more publisher domains.",
                     0.3 + 0.2 * (n - 1), {"publishers": list(f.publishers), "event_type": f.event.event_type})
        # I2: several distinct events of one type in a short period
        by_type: dict = defaultdict(list)
        for f in mine:
            by_type[f.event.event_type].append(f)
        for etype, items in sorted(by_type.items()):
            dated = [f for f in items if f.when]
            if not dated:
                continue
            latest = max(f.when for f in dated)
            window = [f for f in dated if (latest - f.when).days <= CLUSTER_WINDOW_DAYS]
            arts = {a for f in window for a in f.article_ids}
            if len(window) >= CLUSTER_MIN_EVENTS and len(arts) >= 2:
                first = min(f.when for f in window)
                make("activity_cluster", "I2_activity_cluster", client, window,
                     f"{cname}: {len(window)} {etype} event statements between {_fmt(first)} and {_fmt(latest)}",
                     f"{len(window)} {etype} event statements for {cname} (not merged as the same event by the conservative "
                     f"de-duplication, so some may describe one story) appear in {len(arts)} stored articles between "
                     f"{_fmt(first)} and {_fmt(latest)}. Rule I2: at least {CLUSTER_MIN_EVENTS} such statements within "
                     f"{CLUSTER_WINDOW_DAYS} days from at least 2 articles.",
                     len(window) / 6, {"event_type": etype, "events": len(window), "articles": len(arts),
                                       "from": _fmt(first), "to": _fmt(latest)})
        # I3: an activity type that first appears in the latest window
        dated_all = sorted((f for f in mine if f.when), key=lambda f: f.when)
        if dated_all:
            end = dated_all[-1].when
            recent = [f for f in dated_all if (end - f.when).days <= NEW_TYPE_WINDOW_DAYS]
            base = [f for f in dated_all if f not in recent]
            if len(base) >= NEW_TYPE_MIN_BASELINE:
                base_types = {f.event.event_type for f in base}
                for etype in sorted({f.event.event_type for f in recent} - base_types):
                    items = [f for f in recent if f.event.event_type == etype]
                    make("new_activity_type", "I3_new_activity_type", client, items,
                         f"{cname}: first {etype} activity in the latest {NEW_TYPE_WINDOW_DAYS} days",
                         f"{etype} activity for {cname} appears in the latest window ({_fmt(recent[0].when)} to "
                         f"{_fmt(end)}) but not among the {len(base)} earlier stored events (types: "
                         f"{', '.join(sorted(base_types))}). Rule I3. Limitation: activity that is not in the stored "
                         f"corpus is unknown.",
                         0.5, {"new_type": etype, "baseline_events": len(base), "baseline_types": sorted(base_types)})
    # I4: several registered entities are actors of one event
    for f in facts:
        if len(f.entity_keys) >= 2:
            who = ", ".join(names.get(k, k) for k in f.entity_keys)
            make("co_involvement", "I4_co_involvement", f.entity_keys[0], [f],
                 f"{who} named together in a {f.event.event_type} event",
                 f"{who} all precede the action cue in the same stored sentence: \"{f.event.title}\". Rule I4.",
                 0.6, {"entities": list(f.entity_keys)}, entity_keys=list(f.entity_keys))
    out.sort(key=lambda i: (-i.strength, i.insight_id))
    return out


# ---- opportunities ------------------------------------------------------------------------------------------

def derive_opportunities(resp, insights: list[Insight], as_of: datetime | None = None) -> list[Opportunity]:
    facts = event_facts(resp)
    names = _names(resp)
    by_event: dict = defaultdict(list)
    for i in insights:
        for k in i.event_keys:
            by_event[k].append(i.insight_id)
    out: list[Opportunity] = []
    for f in facts:
        rule = OPPORTUNITY_RULES.get(f.event.event_type)
        if not rule:
            continue
        base, label, why = rule
        bonus = min(0.2, 0.1 * (len(f.publishers) - 1))
        score = round(min(1.0, base + bonus), 3)
        if score < MIN_OPPORTUNITY_SCORE:
            continue
        for client in f.entity_keys:
            cname = names.get(client, client)
            out.append(Opportunity(
                opportunity_id=_id("op", client, f.event.event_key), client_key=client, client_name=cname,
                title=f"{cname}: {label} - {f.event.title[:140]}",
                rationale=f"Rule O_{f.event.event_type}: {why}. Supported by {len(f.article_ids)} stored article(s) and "
                          f"{len(f.publishers)} publisher(s); the stated event is \"{f.event.title}\".",
                rule_id=f"O_{f.event.event_type}", interpretation=OPPORTUNITY_INTERPRETATION,
                opportunity_score=score, score_components={"base": base, "multi_publisher_bonus": bonus,
                                                           "threshold": MIN_OPPORTUNITY_SCORE},
                recommended_action=f"Verify the {f.event.event_type} event in the cited source, then "
                                   f"{ACTION_RULES[f.event.event_type]}.",
                action_basis=f"O_{f.event.event_type} on event {f.event.event_key}",
                insight_ids=sorted(by_event.get(f.event.event_key, [])), event_keys=[f.event.event_key],
                entity_keys=list(f.entity_keys), article_ids=sorted(f.article_ids), evidence=list(f.event.evidence),
            ))
    out.sort(key=lambda o: (-o.opportunity_score, o.opportunity_id))
    return out


# ---- alerts -------------------------------------------------------------------------------------------------

def derive_alerts(resp, insights: list[Insight], as_of: datetime | None = None) -> list[Alert]:
    facts = event_facts(resp)
    as_of = as_of or default_as_of(facts)
    names = _names(resp)
    by_event: dict = defaultdict(list)
    for i in insights:
        for k in i.event_keys:
            by_event[k].append(i.insight_id)
    out: list[Alert] = []
    if as_of is None:
        return out
    for f in facts:
        if not f.when or not (timedelta(0) <= as_of - f.when <= timedelta(days=ALERT_WINDOW_DAYS)):
            continue
        for rule_id, (types, severity, desc) in ALERT_RULES.items():
            hit = (f.event.event_type in types) if types else len(f.publishers) >= 3
            if not hit:
                continue
            sev = "critical" if rule_id == "A1_capital_or_control_event" and len(f.publishers) >= 2 else severity
            for client in f.entity_keys:
                cname = names.get(client, client)
                out.append(Alert(
                    alert_id=_id("al", rule_id, client, f.event.event_key), rule_id=rule_id, severity=sev,
                    client_key=client, client_name=cname,
                    title=f"{cname}: {f.event.event_type} event ({_fmt(f.when)})",
                    body=f"\"{f.event.title}\" - {len(f.publishers)} publisher(s), {len(f.article_ids)} article(s). "
                         f"Rule {rule_id}: {desc}.",
                    triggered_because={"event_type": f.event.event_type, "when": _fmt(f.when), "when_source": f.when_source,
                                       "publishers": len(f.publishers), "window_days": ALERT_WINDOW_DAYS,
                                       "as_of": _fmt(as_of)},
                    event_keys=[f.event.event_key], insight_ids=sorted(by_event.get(f.event.event_key, [])),
                    article_ids=sorted(f.article_ids), evidence=list(f.event.evidence), as_of=as_of,
                ))
    order = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda a: (order[a.severity], a.alert_id))
    return out


# ---- briefing -----------------------------------------------------------------------------------------------

def build_briefing(resp, insights, opportunities, alerts, scope: dict, as_of: datetime | None = None,
                   max_events: int = 10) -> Briefing:
    facts = event_facts(resp)
    explicit_as_of = as_of is not None
    as_of = as_of or default_as_of(facts)
    names = _names(resp)
    ranked = sorted(facts, key=lambda f: (f.when is None, -(f.when.timestamp() if f.when else 0), f.event.event_key))
    key_events = [
        BriefingEvent(event_key=f.event.event_key, event_type=f.event.event_type, title=f.event.title, when=f.when,
                      when_source=f.when_source, publishers=list(f.publishers), article_ids=list(f.article_ids),
                      entity_keys=list(f.entity_keys), evidence=list(f.event.evidence)[:2])
        for f in ranked[:max_events]
    ]
    titles = {f.event.event_key: f.event.title for f in facts}
    timeline = [{"position": t.position, "event_key": t.event_key, "ordering_time": t.ordering_time,
                 "ordering_time_source": t.ordering_time_source, "title": titles.get(t.event_key, ""),
                 "article_ids": [str(a) for a in t.article_ids]} for t in resp.timeline]
    host_of = publisher_hosts(resp)
    hosts = Counter(host_of[a.url] for a in resp.articles)
    classes = Counter(a.source_class or "unknown" for a in resp.articles)
    sources = [{"publisher": h, "articles": n} for h, n in sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))] + \
              [{"source_class": c, "articles": n} for c, n in sorted(classes.items(), key=lambda kv: (-kv[1], kv[0]))]
    dates = [a.published_at for a in resp.articles if a.published_at]
    types = Counter(f.event.event_type for f in facts)
    span = f"{_fmt(min(dates))} to {_fmt(max(dates))}" if dates else "unknown dates"
    label = scope.get("client") or scope.get("query") or "the selection"
    summary = (
        f"{label}: {len(resp.articles)} stored articles ({span}) from {len(hosts)} publishers state {len(facts)} distinct "
        f"events" + (f" ({', '.join(f'{n} {t}' for t, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0])))})" if types else "")
        + f"; {len(insights)} insights, {len(opportunities)} opportunities and {len(alerts)} alerts were derived from them."
    )
    limitations = [
        "Events are sentences that state an action; 'reported' does not mean verified.",
        "An event date is the article's stated date only when when_source is event_time; otherwise it is the article's "
        "publication time (publication_fallback).",
        "Insights, opportunities and alerts are deterministic rules over stored articles only; business impact, urgency "
        "and Xenom capability matches are not derivable from stored data.",
    ]
    if not explicit_as_of:
        limitations.append("as_of is the latest event date in the stored corpus, not the current date.")
    return Briefing(
        scope=scope, as_of=as_of, as_of_basis="explicit" if explicit_as_of else "latest stored event date",
        generated_from={"articles": len(resp.articles), "events": len(facts), "entities": len(resp.entities),
                        "relationships": len(resp.relationships)},
        summary=summary, key_events=key_events,
        entities=[{"canonical_key": e.canonical_key, "canonical_name": e.canonical_name, "type": e.entity_type.value,
                   "article_count": e.article_count} for e in resp.entities],
        timeline=timeline, sources=sources, insights=insights, opportunities=opportunities, alerts=alerts,
        limitations=limitations,
    )
