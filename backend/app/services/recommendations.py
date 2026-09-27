"""Recommended actions and Xenom capability matching (PDF s.9, s.14; open decision s.16.1).

* Recommendations persist the rule-derived actions of a client's opportunities as reviewable drafts (rationale, confidence
  computed from the evidence, the stored articles behind them). LLM-synthesised ones (origin ``llm``) are written by
  ``services.llm``. Nothing is published without a person changing the status.
* Capability matching: an opportunity is marked ``capability_match = VERIFIED`` only when a Xenom capability whose
  ``verification_status`` is VERIFIED is literally described by the opportunity's stated event. With no verified capability
  data stored (today) every opportunity stays UNVERIFIED, exactly as before.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import taxonomy as taxonomy_cfg
from app.models.architecture import Recommendation
from app.models.content import Article
from app.models.capability import XenomCapability
from app.models.sources import Source
from app.schemas.derived import Opportunity

_STOP = frozenset("and the for with from that this into over about services solutions platform".split())
_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]{3,}")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOP}


async def match_capabilities(session: AsyncSession, opportunities: list[Opportunity]) -> list[Opportunity]:
    if not opportunities:
        return opportunities
    caps = (await session.scalars(select(XenomCapability).where(XenomCapability.verification_status == "VERIFIED")
                                  .order_by(XenomCapability.name))).all()
    if not caps:
        return opportunities
    out = []
    for opp in opportunities:
        haystack = _tokens(f"{opp.title} {opp.rationale} {' '.join(e.snippet for e in opp.evidence)}")
        hit = next((c for c in caps if (name := _tokens(c.name)) and name <= haystack), None)
        out.append(opp.model_copy(update={"capability_match": "VERIFIED", "capability": hit.name}) if hit else opp)
    return out


def confidence_from_evidence(publishers: int, best_tier: str | None) -> float:
    """PDF s.13: confidence comes from the evidence (independent publishers, source tier), never from a model."""
    return round(min(0.9, 0.35 + 0.15 * max(publishers, 1)) * taxonomy_cfg.TIER_CONFIDENCE_FACTOR.get(best_tier or "TIER_3", 0.75), 2)


async def sync_client(session: AsyncSession, client: str) -> dict:
    """Persist the client's current rule-derived opportunity actions as draft recommendations (idempotent by opportunity id)."""
    from app.services.client_engine import client_intelligence
    intelligence = await client_intelligence(session, client)
    created = updated = 0
    for opp in intelligence.opportunities:
        ids = opp.article_ids
        rows = (await session.execute(select(Article.id, Source.tier, Source.id).outerjoin(Source, Source.id == Article.source_id)
                                      .where(Article.id.in_(ids)))).all() if ids else []
        tiers = [r[1] for r in rows if r[1]]
        best = min(tiers) if tiers else None  # TIER_1 < TIER_2 ...
        publishers = len({r[2] for r in rows if r[2]})
        confidence = confidence_from_evidence(publishers, best)
        basis = {"independent_publishers": publishers, "best_source_tier": best, "rule_id": opp.rule_id, "capability_match": opp.capability_match,
                 "opportunity_score": opp.opportunity_score}
        existing = await session.scalar(select(Recommendation).where(Recommendation.client_key == opp.client_key,
                                                                     Recommendation.opportunity_key == opp.opportunity_id,
                                                                     Recommendation.origin == "rule"))
        if existing is None:
            session.add(Recommendation(client_key=opp.client_key, opportunity_key=opp.opportunity_id, title=opp.title, action=opp.recommended_action,
                                       rationale=opp.rationale, confidence=confidence, confidence_basis=basis, article_ids=[str(i) for i in ids],
                                       origin="rule", generated=False, status="draft"))
            created += 1
        elif existing.status == "draft":  # a reviewed/published/dismissed decision is never overwritten
            existing.action, existing.rationale, existing.confidence, existing.confidence_basis = opp.recommended_action, opp.rationale, confidence, basis
            existing.article_ids = [str(i) for i in ids]
            updated += 1
    await session.commit()
    return {"client": client, "opportunities": len(intelligence.opportunities), "created": created, "updated": updated}
