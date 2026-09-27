"""Source registry API (PDF s.6, s.12).

Reads require any authenticated user; mutations require ADMIN. Authorization is enforced by route-level dependencies
(require_admin / get_current_user) so it is not duplicated inside handlers. Every change is audited with the previous
state, so any source edit can be rolled back.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ingestion import AdapterError
from app.api.deps import get_current_user, require_admin
from app.config.clients import CLIENT_CONFIG
from app.config.source_registry import SourceSpec
from app.db.session import get_session, get_sessionmaker
from app.models import User
from app.models.architecture import ClientSource, ConfigAudit
from app.schemas.ingestion import IngestionResult
from app.schemas.source import (
    FetchConfig,
    SourceClass,
    SourceCreate,
    SourceHealthResponse,
    SourceOut,
    SourceTier,
    SourceType,
    SourceUpdate,
)
from app.services import sources as service
from app.services.ingestion import UnsupportedFixtureSourceError, ingest_source
from app.services.registry_ingestion import dry_run_source, run_source_row
from app.services.sources import DuplicateSourceError

router = APIRouter(prefix="/sources", tags=["sources"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
_DUPLICATE = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="A source with this canonical URL already exists",
)


class DryRunRequest(BaseModel):
    """Test before saving: either an existing source (``source_id``) or an unsaved definition."""

    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2048)
    source_type: SourceType | None = None
    tier: SourceTier = SourceTier.TIER_2
    feed_url: str | None = Field(default=None, max_length=2048)
    config: FetchConfig | None = None
    client: str | None = Field(default=None, max_length=40)


class ClientLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client: str = Field(max_length=40)
    priority: int = Field(default=0, ge=0, le=100)
    role: str = Field(default="primary", pattern="^(primary|secondary|reference|discovery)$")


class ClientLinkOut(ClientLink):
    is_active: bool = True


class RollbackRequest(BaseModel):
    audit_id: uuid.UUID


def _client_name(client: str) -> str:
    for name in CLIENT_CONFIG:
        if name.lower() == client.strip().lower():
            return name
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown client")


@router.post(
    "",
    response_model=SourceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_source(
    payload: SourceCreate, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> SourceOut:
    try:
        source = await service.create_source(session, payload, actor=user.email)
    except DuplicateSourceError:
        raise _DUPLICATE
    except service.FixtureSourceDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return SourceOut.model_validate(source)


@router.get("", response_model=list[SourceOut], dependencies=[Depends(get_current_user)])
async def list_sources(session: AsyncSession = Depends(get_session)) -> list[SourceOut]:
    sources = await service.list_sources(session)
    return [SourceOut.model_validate(s) for s in sources]


@router.post("/dry-run", dependencies=[Depends(require_admin)])
async def dry_run(payload: DryRunRequest, session: AsyncSession = Depends(get_session)) -> dict:
    """Fetch and parse a source and show what the relevance engine would accept or reject. Nothing is stored and no
    health row is written."""
    from app.services.registry_ingestion import spec_from_source

    client_key = None
    if payload.client:
        from app.config.clients import ENTITY_KEY_BY_CLIENT
        client_key = ENTITY_KEY_BY_CLIENT[_client_name(payload.client)]
    if payload.source_id:
        source = await service.get_source(session, payload.source_id)
        if source is None:
            raise _NOT_FOUND
        try:
            spec = spec_from_source(source, client_key)
        except AdapterError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    else:
        if not (payload.url and payload.config and payload.name):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name, url and config are required")
        SourceCreate(name=payload.name, url=payload.url, source_type=payload.source_type or SourceType.OTHER_PUBLIC, tier=payload.tier,
                     feed_url=payload.feed_url)  # same syntax validation as creation
        cfg = payload.config
        spec = SourceSpec(key="dry-run", client=client_key, name=payload.name, url=payload.url, fetch_mode=cfg.fetch_mode,
                          db_source_type=(payload.source_type or SourceType.OTHER_PUBLIC).value, tier=payload.tier.value,
                          source_kind=cfg.source_kind, feed_url=payload.feed_url, require_date=cfg.require_date, render=cfg.render,
                          link_pattern=cfg.link_pattern, query=cfg.query, max_results=cfg.max_results)
    return await dry_run_source(get_sessionmaker(), spec)


@router.get("/audit", dependencies=[Depends(get_current_user)])
async def source_audit(source_id: uuid.UUID | None = None, limit: int = Query(default=50, ge=1, le=200),
                       session: AsyncSession = Depends(get_session)) -> list[dict]:
    stmt = select(ConfigAudit).where(ConfigAudit.object_type == "source").order_by(ConfigAudit.created_at.desc(), ConfigAudit.id).limit(limit)
    if source_id:
        stmt = stmt.where(ConfigAudit.object_key == str(source_id))
    return [{"audit_id": str(r.id), "source_id": r.object_key, "actor": r.actor, "action": r.action, "created_at": r.created_at.isoformat(),
             "before": r.before, "after": r.after} for r in (await session.scalars(stmt)).all()]


@router.get("/{source_id}", response_model=SourceOut, dependencies=[Depends(get_current_user)])
async def get_source(
    source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> SourceOut:
    source = await service.get_source(session, source_id)
    if source is None:
        raise _NOT_FOUND
    return SourceOut.model_validate(source)


@router.patch("/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: uuid.UUID,
    payload: SourceUpdate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    try:
        source = await service.update_source(session, source_id, payload, actor=user.email)
    except DuplicateSourceError:
        raise _DUPLICATE
    except service.FixtureSourceDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if source is None:
        raise _NOT_FOUND
    return SourceOut.model_validate(source)


@router.delete("/{source_id}", response_model=SourceOut)
async def deactivate_source(
    source_id: uuid.UUID, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> SourceOut:
    source = await service.deactivate_source(session, source_id, actor=user.email)
    if source is None:
        raise _NOT_FOUND
    return SourceOut.model_validate(source)


@router.post("/{source_id}/rollback", response_model=SourceOut)
async def rollback_source(source_id: uuid.UUID, payload: RollbackRequest, user: User = Depends(require_admin),
                          session: AsyncSession = Depends(get_session)) -> SourceOut:
    """Restore the state a source had BEFORE the given audited change (recorded as a new audited change)."""
    source = await service.get_source(session, source_id)
    entry = await session.get(ConfigAudit, payload.audit_id)
    if source is None or entry is None or entry.object_type != "source" or entry.object_key != str(source_id):
        raise _NOT_FOUND
    if not entry.before:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The creation of a source has no earlier state to restore")
    before = service.snapshot(source)
    for field, value in entry.before.items():
        if field in service._EDITABLE:
            setattr(source, field, value)
    service._record(session, user.email, "source.rollback", source, before, service.snapshot(source))
    await session.commit()
    await session.refresh(source)
    return SourceOut.model_validate(source)


@router.get("/{source_id}/clients", response_model=list[ClientLinkOut], dependencies=[Depends(get_current_user)])
async def get_client_links(source_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> list[ClientLinkOut]:
    rows = (await session.scalars(select(ClientSource).where(ClientSource.source_id == source_id).order_by(ClientSource.client_key))).all()
    return [ClientLinkOut(client=r.client_key, priority=r.priority, role=r.role, is_active=r.is_active) for r in rows]


@router.put("/{source_id}/clients", response_model=list[ClientLinkOut])
async def set_client_links(source_id: uuid.UUID, links: list[ClientLink], user: User = Depends(require_admin),
                           session: AsyncSession = Depends(get_session)) -> list[ClientLinkOut]:
    """Which clients this source serves, with a per-client priority and role (PDF s.14 client_sources)."""
    source = await service.get_source(session, source_id)
    if source is None:
        raise _NOT_FOUND
    keys = [_client_name(link.client).lower() for link in links]
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Duplicate client in links")
    old = (await session.scalars(select(ClientSource).where(ClientSource.source_id == source_id))).all()
    before = {r.client_key: {"priority": r.priority, "role": r.role} for r in old}
    for row in old:
        await session.delete(row)
    await session.flush()
    for key, link in zip(keys, links):
        session.add(ClientSource(client_key=key, source_id=source_id, priority=link.priority, role=link.role))
    session.add(ConfigAudit(actor=user.email, action="source.clients", object_type="source", object_key=str(source_id),
                            before={"clients": before}, after={"clients": {k: {"priority": l.priority, "role": l.role} for k, l in zip(keys, links)}}))
    await session.commit()
    return [ClientLinkOut(client=k, priority=l.priority, role=l.role) for k, l in zip(keys, links)]


@router.get(
    "/{source_id}/health",
    response_model=SourceHealthResponse,
    dependencies=[Depends(get_current_user)],
)
async def get_source_health(
    source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> SourceHealthResponse:
    source = await service.get_source(session, source_id)
    if source is None:
        raise _NOT_FOUND
    latest = await service.get_latest_health(session, source_id)
    return SourceHealthResponse(source_id=source_id, latest=latest)


@router.post(
    "/{source_id}/fetch",
    response_model=IngestionResult,
    dependencies=[Depends(require_admin)],
)
async def fetch_source(
    source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> IngestionResult:
    """Manual run of one source. A source with a fetch configuration goes through the scheduler's registry path
    (health recorded, same adapters); a plain RSS/fixture source keeps the original single-feed path. Both end in
    ``process_documents``."""
    source = await service.get_source(session, source_id)
    if source is None:
        raise _NOT_FOUND
    if (source.config or {}).get("fetch_mode"):
        report = await run_source_row(get_sessionmaker(), source_id, resolve_media=False)
        if report.get("fetch_status") == "failed":
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(report.get("fetch_error"))[:200])
        if str(report.get("fetch_status", "")).startswith("skipped"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(report["fetch_status"]))
        return IngestionResult(source_id=source_id, adapter=report["mode"], documents_found=report.get("documents_fetched", 0),
                               articles_created=report.get("rows_created", 0), articles_deduplicated=report.get("documents_deduplicated", 0))
    try:
        return await ingest_source(session, source)
    except UnsupportedFixtureSourceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except AdapterError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
