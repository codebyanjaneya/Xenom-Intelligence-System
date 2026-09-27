"""Traceability audit (needs no labels): every derived object must resolve to stored articles and verbatim text.

Checks, for one intelligence response:
  * every evidence span (entities, events, relationships, insights, opportunities, alerts) is a substring of the
    stored title/summary/raw_text of the article at its source_url, and that article is in the response;
  * every event/insight/opportunity/alert reference (article_ids, event_keys, entity_keys, insight_ids) exists;
  * every relationship and timeline entry points at an existing event (and entity).
`violations` must be empty. Read-only.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Article
from app.models.evidence import Claim


async def audit_response(session: AsyncSession, resp) -> dict:
    article_ids = {str(a.article_id) for a in resp.articles}
    by_url = {a.url: str(a.article_id) for a in resp.articles}
    texts: dict = {}
    if article_ids:
        rows = await session.execute(
            select(Article.id, Article.title, Article.summary, Article.raw_text).where(Article.id.in_([a.article_id for a in resp.articles]))
        )
        texts = {str(r[0]): [r[1] or "", r[2] or "", r[3] or ""] for r in rows.all()}
    event_keys = {e.event_key for e in resp.events}
    entity_keys = {e.canonical_key for e in resp.entities}
    insight_ids = {i.insight_id for i in resp.insights}
    violations: list[str] = []
    checked = {"evidence": 0, "references": 0}

    def check_evidence(owner: str, evidence: list) -> None:
        for ev in evidence:
            checked["evidence"] += 1
            aid = by_url.get(ev.source_url)
            if aid is None:
                violations.append(f"{owner}: evidence url not among response articles: {ev.source_url}")
            elif not any((ev.content_span or "") in part for part in texts.get(aid, [])):
                violations.append(f"{owner}: evidence span not found in stored text of {aid}")

    def check_refs(owner: str, kind: str, refs, allowed: set) -> None:
        for r in refs:
            checked["references"] += 1
            if str(r) not in allowed:
                violations.append(f"{owner}: unknown {kind} {r}")

    for e in resp.entities:
        check_evidence(f"entity {e.canonical_key}", e.evidence)
        check_refs(f"entity {e.canonical_key}", "article", e.article_ids, article_ids)
    for e in resp.events:
        check_evidence(f"event {e.event_key}", e.evidence)
        check_refs(f"event {e.event_key}", "article", e.article_ids, article_ids)
    for r in resp.relationships:
        check_evidence(f"relationship {r.entity_key}->{r.event_key}", r.evidence)
        check_refs("relationship", "event", [r.event_key], event_keys)
        check_refs("relationship", "entity", [r.entity_key], entity_keys)
    for t in resp.timeline:
        check_refs("timeline", "event", [t.event_key], event_keys)
        check_refs("timeline", "article", t.article_ids, article_ids)
    for i in resp.insights:
        check_evidence(f"insight {i.insight_id}", i.evidence)
        check_refs(f"insight {i.insight_id}", "event", i.event_keys, event_keys)
        check_refs(f"insight {i.insight_id}", "article", i.article_ids, article_ids)
        check_refs(f"insight {i.insight_id}", "entity", i.entity_keys, entity_keys)
    for o in resp.opportunities:
        check_evidence(f"opportunity {o.opportunity_id}", o.evidence)
        check_refs(f"opportunity {o.opportunity_id}", "event", o.event_keys, event_keys)
        check_refs(f"opportunity {o.opportunity_id}", "article", o.article_ids, article_ids)
        check_refs(f"opportunity {o.opportunity_id}", "insight", o.insight_ids, insight_ids)
    for a in resp.alerts:
        check_evidence(f"alert {a.alert_id}", a.evidence)
        check_refs(f"alert {a.alert_id}", "event", a.event_keys, event_keys)
        check_refs(f"alert {a.alert_id}", "article", a.article_ids, article_ids)
        check_refs(f"alert {a.alert_id}", "insight", a.insight_ids, insight_ids)
    return {"articles": len(article_ids), "events": len(event_keys), "insights": len(insight_ids),
            "opportunities": len(resp.opportunities), "alerts": len(resp.alerts), **checked, "violations": violations}


# ---- other evidence carriers: search hits, briefings, stored claims --------------------------------------------------

async def _article_for_url(session: AsyncSession, url: str):
    return await session.scalar(select(Article).where(or_(Article.canonical_url == url, Article.url == url)))


def _report(checked: int, violations: list[str]) -> dict:
    return {"evidence": checked, "references": 0, "violations": violations}


async def audit_evidence_items(session: AsyncSession, owner: str, items) -> dict:
    """Each item (object or dict with source_url + content_span) must resolve: URL -> stored article -> exact text."""
    violations, checked = [], 0
    for it in items:
        url = it["source_url"] if isinstance(it, dict) else it.source_url
        span = (it["content_span"] if isinstance(it, dict) else it.content_span) or ""
        checked += 1
        art = await _article_for_url(session, url)
        if art is None:
            violations.append(f"{owner}: no stored article for {url}")
        elif "news.google.com" in url:
            violations.append(f"{owner}: discovery wrapper URL used as provenance: {url}")
        elif not any(span in (part or "") for part in (art.title, art.summary, art.raw_text)):
            violations.append(f"{owner}: span not found in stored text of {url}")
    return _report(checked, violations)


async def audit_search_evidence(session: AsyncSession, results) -> dict:
    """Search-evidence hits: the URL is the canonical publisher URL and every span is text[start:end] of its field."""
    violations, checked = [], 0
    for hit in results:
        art = await session.get(Article, hit.article_id)
        if art is None:
            violations.append(f"search hit {hit.article_id}: article missing")
            continue
        if hit.url != (art.canonical_url or art.url) or "news.google.com" in hit.url:
            violations.append(f"search hit {hit.article_id}: url is not the canonical publisher URL")
        for sp in hit.spans:
            checked += 1
            if (getattr(art, sp.field) or "")[sp.start:sp.end] != sp.quote:
                violations.append(f"search hit {hit.article_id}: span offsets do not reproduce the quote")
    return _report(checked, violations)


async def audit_briefing(session: AsyncSession, briefing) -> dict:
    items = [e for k in briefing.key_events for e in k.evidence]
    items += [e for i in briefing.insights for e in i.evidence] + [e for o in briefing.opportunities for e in o.evidence]
    items += [e for a in briefing.alerts for e in a.evidence]
    return await audit_evidence_items(session, "briefing", items)


async def audit_stored_claims(session: AsyncSession) -> dict:
    """Persisted claims. Historical (fixture-era) claims fail here by design; new claims must pass."""
    items = []
    for claim in (await session.scalars(select(Claim))).all():
        for ev in claim.evidence_json or []:
            items.append(ev)
    return await audit_evidence_items(session, "claim", items)


async def audit_architecture(session: AsyncSession) -> dict:
    """Derived objects of the full architecture must resolve to stored, accepted articles and verbatim text:
    chunk offsets, label evidence, authorship, recommendations, recovered items and accepted-article provenance."""
    import json

    from sqlalchemy import text as sql_text

    from app.models.architecture import ArticleLabel, DocumentAuthor, DocumentChunk, Recommendation
    from app.models.entities import Entity

    violations: list[str] = []
    checked = 0
    arts = {a.id: a for a in (await session.scalars(select(Article))).all()}

    for a in arts.values():  # accepted-article provenance: a real publisher URL, never a discovery wrapper
        if a.processing_status == "accepted":
            checked += 1
            url = a.canonical_url or ""
            if not url or "news.google.com" in url:
                violations.append(f"accepted article {a.id}: no canonical publisher URL")
    for c in (await session.scalars(select(DocumentChunk))).all():
        checked += 1
        art = arts.get(c.article_id)
        if art is None or art.processing_status != "accepted":
            violations.append(f"chunk {c.id}: article missing or not accepted")
        elif (getattr(art, c.field) or "")[c.char_start:c.char_end] != c.text_:
            violations.append(f"chunk {c.id}: offsets do not reproduce the chunk text")
    for lab in (await session.scalars(select(ArticleLabel))).all():
        checked += 1
        art = arts.get(lab.article_id)
        if art is None or art.processing_status != "accepted":
            violations.append(f"label {lab.label} on {lab.article_id}: article missing or not accepted")
            continue
        ev = lab.evidence or {}
        if lab.method == "llm" and ev.get("quote") and ev["quote"] not in (getattr(art, ev.get("field", "summary")) or ""):
            violations.append(f"llm label {lab.label} on {lab.article_id}: quote not in stored text")
        if lab.method == "rule" and ev.get("basis") in ("competitor named in the client profile", "model-name dictionary", "partnership terms",
                                                         "launch / expansion / order terms", "financial terms", "paper marker"):
            hay = " ".join(filter(None, [art.title, art.summary])).lower()
            missing = [t for t in ev.get("terms", []) if t.lower() not in hay]
            if missing:
                violations.append(f"rule label {lab.label} on {lab.article_id}: terms not in stored text: {missing[:2]}")
    person_ids = set((await session.scalars(select(Entity.id).where(Entity.entity_type == "PERSON"))).all())
    for da in (await session.scalars(select(DocumentAuthor))).all():
        checked += 1
        if arts.get(da.article_id) is None or arts[da.article_id].processing_status != "accepted" or da.entity_id not in person_ids:
            violations.append(f"document_author {da.id}: article not accepted or entity is not a PERSON")
    arts_by_str = {str(k): a for k, a in arts.items()}
    claim_ids = {str(i) for i in (await session.scalars(select(Claim.id))).all()}
    for rec in (await session.scalars(select(Recommendation))).all():
        for aid in rec.article_ids or []:
            checked += 1
            if aid not in arts_by_str:
                violations.append(f"recommendation {rec.id}: unknown article {aid}")
        for cid in rec.claim_ids or []:
            checked += 1
            if cid not in claim_ids:
                violations.append(f"recommendation {rec.id}: unknown claim {cid}")
        if rec.origin == "llm" and not rec.claim_ids:
            violations.append(f"recommendation {rec.id}: generated text without cited claims")
    for aid, a in arts.items():
        try:
            evidence = json.loads(a.score_reason or "{}")
        except ValueError:
            violations.append(f"article {aid}: score_reason is not valid JSON")
            continue
        if evidence.get("recovered_by") and a.processing_status != "accepted":
            violations.append(f"article {aid}: recovered but not accepted")
    for table, column in (("insights", "body"), ("opportunities", "rationale"), ("alerts", "body")):
        for rid, content in (await session.execute(sql_text(f"select id, {column} from {table}"))).all():
            marker = (content or "").partition("Evidence:")[2]
            if not marker:
                continue  # rows written by the LLM path cite claims instead (checked through insight_claims)
            for url in [l[2:].strip() for l in marker.splitlines() if l.startswith("- ")]:
                checked += 1
                if "news.google.com" in url:
                    violations.append(f"{table} {rid}: discovery wrapper used as provenance")
                elif await _article_for_url(session, url) is None:
                    violations.append(f"{table} {rid}: evidence url is not a stored article: {url}")
    rows = (await session.execute(sql_text("select id, target_id from review_items where object_type in ('recall_candidate','llm_adjudication')"))).all()
    for rid, target in rows:
        checked += 1
        if target not in arts:
            violations.append(f"review item {rid}: target article missing")
    return _report(checked, violations)
