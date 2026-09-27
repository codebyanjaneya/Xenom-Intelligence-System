"""Client profile administration (PDF s.12): versioned profiles, impact preview, rollback, suggest-then-confirm, audit.

Reads need any authenticated user; every mutation needs ADMIN. Nothing here changes stored data except through a saved,
audited profile version (or an explicit re-evaluation job).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.config.clients import get_all_clients
from app.db.session import get_session, get_sessionmaker
from app.models import User
from app.models.architecture import ConfigAudit, ProfileSuggestion
from app.services import profile_impact as impact
from app.services import profiles as prof

router = APIRouter(prefix="/profiles", tags=["profiles"])


class ProfileVersionOut(BaseModel):
    version: int
    is_active: bool
    created_by: str | None
    change_note: str | None
    reverted_from: int | None
    created_at: datetime


class ProfileOut(BaseModel):
    client: str
    version: int
    origin: str  # "saved" or "code" (fallback: no saved version yet)
    data: prof.ProfileData
    versions: list[ProfileVersionOut]


class ProfileSave(BaseModel):
    data: prof.ProfileData
    note: str | None = Field(default=None, max_length=500)


class RollbackRequest(BaseModel):
    version: int = Field(ge=1)


class SuggestionOut(BaseModel):
    id: uuid.UUID
    client_key: str
    kind: str
    value: str
    reason: str
    example_article_ids: list[str] | None
    method: str
    status: str
    created_at: datetime


class AuditOut(BaseModel):
    id: uuid.UUID
    actor: str | None
    action: str
    object_type: str
    object_key: str
    created_at: datetime
    changes: dict | None = None


def _resolve(client: str) -> str:
    try:
        return prof.resolve(client)[1]
    except prof.UnknownProfileClient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown client")


def _versions(rows) -> list[ProfileVersionOut]:
    return [ProfileVersionOut(version=r.version, is_active=r.is_active, created_by=r.created_by, change_note=r.change_note,
                              reverted_from=r.reverted_from, created_at=r.created_at) for r in rows]


async def _profile_out(session: AsyncSession, name: str) -> ProfileOut:
    version, data, origin = await prof.effective_profile(session, name)
    return ProfileOut(client=name, version=version, origin=origin, data=data, versions=_versions(await prof.list_versions(session, name)))


@router.get("", dependencies=[Depends(get_current_user)])
async def list_profiles(session: AsyncSession = Depends(get_session)) -> list[dict]:
    out = []
    for name in get_all_clients():
        version, _data, origin = await prof.effective_profile(session, name)
        out.append({"client": name, "version": version, "origin": origin})
    return out


@router.get("/audit", response_model=list[AuditOut], dependencies=[Depends(get_current_user)])
async def audit_trail(limit: int = Query(default=50, ge=1, le=200), object_type: str | None = Query(default=None, max_length=60),
                      session: AsyncSession = Depends(get_session)) -> list[AuditOut]:
    stmt = select(ConfigAudit).order_by(ConfigAudit.created_at.desc(), ConfigAudit.id).limit(limit)
    if object_type:
        stmt = stmt.where(ConfigAudit.object_type == object_type)
    return [AuditOut(id=r.id, actor=r.actor, action=r.action, object_type=r.object_type, object_key=r.object_key, created_at=r.created_at,
                     changes=prof.diff(r.before or {}, r.after or {}) if r.before is not None and r.after is not None else r.after)
            for r in (await session.scalars(stmt)).all()]


@router.get("/{client}", response_model=ProfileOut, dependencies=[Depends(get_current_user)])
async def get_profile(client: str, session: AsyncSession = Depends(get_session)) -> ProfileOut:
    return await _profile_out(session, _resolve(client))


@router.put("/{client}", response_model=ProfileOut)
async def save_profile(client: str, payload: ProfileSave, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)) -> ProfileOut:
    name = _resolve(client)
    await prof.save_profile(session, name, payload.data, actor=user.email, note=payload.note)
    return await _profile_out(session, name)


@router.post("/{client}/preview", dependencies=[Depends(require_admin)])
async def preview(client: str, payload: ProfileSave, session: AsyncSession = Depends(get_session)) -> dict:
    """Show how many stored articles the proposed profile would add or remove. Saves nothing."""
    return await impact.preview_impact(session, _resolve(client), payload.data)


@router.post("/{client}/rollback", response_model=ProfileOut)
async def rollback(client: str, payload: RollbackRequest, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)) -> ProfileOut:
    name = _resolve(client)
    try:
        await prof.rollback(session, name, payload.version, actor=user.email)
    except prof.ProfileVersionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return await _profile_out(session, name)


# ---- background re-evaluation of stored items, including rejected ones ------------------------------------------------
_JOBS: dict[str, dict] = {}


async def _run_job(job_id: str, name: str, actor: str | None) -> None:
    job = _JOBS[job_id]
    try:
        async with get_sessionmaker()() as session:
            job["result"] = await impact.reevaluate(session, name, actor=actor)
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 - reported on the job, never silent
        job["status"], job["error"] = "failed", f"{type(exc).__name__}: {str(exc)[:200]}"
    job["finished_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/{client}/reevaluate", status_code=status.HTTP_202_ACCEPTED)
async def start_reevaluation(client: str, user: User = Depends(require_admin)) -> dict:
    name = _resolve(client)
    running = [j for j in _JOBS.values() if j["client"] == name and j["status"] == "running"]
    if running:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A re-evaluation is already running for this client")
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {"job_id": job_id, "client": name, "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    for old in sorted(_JOBS, key=lambda k: _JOBS[k]["started_at"])[:-20]:
        _JOBS.pop(old, None)
    asyncio.create_task(_run_job(job_id, name, user.email))
    return _JOBS[job_id]


@router.get("/{client}/reevaluate/{job_id}", dependencies=[Depends(get_current_user)])
async def reevaluation_status(client: str, job_id: str) -> dict:
    job = _JOBS.get(job_id)
    if job is None or job["client"] != _resolve(client):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


# ---- suggest, then confirm --------------------------------------------------------------------------------------------
@router.get("/{client}/suggestions", response_model=list[SuggestionOut], dependencies=[Depends(get_current_user)])
async def list_suggestions(client: str, status_filter: str = Query(default="pending", alias="status", pattern="^(pending|accepted|dismissed)$"),
                           session: AsyncSession = Depends(get_session)) -> list[SuggestionOut]:
    key = _resolve(client).lower()
    rows = (await session.scalars(select(ProfileSuggestion).where(ProfileSuggestion.client_key == key, ProfileSuggestion.status == status_filter)
                                  .order_by(ProfileSuggestion.created_at.desc(), ProfileSuggestion.id))).all()
    return [SuggestionOut(id=r.id, client_key=r.client_key, kind=r.kind, value=r.value, reason=r.reason,
                          example_article_ids=r.example_article_ids, method=r.method, status=r.status, created_at=r.created_at) for r in rows]


@router.post("/{client}/suggestions/generate", response_model=list[SuggestionOut])
async def generate_suggestions(client: str, _user: User = Depends(require_admin),
                               session: AsyncSession = Depends(get_session)) -> list[SuggestionOut]:
    rows = await impact.generate_suggestions(session, _resolve(client))
    return [SuggestionOut(id=r.id, client_key=r.client_key, kind=r.kind, value=r.value, reason=r.reason,
                          example_article_ids=r.example_article_ids, method=r.method, status=r.status, created_at=r.created_at) for r in rows]


class Decision(BaseModel):
    accept: bool


@router.post("/{client}/suggestions/{suggestion_id}")
async def decide(client: str, suggestion_id: uuid.UUID, payload: Decision, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)) -> dict:
    key = _resolve(client).lower()
    row = await session.get(ProfileSuggestion, suggestion_id)
    if row is None or row.client_key != key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    try:
        row, version = await impact.decide_suggestion(session, suggestion_id, accept=payload.accept, actor=user.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return {"id": str(row.id), "status": row.status, "profile_version": version}
