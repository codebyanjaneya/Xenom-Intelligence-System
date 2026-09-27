from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, desc, nullslast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, Entity, EntityMention, Source


async def add(session: AsyncSession, article: Article) -> Article:
    session.add(article)
    await session.flush()
    return article


async def find_duplicate(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    normalized_url: str,
    content_hash: str | None,
) -> Article | None:
    signals = [Article.normalized_url == normalized_url]
    if content_hash is not None:
        signals.append(Article.content_hash == content_hash)
    return await session.scalar(
        select(Article)
        .where(and_(Article.source_id == source_id, or_(*signals)))
        .order_by(Article.created_at.asc())
        .limit(1)
    )


async def list_articles(
    session: AsyncSession,
    *,
    source_id: uuid.UUID | None = None,
    source_name: str | None = None,
    entity_key: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[Article, str | None, str | None]]:
    statement = select(Article, Source.name).outerjoin(Source, Article.source_id == Source.id)

    # Filter for accepted articles only (quality_gate_passed=true)
    statement = statement.where(Article.quality_gate_passed == True)

    if source_id is not None:
        statement = statement.where(Article.source_id == source_id)
    if source_name is not None:
        statement = statement.where(Source.name == source_name)
    if entity_key is not None:
        # Restrict the ARTICLES to the requested client (previously only the label list was filtered)
        statement = statement.where(
            Article.id.in_(
                select(EntityMention.article_id)
                .join(Entity, EntityMention.entity_id == Entity.id)
                .where(Entity.canonical_key == entity_key, Entity.entity_type == "ORG")
            )
        )

    # IMPORTANT: Collect entities BEFORE limiting results
    statement = (
        statement
        .order_by(
            nullslast(desc(Article.published_at)),
            nullslast(desc(Article.fetched_at)),
            desc(Article.created_at),
            Article.id,  # total order: offset pages must be stable and disjoint
        )
        .limit(limit)
        .offset(offset)
    )
    rows = await session.execute(statement)
    articles = [(article, source_name_val) for article, source_name_val in rows.all()]

    if not articles:
        return []

    article_ids = [a[0].id for a in articles]

    # Fetch all entities for all articles BEFORE limiting
    entity_statement = (
        select(EntityMention.article_id, Entity.canonical_name)
        .join(Entity, EntityMention.entity_id == Entity.id)
        .where(EntityMention.article_id.in_(article_ids))
        .where(Entity.entity_type == "ORG")  # Only client organizations
    )
    if entity_key is not None:
        entity_statement = entity_statement.where(Entity.canonical_key == entity_key)

    entity_rows = await session.execute(entity_statement)
    entity_map: dict[uuid.UUID, list[str]] = {}
    for article_id, entity_name in entity_rows:
        if article_id not in entity_map:
            entity_map[article_id] = []
        entity_map[article_id].append(entity_name)

    # Return list of (article, source_name, entity_names_list) tuples
    # Entity list is sorted for consistent ordering
    # Limit the final result to requested amount AFTER entity mapping is complete
    result = [
        (article, source_name_val, sorted(entity_map.get(article.id, [])))
        for article, source_name_val in articles
    ]

    return result[:limit]

def title_key(title: str | None) -> str:
    """Headline identity for syndication checks: drops a trailing ' - Publisher' and punctuation."""
    value = (title or "").strip()
    if " - " in value:
        head, tail = value.rsplit(" - ", 1)
        if len(tail) <= 40 and len(head) >= 15:
            value = head
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


async def find_by_url_anywhere(session: AsyncSession, *, url: str, normalized_url: str) -> Article | None:
    """Global (cross-source) URL check. ``articles.url`` is unique, so this avoids IntegrityError."""
    return await session.scalar(
        select(Article).where(or_(Article.url == url, Article.normalized_url == normalized_url)).limit(1)
    )


async def find_cross_source_duplicate(
    session: AsyncSession,
    *,
    article_id: uuid.UUID,
    urls: list[str],
    title: str | None,
    published_at: datetime | None,
) -> Article | None:
    """Same story already ACCEPTED from another source.

    Matches on the publisher URL (after Google-wrapper resolution) or on an identical
    headline within +/-3 days (syndicated copies). In-source dedupe semantics are unchanged.
    """
    urls = [u for u in urls if u]
    if urls:
        hit = await session.scalar(
            select(Article)
            .where(
                Article.id != article_id,
                Article.quality_gate_passed == True,  # noqa: E712
                or_(Article.url.in_(urls), Article.canonical_url.in_(urls), Article.normalized_url.in_(urls)),
            )
            .limit(1)
        )
        if hit is not None:
            return hit
    key = title_key(title)
    if not key or published_at is None:
        return None
    window = timedelta(days=3)
    candidates = await session.scalars(
        select(Article)
        .where(
            Article.id != article_id,
            Article.quality_gate_passed == True,  # noqa: E712
            Article.published_at >= published_at - window,
            Article.published_at <= published_at + window,
        )
        .limit(500)
    )
    for candidate in candidates:
        if title_key(candidate.title) == key:
            return candidate
    return None
