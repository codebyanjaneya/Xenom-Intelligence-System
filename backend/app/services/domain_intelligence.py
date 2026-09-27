"""Cross-cutting, evidence-backed views over stored accepted articles (read-only, deterministic).

technology  : AI/technology themes (config/intelligence_themes.py), each hit with a verbatim, markup-free quote;
industry    : client -> industry term -> signal, from each client's configured industry keywords;
competitors : stored COMPETITOR_OF relationships plus conservative names inferred from accepted article evidence;
clients     : per-client counts straight from the database;
feed        : a fair global feed (round-robin across clients, capped per publisher) instead of "newest N".
Nothing here generates text: every signal is a stored article plus a substring of its stored text.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import CLIENT_CONFIG, get_all_clients
from app.config.intelligence_themes import TECH_THEMES
from app.config.scheduler import SCHEDULER_CONFIG
from app.config.theme_discovery import THEME_DISCOVERY, theme_specs
from app.models.content import Article
from app.models.architecture import DocumentAuthor
from app.models.entities import Entity, EntityMention, Relationship, RelationshipEvidence
from app.models.sources import Source, SourceHealth
from app.schemas.article import ArticleOut
from app.schemas.domain import (
    ClientIndustry, ClientSummary, DigestItem, DigestSection, EvidenceQuote as _EQ, MorningDigest, ResearchRecord, ResearchView,
    IndustryEntityResult, ClientSummaryView, CompetitorView, EvidenceQuote, IndustryTermResult, IndustryView,
    SignalRef, ThemeResult, ThemeView,
)
from app.services.article_evidence import FIELDS, find_spans
from app.services.articles import _relevance_evidence, get_articles
from app.services.topic_tagger import tag_article_topics
from app.services.digest_ranking import DigestCandidate, rank_candidate, select_diverse

_XENOM = "xenom technology"


def _host(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host or None


async def _accepted(session: AsyncSession) -> list[dict]:
    stmt = (
        select(Article, Source.name, Source.tier, Source.reliability_score)
        .outerjoin(Source, Article.source_id == Source.id)
        .where(Article.processing_status == "accepted", Article.quality_gate_passed.is_(True))
        .order_by(Article.published_at.desc().nullslast(), Article.id)
    )
    rows = []
    for a, source_name, source_tier, reliability_score in (await session.execute(stmt)).all():
        rel = _relevance_evidence(a.score_reason) or {}
        rows.append({
            "id": a.id, "url": a.canonical_url or a.url, "published_at": a.published_at, "source": source_name,
            "publisher": _host(rel.get("publisher_url")) or rel.get("source_domain") or _host(a.canonical_url or a.url),
            "fields": {"title": a.title, "summary": a.summary, "raw_text": a.raw_text}, "topic": a.topic,
            "source_class": rel.get("source_type"), "tier": source_tier, "reliability_score": reliability_score,
            "source_metadata": a.source_metadata,
        })
    return rows


async def _clients_of(session: AsyncSession) -> dict:
    out: dict = defaultdict(set)
    for aid, name in (await session.execute(
        select(EntityMention.article_id, Entity.canonical_name)
        .join(Entity, Entity.id == EntityMention.entity_id)
        .where(Entity.entity_type == "ORG", Entity.canonical_key != _XENOM)
    )).all():
        out[aid].add(name)
    return {k: sorted(v) for k, v in out.items()}


def _terms_in(rx: re.Pattern, fields: dict) -> set[str]:
    return {m.group(0).lower() for name in FIELDS for m in rx.finditer(fields.get(name) or "")}


def _signal(row: dict, clients: dict, terms: set[str]) -> SignalRef | None:
    spans = find_spans(row["fields"], sorted(terms), max_spans=1)
    if not spans:
        return None                                       # no clean verbatim evidence -> not reported
    sp = spans[0]
    return SignalRef(
        article_id=row["id"], title=row["fields"].get("title"), url=row["url"], published_at=row["published_at"],
        source=row["source"], publisher=row["publisher"], clients=clients.get(row["id"], []),
        matched_terms=sorted(terms), evidence=EvidenceQuote(field=sp.field, start=sp.start, end=sp.end, quote=sp.quote),
    )


def _rx(pattern: str) -> re.Pattern:
    return re.compile(r"\b(?:" + pattern + r")\b", re.IGNORECASE)


async def _theme_discovery(session: AsyncSession, key: str) -> dict:
    phrase = THEME_DISCOVERY.get(key)
    if phrase is None:
        return {"configured": False, "reason": "No discovery query is configured for this theme (config/theme_discovery.py)."}
    specs = theme_specs(theme=key)
    names = [f"Google News: {c} ({q})" for c, _k, q, _u in specs]
    n_sources, last = (await session.execute(
        select(func.count(func.distinct(Source.id)), func.max(SourceHealth.checked_at))
        .select_from(Source).outerjoin(SourceHealth, SourceHealth.source_id == Source.id).where(Source.name.in_(names)))).one()
    return {
        "configured": True, "phrase": phrase, "queries": len(specs), "clients": sorted({c for c, *_ in specs}),
        "cadence_minutes": SCHEDULER_CONFIG.theme_discovery_interval_minutes if SCHEDULER_CONFIG.theme_discovery_enabled else None,
        "enabled": SCHEDULER_CONFIG.theme_discovery_enabled,
        "ingestion_path": "Google News RSS -> clean/dedupe -> client-relevance gate -> quality gate -> DB -> literal match + verbatim evidence",
        "sources_registered": n_sources, "last_attempt_at": last.isoformat() if last else None,
        "status": "NEVER_ATTEMPTED" if not last else "ATTEMPTED",
    }


async def technology_view(session: AsyncSession, per_theme: int = 5, *, exclude_source_class: str | None = None) -> ThemeView:
    rows = await _accepted(session)
    if exclude_source_class:
        rows = [r for r in rows if (r.get("source_class") or "").lower() != exclude_source_class.lower()]
    clients = await _clients_of(session)
    excluded = 0
    themes: list[ThemeResult] = []
    for theme in TECH_THEMES:
        rx, signals = _rx(theme.pattern), []
        for row in rows:
            terms = _terms_in(rx, row["fields"])
            if not terms:
                continue
            sig = _signal(row, clients, terms)
            if sig is None:
                excluded += 1
            else:
                signals.append(sig)
        by_client = Counter(c for s in signals for c in (s.clients or ["(no registered client)"]))
        themes.append(ThemeResult(
            key=theme.key, label=theme.label,
            status="EVIDENCE_FOUND" if signals else "NO_EVIDENCE_IN_STORED_CORPUS",
            article_count=len(signals), by_client=dict(sorted(by_client.items())), signals=signals[:per_theme],
            discovery=await _theme_discovery(session, theme.key),
        ))
    return ThemeView(
        scope=f"{len(rows)} stored accepted articles", themes=themes, excluded_without_clean_evidence=excluded,
        themes_with_evidence=[t.key for t in themes if t.article_count],
        themes_without_evidence=[t.key for t in themes if not t.article_count],
        note="A theme is evidenced only when its literal terms occur in stored text with a markup-free quote. Themes with "
             "no stored evidence stay empty: the current discovery queries are client-centred, so themes such as generative "
             "or embodied AI need AI/technology discovery sources before they can appear.",
    )


async def industry_view(session: AsyncSession, per_term: int = 3) -> IndustryView:
    rows = await _accepted(session)
    clients = await _clients_of(session)
    by_client: dict = defaultdict(list)
    for row in rows:
        for c in clients.get(row["id"], []):
            by_client[c].append(row)
    result: list[ClientIndustry] = []
    for client in get_all_clients():
        cfg = CLIENT_CONFIG[client]
        articles = by_client.get(client, [])
        terms = []
        for term in sorted({k.lower() for k in cfg.industry_keywords}):
            rx, signals = _rx(re.escape(term)), []
            for row in articles:
                found = _terms_in(rx, row["fields"])
                sig = _signal(row, clients, found) if found else None
                if sig:
                    signals.append(sig)
            terms.append(IndustryTermResult(term=term, article_count=len(signals), signals=signals[:per_term]))
        terms.sort(key=lambda t: (-t.article_count, t.term))
        result.append(ClientIndustry(client=client, accepted_articles=len(articles), industry_terms=terms))
    registered = [r[0] for r in (await session.execute(
        select(Entity.canonical_name).where(Entity.entity_type == "INDUSTRY").order_by(Entity.canonical_name))).all()]
    entities: list[IndustryEntityResult] = []
    for name in registered:
        rx, signals = _rx(re.escape(name.lower())), []
        for row in rows:
            found = _terms_in(rx, row["fields"])
            sig = _signal(row, clients, found) if found else None
            if sig:
                signals.append(sig)
        by_c = Counter(c for s_ in signals for c in (s_.clients or ["(no registered client)"]))
        entities.append(IndustryEntityResult(
            industry=name, status="EVIDENCE_FOUND" if signals else "NO_EVIDENCE_IN_STORED_CORPUS",
            article_count=len(signals), by_client=dict(sorted(by_c.items())), signals=signals[:per_term]))
    labels: Counter = Counter()
    for row in rows:
        found = [x.strip() for x in (row["topic"] or "").split("|") if x.strip()]
        for label in found or ["Unlabelled"]:
            labels[label] += 1
    stored = [r for r in rows if (r["topic"] or "").strip()]
    unlabelled = [r for r in rows if not (r["topic"] or "").strip()]
    def current(r):
        return "|".join(t.value for t in tag_article_topics(r["fields"].get("title"), r["fields"].get("summary")))
    coverage = {
        "accepted_articles": len(rows), "stored_labelled": len(stored), "stored_unlabelled": len(unlabelled),
        "unlabelled_the_current_classifier_would_label": sum(1 for r in unlabelled if current(r)),
        "stored_labels_reproduced_by_current_classifier": sum(1 for r in stored if current(r) == r["topic"]),
        "explanation": "Unlabelled means the stored row has no topic; it is not a topic and no label is guessed. The keyword "
                       "classifier that ran at ingestion is deterministic on title + summary; rows that the current classifier "
                       "would label differently were tagged by an earlier version or before their summary was enriched. Stored "
                       "rows are never rewritten by this view; a backfill would modify the corpus and needs explicit approval.",
    }
    return IndustryView(
        topic_coverage=coverage,
        scope=f"{len(rows)} stored accepted articles", clients=result, registered_industries=registered, industry_entities=entities,
        taxonomy_labels=dict(sorted(labels.items(), key=lambda kv: (-kv[1], kv[0]))),
        note="Industry terms come from each client's configured industry keywords and count only articles already "
             "attributed to that client. Registered INDUSTRY entities are resolved to articles by literal name match in stored text (entity "
             "linking itself is ORG-only; no links are created), and taxonomy labels are the existing keyword classifier's output.",
    )


_DEVELOPMENT_LABELS = ("product_business_movement", "partnership", "ai_model", "technology", "market_financial_signal",
                      "regulatory_policy", "opportunity")
_COMPARISON_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "product_technology": ("product_business_movement", "technology"),
    "ai_adoption": ("ai_model",),
    "digital_capability": ("technology",),
    "market_activity": ("market_financial_signal",),
    "partnerships": ("partnership",),
    "strategic_moves": ("product_business_movement", "regulatory_policy"),
}
_IMPACT_TEMPLATES = {
    "product_business_movement": ("A competitor product, launch or capacity move can shift what clients expect in this market.",
                                  "The client's own offering may look dated by comparison if this is not addressed.",
                                  "A chance to match or differentiate the client's own roadmap against this stated move."),
    "partnership": ("A competitor partnership can open a distribution or technology channel the client does not have.",
                    "Loss of exclusivity or access to a partner ecosystem the competitor now shares.",
                    "A similar partnership or an alternative alliance the client could pursue."),
    "ai_model": ("Competitor AI/model adoption signals where the technology conversation in this sector is heading.",
                "Falling behind on AI-enabled features that buyers start to expect.",
                "Evaluate whether the same or an adjacent AI capability is worth exploring for the client."),
    "technology": ("A stated technology move changes the competitive baseline in the client's sector.",
                  "The client's technology positioning may need to be re-examined against this.",
                  "A technology conversation or capability review with the client."),
    "market_financial_signal": ("Financial or market signals reveal competitor momentum or strain.",
                                "A financially stronger competitor may outspend the client on growth.",
                                "If the competitor shows strain, an opening for the client to gain share."),
    "regulatory_policy": ("A regulatory or policy move can change compliance costs or market access for everyone in the sector.",
                          "The client may face the same regulatory pressure without preparation.",
                          "Early alignment with the same policy shift ahead of competitors."),
    "opportunity": ("A detected opportunity signal (funding, expansion, hiring, procurement) around the competitor.",
                    "The competitor may be positioning to capture demand the client also serves.",
                    "Review whether the same signal represents an opening for the client."),
}


async def _label_rows(session: AsyncSession, article_ids) -> dict:
    if not article_ids:
        return {}
    from app.models.architecture import ArticleLabel
    rows = (await session.execute(select(ArticleLabel).where(ArticleLabel.article_id.in_(article_ids),
                                                              ArticleLabel.label.in_(_DEVELOPMENT_LABELS))
                                  .order_by(ArticleLabel.article_id, ArticleLabel.label))).all()
    out: dict = defaultdict(list)
    for (row,) in rows:
        out[row.article_id].append(row)
    return out


def _development(row: dict, label_row) -> dict | None:
    ev = label_row.evidence or {}
    terms = ev.get("terms") or []
    spans = find_spans(row["fields"], sorted({t.lower() for t in terms})) if terms else []
    quote = spans[0].quote if spans else None
    if not quote:
        return None
    return {"type": label_row.label, "title": row["fields"].get("title"), "url": row["url"], "published_at": row["published_at"],
            "source": row["source"], "quote": quote, "confidence": label_row.confidence}


def _best_evidence(devs: list[dict], labels: tuple[str, ...]) -> dict | None:
    matches = [d for d in devs if d["type"] in labels]
    if not matches:
        return None
    matches.sort(key=lambda d: (d["published_at"] is None, d["published_at"] and -d["published_at"].timestamp() if d["published_at"] else 0))
    best = matches[0]
    return {"quote": best["quote"], "url": best["url"], "date": best["published_at"], "source": best["source"], "confidence": best["confidence"]}


async def _client_dimension_evidence(session: AsyncSession, client_name: str, rows: dict, clients: dict) -> dict:
    """The client's OWN evidence for each comparison dimension, from its own accepted articles (never a competitor's)."""
    own_ids = [aid for aid, row in rows.items() if client_name.lower() in {c.lower() for c in clients.get(aid, [])}]
    label_map = await _label_rows(session, own_ids)
    devs = []
    for aid in own_ids:
        for label_row in label_map.get(aid, []):
            d = _development(rows[aid], label_row)
            if d:
                devs.append(d)
    return {dim: _best_evidence(devs, labels) for dim, labels in _COMPARISON_DIMENSIONS.items()}


async def competitor_view(session: AsyncSession, client: str | None = None) -> CompetitorView:
    rels_stmt = (select(Relationship, Entity).join(Entity, Entity.id == Relationship.source_entity_id)
                .where(Relationship.relationship_type == "COMPETITOR_OF"))
    rels = (await session.execute(rels_stmt)).all()
    n_language = await session.scalar(
        select(func.count()).where(Article.processing_status == "accepted", Article.topic.ilike("%competitor%")))
    competitors = []
    rows = {r["id"]: r for r in await _accepted(session)}
    clients = await _clients_of(session)
    client_dim_cache: dict[str, dict] = {}
    seen_names: set[str] = set()

    def add_entry(competitor_name: str, target_name: str | None, rel_status: str | None, notes: list[str], signals: list[SignalRef],
                  developments: list[dict], confidence: float | None, comparison: dict | None = None, impact: list[dict] | None = None):
        key = f"{(target_name or '').lower()}::{competitor_name.lower()}"
        if key in seen_names:
            return
        seen_names.add(key)
        competitors.append({
            "competitor": competitor_name, "competitor_of": target_name,
            "profile": {"name": competitor_name, "status": "inferred", "relationship_status": rel_status or "not_verified",
                       "confidence": confidence, "basis": notes[:2], "domain_industry": None},
            "article_count": len(signals), "signals": [s.model_dump(mode="json") for s in signals[:5]],
            "developments": developments[:10], "comparison": comparison or {}, "impact": impact or [],
            "relationship_status": rel_status or "not_verified", "basis": notes[:2],
            "coverage": "no stored article names this competitor" if not signals else None,
        })

    if rels:
        for rel, competitor in rels:
            target = await session.get(Entity, rel.target_entity_id)
            target_name = target.canonical_name if target else None
            if client and (not target_name or target_name.lower() != client.strip().lower()):
                continue
            article_ids = {r[0] for r in (await session.execute(
                select(EntityMention.article_id).where(EntityMention.entity_id == competitor.id).distinct())).all()}
            rx = _rx(re.escape(competitor.canonical_name))
            article_ids |= {aid for aid, row in rows.items()
                            if target_name and target_name.lower() in {c.lower() for c in clients.get(aid, [])} and _terms_in(rx, row["fields"])}
            notes = [n for (n,) in (await session.execute(
                select(RelationshipEvidence.note).where(RelationshipEvidence.relationship_id == rel.id).order_by(RelationshipEvidence.created_at))).all() if n]
            signals = [s for aid in sorted(article_ids, key=str) if aid in rows
                       for s in [_signal(rows[aid], clients, _terms_in(rx, rows[aid]["fields"]) or {competitor.canonical_name.lower()})]
                       if s]

            label_map = await _label_rows(session, article_ids)
            developments = []
            for aid in sorted(article_ids, key=str):
                if aid not in rows:
                    continue
                for label_row in label_map.get(aid, []):
                    d = _development(rows[aid], label_row)
                    if d:
                        developments.append(d)
            developments.sort(key=lambda d: (d["published_at"] is None, -(d["published_at"].timestamp() if d["published_at"] else 0)))

            publishers = {rows[aid]["publisher"] for aid in article_ids if aid in rows and rows[aid]["publisher"]}
            confidence = round(min(0.9, 0.3 + 0.15 * len(publishers)) * (0.6 if rel.verification_status != "verified" else 1.0), 2) if publishers else None

            comparison = {}
            if target_name:
                if target_name not in client_dim_cache:
                    client_dim_cache[target_name] = await _client_dimension_evidence(session, target_name, rows, clients)
                own_dims = client_dim_cache[target_name]
                for dim, labels in _COMPARISON_DIMENSIONS.items():
                    comp_ev = _best_evidence(developments, labels)
                    own_ev = own_dims.get(dim)
                    comparison[dim] = {
                        "client_evidence": own_ev, "competitor_evidence": comp_ev,
                        "status": "insufficient evidence" if not own_ev and not comp_ev else
                                 ("client evidence only" if comp_ev is None else ("competitor evidence only" if own_ev is None else "both sides have evidence")),
                    }

            impact = []
            for dev in developments[:3]:
                template = _IMPACT_TEMPLATES.get(dev["type"])
                if not template:
                    continue
                why, risk, opp = template
                impact.append({
                    "development": {"type": dev["type"], "title": dev["title"], "url": dev["url"], "date": dev["published_at"], "source": dev["source"]},
                    "why_it_matters": why, "potential_risk": risk, "potential_opportunity": {"text": opp, "status": "POTENTIAL", "capability_match": "UNVERIFIED"},
                    "recommended_action": {"text": "Verify this development against the cited source before acting.", "review_status": "needs_review"},
                })

            add_entry(competitor.canonical_name, target_name, rel.verification_status, notes,
                      signals, developments, confidence, comparison, impact)

    if client:
        target_names = [client]
    else:
        target_names = get_all_clients()

    for target_name in target_names:
        target_lower = target_name.lower()
        relevant_ids = [aid for aid, row in rows.items() if target_lower in {c.lower() for c in clients.get(aid, [])}]
        if not relevant_ids:
            continue
        entity_rows = (await session.execute(
            select(EntityMention.article_id, Entity.canonical_name)
            .join(Entity, Entity.id == EntityMention.entity_id)
            .where(Entity.entity_type == "ORG", EntityMention.article_id.in_(relevant_ids))
        )).all()
        by_company: dict[str, set] = {}
        for article_id, name in entity_rows:
            name = (name or '').strip()
            if not name:
                continue
            normalized = name.lower()
            if normalized in {target_lower, _XENOM, 'xenom'}:
                continue
            if normalized in {c.lower() for c in get_all_clients()}:
                continue
            by_company.setdefault(name, set()).add(article_id)

        for company_name, article_ids in sorted(by_company.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            company_ids = sorted(article_ids, key=str)
            rx = _rx(re.escape(company_name))
            signals = []
            for aid in company_ids:
                row = rows.get(aid)
                if row is None:
                    continue
                found = _terms_in(rx, row["fields"])
                if not found:
                    continue
                signal = _signal(row, clients, found)
                if signal:
                    signals.append(signal)
            if not signals:
                continue
            notes = [f"Inferred from {len(signals)} accepted article mentions for {target_name}."]
            label_map = await _label_rows(session, company_ids)
            developments = []
            for aid in company_ids:
                row = rows.get(aid)
                if row is None:
                    continue
                for label_row in label_map.get(aid, []):
                    d = _development(row, label_row)
                    if d:
                        developments.append(d)
            developments.sort(key=lambda d: (d["published_at"] is None, -(d["published_at"].timestamp() if d["published_at"] else 0)))
            confidence = min(0.94, 0.45 + (0.08 * min(len(company_ids), 6)))
            add_entry(company_name, target_name, "not_verified", notes, signals, developments, round(confidence, 2))

    if not competitors:
        return CompetitorView(
            configured=False, competitors=[],
            unverified_competitor_language_articles=n_language or 0,
            reason=("No competitor is configured for this client." if client else
                    "No competitor relationships are stored; competitors are never inferred from the live corpus."),
            how_to_configure="List the competitors in the client profile or let the corpus infer them from the client's accepted article mentions.",
        )

    return CompetitorView(
        configured=True, competitors=competitors,
        unverified_competitor_language_articles=n_language or 0,
        reason="" if client else "Competitor names were inferred from the live article corpus for the selected client.",
        how_to_configure="These competitor rows are derived from stored article mentions and may still need a human verification pass.",
    )


async def research_view(session: AsyncSession) -> ResearchView:
    """Papers / researchers / labs. Only stored articles whose source class is "research" can appear here; client news
    is never presented as research. Authors and institutions are reported as NOT_STORED because no field holds them."""
    records = []
    authors_by_article: dict = {}
    for aid, name, affiliation, position in (await session.execute(
            select(DocumentAuthor.article_id, Entity.canonical_name, DocumentAuthor.affiliation, DocumentAuthor.position)
            .join(Entity, Entity.id == DocumentAuthor.entity_id).order_by(DocumentAuthor.article_id, DocumentAuthor.position))).all():
        authors_by_article.setdefault(str(aid), []).append({"name": name, "affiliation": affiliation})
    for row in await _accepted(session):
        if (row["source_class"] or "").lower() != "research":
            continue
        title = row["fields"].get("title") or ""
        names = authors_by_article.get(str(row["id"]), [])
        orgs = sorted({a["affiliation"] for a in names if a["affiliation"]})
        metrics = row.get("source_metadata") or {}
        kind = "model" if "downloads" in metrics or "pipeline_tag" in metrics or "huggingface.co" in (row["publisher"] or "") else "paper"
        records.append(ResearchRecord(
            kind=kind, title=title or None, url=row["url"], published_at=row["published_at"], publisher=row["publisher"],
            authors=[a["name"] for a in names], authors_status="STORED" if names else "NOT_STORED",
            institutions=orgs, institutions_status="STORED" if orgs else "NOT_STORED",
            evidence=EvidenceQuote(field="title", start=0, end=len(title), quote=title), metrics=metrics))
    return ResearchView(
        status="RESEARCH_RECORDS_FOUND" if records else "NO_RESEARCH_DATA_IN_STORED_CORPUS", records=records,
        papers=[r for r in records if r.kind == "paper"], models=[r for r in records if r.kind == "model"],
        reason="" if records else "No stored article comes from a research source (papers, labs, institutions). Searching "
                                   "client news is not research intelligence and is never shown here.",
        interface={"required": ["title", "canonical URL", "publication date", "publisher/venue", "verbatim evidence"],
                   "stored_when_the_source_states_them": ["authors", "institutions/labs"], "not_yet_stored": ["DOI or paper ID"],
                   "researchers": "GET /intelligence/researchers",
                   "source_class": "research", "how_to_add": "Register a research source (arXiv, institution or journal feed) "
                                                             "whose accepted articles carry source class \"research\"."})


async def client_summary(session: AsyncSession) -> ClientSummaryView:
    rows = await _accepted(session)
    clients = await _clients_of(session)
    total_rejected = await session.scalar(select(func.count()).where(Article.processing_status == "rejected")) or 0
    out = []
    for name in get_all_clients():
        mine = [r for r in rows if name in clients.get(r["id"], [])]
        classes: Counter = Counter(r["source_class"] or "unknown" for r in mine)
        labels: Counter = Counter()
        for r in mine:
            for label in (r["topic"] or "").split("|"):
                if label.strip():
                    labels[label.strip()] += 1
        out.append(ClientSummary(
            client=name, accepted_articles=len(mine),
            latest_published_at=max((r["published_at"] for r in mine if r["published_at"]), default=None),
            publishers=len({r["publisher"] for r in mine if r["publisher"]}),
            source_classes=dict(sorted(classes.items())),
            topic_labels=dict(sorted(labels.items(), key=lambda kv: (-kv[1], kv[0]))),
        ))
    return ClientSummaryView(clients=out, total_accepted=len(rows), total_rejected=total_rejected)


async def diverse_feed(session: AsyncSession, total: int = 24, max_per_publisher: int = 3,
                       allowed_clients: list[str] | None = None) -> list[ArticleOut]:
    """Round-robin across clients (each client's newest first), at most `max_per_publisher` per publisher, an
    article used once even when it is tagged to several clients. Deterministic; nothing is invented."""
    lists = []
    for name in allowed_clients if allowed_clients is not None else get_all_clients():
        items = await get_articles(session, entity_key=name.lower(), limit=max(total, 1))
        if items:
            lists.append((max((i.published_at for i in items if i.published_at), default=None), name, items))
    lists.sort(key=lambda t: (t[0] is None, -(t[0].timestamp() if t[0] else 0), t[1]))
    queues = [list(items) for _, _, items in lists]
    used: set = set()
    per_pub: Counter = Counter()
    feed: list[ArticleOut] = []
    while queues and len(feed) < total:
        progressed = False
        for q in queues:
            while q:
                item = q.pop(0)
                pub = (item.relevance or {}).get("source_domain") or _host(item.canonical_url or item.url) or ""
                if item.id in used or per_pub[pub] >= max_per_publisher:
                    continue
                used.add(item.id)
                per_pub[pub] += 1
                feed.append(item)
                progressed = True
                break
            if len(feed) >= total:
                break
        if not progressed:
            break
        queues = [q for q in queues if q]
    return feed


DIGEST_MAX_SECTIONS = 6  # how many of the strongest theme/client pools become sections each run


def _digest_pool() -> list[tuple[str, str, str]]:
    """(key, label, pattern) for every tracked tech theme, plus one per monitored client. Nothing is hardcoded
    to a fixed subset: which of these actually becomes a Digest section is decided fresh each run from real
    evidence in the corpus, so the Digest composition changes as coverage changes, day to day."""
    pool = [(theme.key, theme.label, theme.pattern) for theme in TECH_THEMES]
    for canonical, config in CLIENT_CONFIG.items():
        names = [canonical, *config.aliases]
        pattern = "|".join(re.escape(n) for n in names)
        pool.append((f"client_{canonical.lower()}", f"{canonical} in the news", pattern))
    return pool


async def _digest_candidates(session: AsyncSession) -> dict[str, tuple[int, list[DigestCandidate]]]:
    rows = await _accepted(session)
    clients = await _clients_of(session)
    output: dict[str, tuple[int, list[DigestCandidate]]] = {}
    for key, _label, pattern in _digest_pool():
        rx = _rx(pattern)
        candidates: list[DigestCandidate] = []
        article_count = 0
        for row in rows:
            if (row["source_class"] or "").lower() == "research":
                continue
            terms = _terms_in(rx, row["fields"])
            if not terms:
                continue                                   # no verbatim evidence anywhere -> never included
            title = row["fields"].get("title") or ""
            summary = row["fields"].get("summary") or ""
            title_match = bool(rx.search(title))
            summary_match = bool(rx.search(summary))
            signal = _signal(row, clients, terms)
            if signal is None:
                continue
            article_count += 1
            # Title/summary relevance is a ranking boost, not a filter: raw-text-only evidence is still real
            # evidence (find_spans above already proved a verbatim quote exists) and stays eligible, just
            # scored lower so title/summary hits are preferred when both are available.
            match_score = 1.0 if title_match else 0.75 if summary_match else 0.4
            candidates.append(rank_candidate(DigestCandidate(
                signal=signal, theme=key, tier=row["tier"] or "TIER_3",
                reliability=float(row["reliability_score"] or 0.5),
                title_summary_match=match_score,
            )))
        output[key] = (article_count, candidates)
    return output


async def morning_digest(session: AsyncSession, *, per_section: int = 5, top_n: int = 5) -> MorningDigest:
    """Deterministic, evidence-backed Digest over the accepted article corpus. Which themes/clients appear is
    picked fresh each run (top N by evidence volume and strength), not a fixed hardcoded list — so the Digest
    varies as the corpus does instead of always showing the same "robotics/AI" sections."""
    theme_candidates = await _digest_candidates(session)
    labels = {key: label for key, label, _pattern in _digest_pool()}
    evidenced = [key for key, (count, _c) in theme_candidates.items() if count > 0]
    evidenced.sort(key=lambda key: (theme_candidates[key][0],
                                    max((c.score for c in theme_candidates[key][1]), default=0.0)), reverse=True)
    chosen_keys = evidenced[:DIGEST_MAX_SECTIONS]

    sections: list[DigestSection] = []
    selected: list[DigestCandidate] = []
    for key in chosen_keys:
        article_count, candidates = theme_candidates[key]
        chosen = select_diverse(candidates, per_section, max_per_publisher=2, already_selected=selected)
        selected.extend(chosen)
        sections.append(DigestSection(key=key, label=labels[key],
                                      status="EVIDENCE_FOUND" if article_count else "NO_EVIDENCE_IN_STORED_CORPUS",
                                      article_count=article_count, signals=[item.signal for item in chosen]))

    worth_candidates = select_diverse(selected, max(3, min(top_n, 5)), max_per_publisher=1)
    worth_your_attention = [DigestItem(kind="article", title=item.signal.title, url=item.signal.url,
                                       published_at=item.signal.published_at,
                                       source=item.signal.publisher or item.signal.source, theme=item.theme,
                                       evidence=item.signal.evidence) for item in worth_candidates]

    return MorningDigest(
        generated_at=datetime.now(timezone.utc), sections=sections, researchers=[],
        worth_your_attention=worth_your_attention,
        note="A deterministic saved query over the stored article corpus (PDF s.11), not a new pipeline stage. "
             "Every item requires verbatim stored evidence; title/summary relevance, source reliability and freshness rank results, "
             "and publisher diversity is a soft preference (filled from the next-best items when one publisher dominates). "
             f"Sections are the top {DIGEST_MAX_SECTIONS} tech themes or monitored clients by evidence that day (from the full theme+client "
             "pool), not a fixed list, so the mix changes as coverage changes. Researchers remain in the separate Research view.",
    )
