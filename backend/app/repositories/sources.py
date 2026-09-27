"""Source persistence/read queries. Data access only — no business logic."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source, SourceHealth


async def add(session: AsyncSession, source: Source) -> Source:
    session.add(source)
    await session.flush()
    return source


async def get(session: AsyncSession, source_id: uuid.UUID) -> Source | None:
    return await session.get(Source, source_id)


async def get_by_canonical_url(session: AsyncSession, canonical_url: str) -> Source | None:
    return await session.scalar(select(Source).where(Source.canonical_url == canonical_url))


async def list_all(session: AsyncSession) -> list[Source]:
    result = await session.scalars(select(Source).order_by(Source.created_at.desc()))
    return list(result)


async def latest_health(session: AsyncSession, source_id: uuid.UUID) -> SourceHealth | None:
    return await session.scalar(
        select(SourceHealth)
        .where(SourceHealth.source_id == source_id)
        .order_by(SourceHealth.checked_at.desc())
        .limit(1)
    )
