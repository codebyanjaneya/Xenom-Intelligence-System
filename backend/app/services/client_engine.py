"""Client- and topic-level intelligence over stored articles (read-only, deterministic).

client view  = the client's accepted articles (by stored entity mentions, newest first) -> the same
               intelligence_from_hits pipeline as search -> insights / opportunities / alerts -> briefing.
topic view   = ranked search hits for a query -> the same pipeline.
`persist_briefing` is the only write path: it stores a computed briefing in the existing `briefings` table.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import get_all_clients
from app.models.content import Article
from app.models.delivery import Briefing as BriefingRow
from app.models.entities import Entity, EntityMention
from app.models.sources import Source
from app.schemas.article_search import ArticleSearchHit
from app.schemas.derived import Briefing
from app.services.article_evidence import search_with_evidence
from app.services.markup import strip_markup
from app.services.articles import _relevance_evidence
from app.services.derived_intelligence import build_briefing
from app.services.stored_intelligence import StoredIntelligenceResponse, intelligence_from_hits


class UnknownClient(ValueError):
    pass


def resolve_client(name: str) -> tuple[str, str]:
    """(canonical_key, canonical_name) for a configured client; case-insensitive."""
    for canonical in get_all_clients():
        if canonical.lower() == name.strip().lower():
            return canonical.lower(), canonical
    raise UnknownClient(f"unknown client: {name}")


async def client_hits(session: AsyncSession, client_key: str, *, limit: int = 100,
                      date_from: datetime | None = None, date_to: datetime | None = None) -> list[ArticleSearchHit]:
    statement = (
        select(Article, Source.name, Source.source_type, Source.tier)
        .outerjoin(Source, Article.source_id == Source.id)
        .where(Article.processing_status == "accepted", Article.quality_gate_passed.is_(True))
        .where(exists().where(EntityMention.article_id == Article.id, EntityMention.entity_id == Entity.id,
                              Entity.canonical_key == client_key, Entity.entity_type == "ORG"))
    )
    if date_from:
        statement = statement.where(Article.published_at >= date_from)
    if date_to:
        statement = statement.where(Article.published_at <= date_to)
    statement = statement.order_by(Article.published_at.desc().nullslast(), Article.id).limit(limit)
    hits = []
    for article, source_name, source_type, source_tier in (await session.execute(statement)).all():
        evidence = _relevance_evidence(article.score_reason)
        hits.append(ArticleSearchHit(
            article_id=article.id, title=strip_markup(article.title), summary=strip_markup(article.summary),
            url=article.canonical_url or article.url,
            published_at=article.published_at, client=(evidence or {}).get("entity"), source=source_name,
            source_class=(evidence or {}).get("source_type") or source_type, tier=source_tier, relevance=evidence,
            score=float(article.relevance_score or 0.0),
        ))
    return hits


async def client_intelligence(session: AsyncSession, client: str, *, limit: int = 100,
                              date_from: datetime | None = None, date_to: datetime | None = None,
                              as_of: datetime | None = None) -> StoredIntelligenceResponse:
    key, _name = resolve_client(client)
    hits = await client_hits(session, key, limit=limit, date_from=date_from, date_to=date_to)
    return await intelligence_from_hits(session, f"client:{key}", hits, as_of)


async def client_briefing(session: AsyncSession, client: str, **kw) -> Briefing:
    resp = await client_intelligence(session, client, **kw)
    return build_briefing(resp, resp.insights, resp.opportunities, resp.alerts,
                          {"client": resolve_client(client)[1]}, kw.get("as_of"))


async def topic_briefing(session: AsyncSession, query: str, *, limit: int = 20, as_of: datetime | None = None) -> Briefing:
    found = await search_with_evidence(session, query, limit=limit)
    resp = await intelligence_from_hits(session, found.query, found.results, as_of)
    return build_briefing(resp, resp.insights, resp.opportunities, resp.alerts, {"query": found.query}, as_of)


async def persist_briefing(session: AsyncSession, briefing: Briefing) -> BriefingRow:
    """Store a computed briefing (broadcast row, user_id NULL) in the existing `briefings` table. Re-running for the
    same scope and as_of date replaces the stored payload instead of adding a row."""
    day: date = (briefing.as_of or datetime.now(timezone.utc)).date()
    label = briefing.scope.get("client") or briefing.scope.get("query") or "briefing"
    title = f"Briefing: {label}"
    payload = briefing.model_dump(mode="json")
    row = await session.scalar(select(BriefingRow).where(BriefingRow.user_id.is_(None), BriefingRow.briefing_date == day,
                                                         BriefingRow.title == title))
    if row is None:
        row = BriefingRow(user_id=None, briefing_date=day, title=title, payload=payload)
        session.add(row)
    else:
        row.payload = payload
    await session.commit()
    await session.refresh(row)
    return row
