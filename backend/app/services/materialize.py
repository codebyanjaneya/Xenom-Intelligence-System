"""Persist the rule-derived intelligence into the existing insights / opportunities / alerts tables (PDF s.2, s.13, s.14).

Derived objects are computed from stored articles on demand (always current). This makes them reviewable records: insights start as
``draft`` and a person publishes or archives them; opportunities keep POTENTIAL/QUALIFIED/DISMISSED decisions; alerts are broadcast
rows linked to their insight. Idempotent (natural key = client + title), and a human decision is never overwritten by a refresh.
Each row's body lists the publisher URLs of its evidence so the trace audit can resolve it to stored text.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import get_all_clients
from app.models import Alert, Insight
from app.models.entities import Entity
from app.models.capability import Opportunity as OpportunityRow
from app.services.client_engine import client_intelligence, resolve_client

EVIDENCE_MARK = "Evidence:"


def _evidence_lines(evidence) -> str:
    urls = list(dict.fromkeys(e.source_url for e in evidence))
    return EVIDENCE_MARK + "\n" + "\n".join(f"- {u}" for u in urls)


async def materialize_client(session: AsyncSession, client: str) -> dict:
    key, name = resolve_client(client)
    entity = await session.scalar(select(Entity).where(Entity.canonical_key == key, Entity.entity_type == "ORG"))
    if entity is None:
        return {"client": name, "skipped": "client entity is not registered"}
    resp = await client_intelligence(session, name, limit=500)
    counts = {"insights_created": 0, "insights_refreshed": 0, "opportunities_created": 0, "opportunities_refreshed": 0,
              "alerts_created": 0}
    row_of: dict[str, Insight] = {}
    for ins in resp.insights:
        body = f"{ins.explanation}\n\nRule {ins.rule_id}; derived id {ins.insight_id}. Limitations: {' '.join(ins.limitations)}\n{_evidence_lines(ins.evidence)}"
        row = await session.scalar(select(Insight).where(Insight.client_entity_id == entity.id, Insight.title == ins.title,
                                                         Insight.insight_type == ins.insight_type))
        if row is None:
            row = Insight(client_entity_id=entity.id, title=ins.title, body=body, insight_type=ins.insight_type, priority=ins.strength, status="draft")
            session.add(row)
            counts["insights_created"] += 1
        elif row.status == "draft":
            row.body, row.priority = body, ins.strength
            counts["insights_refreshed"] += 1
        await session.flush()
        row_of[ins.insight_id] = row
    for opp in resp.opportunities:
        rationale = f"{opp.rationale}\n\n{opp.interpretation}\nRecommended next step (needs review): {opp.recommended_action}\n{_evidence_lines(opp.evidence)}"
        row = await session.scalar(select(OpportunityRow).where(OpportunityRow.client_entity_id == entity.id, OpportunityRow.title == opp.title))
        if row is None:
            session.add(OpportunityRow(client_entity_id=entity.id, title=opp.title, rationale=rationale, status="POTENTIAL",
                                       capability_match=opp.capability_match, opportunity_score=opp.opportunity_score, priority=opp.opportunity_score))
            counts["opportunities_created"] += 1
        elif row.status == "POTENTIAL":
            row.rationale, row.capability_match, row.opportunity_score = rationale, opp.capability_match, opp.opportunity_score
            counts["opportunities_refreshed"] += 1
    for alert in resp.alerts:
        body = f"{alert.body}\n{_evidence_lines(alert.evidence)}"
        exists = await session.scalar(select(Alert.id).where(Alert.user_id.is_(None), Alert.title == alert.title))
        if exists is None:
            linked = next((row_of[i].id for i in alert.insight_ids if i in row_of), None)
            session.add(Alert(user_id=None, insight_id=linked, title=alert.title, body=body, severity=alert.severity))
            counts["alerts_created"] += 1
    await session.commit()
    return {"client": name, **counts}


async def materialize_all(session: AsyncSession) -> list[dict]:
    out = []
    for name in get_all_clients():
        try:
            out.append(await materialize_client(session, name))
        except Exception as exc:  # noqa: BLE001 - one client never blocks the others
            await session.rollback()
            out.append({"client": name, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    return out
