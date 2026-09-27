"""Source registry service: validation orchestration, URL canonicalization,
duplicate prevention, and CRUD/deactivate. No network access.
"""

from __future__ import annotations

import uuid

from app.config.settings import get_settings
from urllib.parse import urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source, SourceHealth
from app.models.architecture import ConfigAudit
from app.repositories import sources as repo
from app.schemas.source import SourceCreate, SourceUpdate

_EDITABLE = ("name", "canonical_url", "feed_url", "source_type", "tier", "is_active", "source_class", "reliability_score",
             "priority", "config", "notes")


def snapshot(source: Source) -> dict:
    """The manager-editable state of a source (what versioning, audit and rollback operate on)."""
    return {k: getattr(source, k) for k in _EDITABLE}


def _record(session: AsyncSession, actor: str | None, action: str, source: Source, before: dict | None, after: dict | None) -> None:
    session.add(ConfigAudit(actor=actor, action=action, object_type="source", object_key=str(source.id), before=before, after=after))


class DuplicateSourceError(Exception):
    """Raised when a source with the same canonical URL already exists."""


def canonicalize_url(url: str) -> str:
    """Normalize a URL for duplicate comparison (syntax only, no fetching).

    Lowercases scheme/host, strips default ports and a trailing slash, and drops
    the fragment. Query is preserved and `www.` is intentionally NOT stripped
    (it can denote a different host).
    """
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


class FixtureSourceDisabledError(ValueError):
    """fixture_path is a development/test mechanism and is refused when ENVIRONMENT=production."""


def _refuse_fixture_in_production(fixture_path: str | None) -> None:
    if fixture_path and get_settings().environment.strip().lower() == "production":
        raise FixtureSourceDisabledError("Fixture-backed sources are disabled in production")


async def create_source(session: AsyncSession, payload: SourceCreate, actor: str | None = None) -> Source:
    _refuse_fixture_in_production(payload.fixture_path)
    canonical = canonicalize_url(payload.url)
    if await repo.get_by_canonical_url(session, canonical) is not None:
        raise DuplicateSourceError(canonical)

    source = Source(
        name=payload.name,
        canonical_url=canonical,
        feed_url=payload.feed_url,
        fixture_path=payload.fixture_path,
        source_type=payload.source_type.value,
        tier=payload.tier.value,
        is_active=payload.is_active,
        source_class=payload.source_class.value if payload.source_class else None,
        reliability_score=payload.reliability_score,
        priority=payload.priority,
        config=payload.config.model_dump(mode="json") if payload.config else None,
        notes=payload.notes,
    )
    await repo.add(session, source)
    await session.flush()
    _record(session, actor, "source.create", source, None, snapshot(source))
    await session.commit()
    await session.refresh(source)
    return source


async def update_source(
    session: AsyncSession, source_id: uuid.UUID, payload: SourceUpdate, actor: str | None = None
) -> Source | None:
    source = await repo.get(session, source_id)
    if source is None:
        return None
    before = snapshot(source)

    data = payload.model_dump(exclude_unset=True)
    _refuse_fixture_in_production(data.get("fixture_path"))

    if "url" in data and data["url"] is not None:
        canonical = canonicalize_url(data.pop("url"))
        existing = await repo.get_by_canonical_url(session, canonical)
        if existing is not None and existing.id != source.id:
            raise DuplicateSourceError(canonical)
        source.canonical_url = canonical

    if "source_type" in data and data["source_type"] is not None:
        source.source_type = data.pop("source_type").value
    if "tier" in data and data["tier"] is not None:
        source.tier = data.pop("tier").value

    for field in ("name", "feed_url", "fixture_path", "is_active", "reliability_score", "priority", "notes"):
        if field in data:
            setattr(source, field, data[field])
    if data.get("source_class") is not None:
        source.source_class = data["source_class"].value
    if payload.config is not None:
        source.config = payload.config.model_dump(mode="json")
    elif source.config is None or not source.config.get("managed"):
        source.config = {**(source.config or {}), "managed": True}  # registry sync must not undo a manager's edit
    _record(session, actor, "source.update", source, before, snapshot(source))
    await session.commit()
    await session.refresh(source)
    return source


async def deactivate_source(session: AsyncSession, source_id: uuid.UUID, actor: str | None = None) -> Source | None:
    """Safe deactivate (soft): sets is_active=False. Not a hard delete, so any
    future references stay intact."""
    source = await repo.get(session, source_id)
    if source is None:
        return None
    before = snapshot(source)
    source.is_active = False
    source.config = {**(source.config or {}), "managed": True}
    _record(session, actor, "source.deactivate", source, before, snapshot(source))
    await session.commit()
    await session.refresh(source)
    return source


async def get_source(session: AsyncSession, source_id: uuid.UUID) -> Source | None:
    return await repo.get(session, source_id)


async def list_sources(session: AsyncSession) -> list[Source]:
    return await repo.list_all(session)


async def get_latest_health(session: AsyncSession, source_id: uuid.UUID) -> SourceHealth | None:
    return await repo.latest_health(session, source_id)
