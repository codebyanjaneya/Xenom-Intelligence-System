"""Live/continuous-update visibility: scheduler state (this process) plus what the database shows (read-only).

The scheduler state is in-memory and describes this backend process; the freshness figures come from stored rows,
so a stale corpus is visible even when the scheduler is off or was never started.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import get_all_clients
from app.models.content import Article
from app.models.entities import Entity, EntityMention
from app.models.sources import Source, SourceHealth
from app.services.background_scheduler import get_scheduler


class ClientFreshness(BaseModel):
    client: str
    accepted_articles: int
    latest_published_at: datetime | None
    latest_ingested_at: datetime | None
    hours_since_latest_ingest: float | None


class PipelineStatus(BaseModel):
    scheduler: dict
    latest_article_ingested_at: datetime | None
    hours_since_latest_ingest: float | None
    health_checks_recorded: int
    latest_health_check_at: datetime | None
    clients: list[ClientFreshness]
    sources_total: int
    sources_active: int
    accepted_total: int
    rejected_total: int
    rejection_reasons: dict[str, int]  # stored rejected rows by stage:reason
    last_cycle: dict | None  # counts of the last scheduler cycle in this process
    next_cycle_at: str | None
    continuous_updates: str  # ACTIVE | STALE | SCHEDULER_OFF | NO_DATA, with the reason in `explanation`
    explanation: str


STALE_AFTER_MINUTES_FACTOR = 3  # more than 3 scheduler intervals without a new stored row


async def pipeline_status(session: AsyncSession, now: datetime | None = None) -> PipelineStatus:
    now = now or datetime.now(timezone.utc)
    sched = get_scheduler().status()
    newest = await session.scalar(select(func.max(Article.created_at)))
    n_health, last_health = (await session.execute(select(func.count(), func.max(SourceHealth.checked_at)))).one()

    rows = {
        r[0]: r[1:] for r in (await session.execute(
            select(Entity.canonical_key, func.count(func.distinct(Article.id)), func.max(Article.published_at),
                   func.max(Article.created_at))
            .join(EntityMention, EntityMention.entity_id == Entity.id)
            .join(Article, Article.id == EntityMention.article_id)
            .where(Article.processing_status == "accepted", Entity.entity_type == "ORG")
            .group_by(Entity.canonical_key)
        )).all()
    }

    def hours(ts: datetime | None) -> float | None:
        return round((now - ts).total_seconds() / 3600, 1) if ts else None

    clients = []
    for name in get_all_clients():
        count, pub, ing = rows.get(name.lower(), (0, None, None))
        clients.append(ClientFreshness(client=name, accepted_articles=count, latest_published_at=pub,
                                       latest_ingested_at=ing, hours_since_latest_ingest=hours(ing)))

    h = hours(newest)
    limit_h = sched["interval_minutes"] * STALE_AFTER_MINUTES_FACTOR / 60
    if newest is None:
        state, why = "NO_DATA", "No articles are stored."
    elif not sched["enabled"]:
        state, why = "SCHEDULER_OFF", f"Scheduler disabled in this process; newest stored article was ingested {h} h ago."
    elif h is not None and h > limit_h:
        state, why = "STALE", f"Scheduler enabled but nothing new was stored for {h} h (limit {limit_h} h)."
    else:
        state, why = "ACTIVE", f"Scheduler enabled; newest stored article was ingested {h} h ago."
    src_total, src_active = (await session.execute(select(func.count(), func.count().filter(Source.is_active)))).one()
    acc, rej = (await session.execute(select(func.count().filter(Article.processing_status == "accepted"),
                                             func.count().filter(Article.processing_status == "rejected")))).one()
    reasons: dict[str, int] = {}
    for stage, reason, n in (await session.execute(text(
        "select coalesce(score_reason::json->>'stage','?'), coalesce(score_reason::json->>'reason','?'), count(*) "
        "from articles where processing_status='rejected' and score_reason like '{%' group by 1,2 order by 3 desc"))).all():
        reasons[f"{stage}:{reason}"] = n
    cycle = sched.get("last_summary")
    last_cycle = None if not cycle else {
        k: cycle.get(k) for k in ("started_at", "completed_at", "duration_seconds", "total_fetched", "total_accepted",
                                  "total_rejected", "clients_processed")} | {"failed_sources": len(cycle.get("failed_sources", []))}
    return PipelineStatus(rejection_reasons=reasons, sources_total=src_total, sources_active=src_active, accepted_total=acc, rejected_total=rej,
                          last_cycle=last_cycle, next_cycle_at=sched.get("next_run_at"), scheduler=sched, latest_article_ingested_at=newest, hours_since_latest_ingest=h,
                          health_checks_recorded=n_health, latest_health_check_at=last_health, clients=clients,
                          continuous_updates=state, explanation=why)
