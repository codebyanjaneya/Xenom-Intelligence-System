"""Source status: configured vs healthy vs producing, kept as three separate facts (read-only).

  configured   = the source exists (`sources` row) and is_active says whether ingestion runs it;
  health       = the latest measured `source_health` row (HEALTHY / UNHEALTHY / UNKNOWN when never measured);
  productivity = what the source actually yielded, from stored articles: accepted / rejected, acceptance rate,
                 latest accepted article, and the clients its accepted articles belong to.
A source is never called healthy because it is configured, and never called productive because it is healthy.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Article
from app.models.entities import Entity, EntityMention
from app.models.sources import Source, SourceHealth


class SourceStatus(BaseModel):
    source_id: str
    name: str
    source_type: str | None
    tier: str
    url: str
    configured_active: bool
    health_status: str  # HEALTHY | UNHEALTHY | UNKNOWN (never measured)
    last_checked_at: datetime | None
    last_health_error: str | None
    last_latency_ms: int | None
    consecutive_failures: int
    productivity_state: str  # PRODUCING | NO_ACCEPTED | NO_ARTICLES | INACTIVE
    accepted: int
    rejected: int
    acceptance_rate: float | None  # accepted / (accepted + rejected); None with no articles
    latest_accepted_published_at: datetime | None
    latest_fetched_at: datetime | None
    clients: list[str]


class SourceStatusReport(BaseModel):
    total: int
    by_health: dict[str, int]
    by_productivity: dict[str, int]
    sources: list[SourceStatus]
    note: str


async def source_status_report(session: AsyncSession) -> SourceStatusReport:
    sources = (await session.execute(select(Source).order_by(Source.name))).scalars().all()

    counts = {
        r[0]: r[1:] for r in (await session.execute(
            select(
                Article.source_id,
                func.count().filter(Article.processing_status == "accepted"),
                func.count().filter(Article.processing_status == "rejected"),
                func.max(Article.published_at).filter(Article.processing_status == "accepted"),
                func.max(Article.fetched_at),
            ).group_by(Article.source_id)
        )).all()
    }

    clients: dict = defaultdict(set)
    for source_id, key in (await session.execute(
        select(Article.source_id, Entity.canonical_key)
        .join(EntityMention, EntityMention.article_id == Article.id)
        .join(Entity, Entity.id == EntityMention.entity_id)
        .where(Article.processing_status == "accepted", Entity.entity_type == "ORG",
               Entity.canonical_key != "xenom technology")
        .distinct()
    )).all():
        clients[source_id].add(key)

    health: dict = defaultdict(list)  # newest first
    for row in (await session.execute(select(SourceHealth).order_by(SourceHealth.checked_at.desc()))).scalars():
        health[row.source_id].append(row)

    out: list[SourceStatus] = []
    for s in sources:
        acc, rej, latest_pub, latest_fetch = counts.get(s.id, (0, 0, None, None))
        rows = health.get(s.id, [])
        failures = 0
        for r in rows:
            if r.status == "ok":
                break
            failures += 1
        latest = rows[0] if rows else None
        if latest is None:
            hs = "UNKNOWN"
        else:
            hs = "HEALTHY" if latest.status == "ok" else "UNHEALTHY"
        if not s.is_active:
            prod = "INACTIVE"
        elif acc > 0:
            prod = "PRODUCING"
        elif rej > 0:
            prod = "NO_ACCEPTED"
        else:
            prod = "NO_ARTICLES"
        out.append(SourceStatus(
            source_id=str(s.id), name=s.name, source_type=s.source_type, tier=s.tier, url=s.canonical_url,
            configured_active=s.is_active, health_status=hs,
            last_checked_at=latest.checked_at if latest else None,
            last_health_error=latest.error_message if latest and latest.status != "ok" else None,
            last_latency_ms=latest.latency_ms if latest else None, consecutive_failures=failures,
            productivity_state=prod, accepted=acc, rejected=rej,
            acceptance_rate=round(acc / (acc + rej), 3) if (acc + rej) else None,
            latest_accepted_published_at=latest_pub, latest_fetched_at=latest_fetch,
            clients=sorted(clients.get(s.id, ())),
        ))
    out.sort(key=lambda x: (-x.accepted, x.name))

    def tally(attr: str) -> dict[str, int]:
        d: dict[str, int] = defaultdict(int)
        for x in out:
            d[getattr(x, attr)] += 1
        return dict(sorted(d.items()))

    return SourceStatusReport(
        total=len(out), by_health=tally("health_status"), by_productivity=tally("productivity_state"), sources=out,
        note="Health comes only from measured fetch attempts (source_health); UNKNOWN means never measured. "
             "Productivity comes from stored articles. Neither is inferred from the other or from configuration.",
    )
