"""XENOM Intelligence API endpoints.

Endpoints for intelligence pipeline execution, client configuration, and intelligence retrieval.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import accessible_clients, get_current_user, require_admin, require_client_access
from app.config.clients import CLIENT_CONFIG, get_all_clients
from app.db.session import get_session
from app.models import Source, User
from app.schemas.source import SourceOut, SourceTier
from app.services import sources as sources_service
from app.schemas.intelligence import IntelligenceRequest
from app.services.intelligence_pipeline import run_intelligence_pipeline
from app.models.delivery import Briefing as BriefingRow
from app.schemas.derived import Alert, Briefing, Insight, Opportunity
from app.schemas.domain import ClientSummaryView, CompetitorView, IndustryView, MorningDigest, ResearchView, ThemeView
from app.services.domain_intelligence import client_summary, competitor_view, industry_view, morning_digest, research_view, technology_view
from app.services.pipeline_status import PipelineStatus, pipeline_status
from app.services.source_status import SourceStatusReport, source_status_report
from app.services.client_engine import (
    UnknownClient, client_briefing, client_intelligence, persist_briefing, topic_briefing,
)
from app.services.stored_intelligence import StoredIntelligenceResponse, extract_from_stored
from app.config.scheduler import SCHEDULER_CONFIG
from app.db.session import get_sessionmaker
from app.services.registry_ingestion import google_specs, run_google_query

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class PipelineRequest(BaseModel):
    """Request to run intelligence pipeline."""

    client_canonical_name: str | None = None
    """Specific client to fetch intelligence for (or None for all configured clients)."""

    quality_gate_threshold: int | None = Field(default=None, ge=0, le=100)
    """Quality gate override (0-100); default is the scheduler's configured gate (40)."""


class PipelineResponse(BaseModel):
    """Result of intelligence pipeline execution."""

    fetched: int
    parsed: int
    cleaned: int
    deduplicated: int
    tagged_entity: int
    tagged_topic: int
    scored: int
    accepted: int
    rejected: int
    errors: list[str]
    clients_processed: list[str]
    timestamp: datetime


@router.post("/pipeline/run", response_model=PipelineResponse, dependencies=[Depends(require_admin)])
async def run_pipeline(request: PipelineRequest) -> PipelineResponse:
    """Manually run the configured Google News queries once (admin). Uses exactly the scheduler's path
    (registry source URLs, client context, health recording), never a separate implementation."""
    if request.client_canonical_name:
        if request.client_canonical_name not in CLIENT_CONFIG:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown client: {request.client_canonical_name}")
        clients = [request.client_canonical_name]
    else:
        clients = get_all_clients()
    gate = request.quality_gate_threshold if request.quality_gate_threshold is not None else SCHEDULER_CONFIG.quality_gate_threshold
    maker = get_sessionmaker()
    totals = {k: 0 for k in ("fetched", "parsed", "cleaned", "deduplicated", "tagged_entity", "tagged_topic", "scored", "accepted", "rejected")}
    keys = {"fetched": "documents_fetched", "parsed": "documents_parsed", "cleaned": "documents_cleaned",
            "deduplicated": "documents_deduplicated", "tagged_entity": "documents_tagged_entity",
            "tagged_topic": "documents_tagged_topic", "scored": "documents_scored", "accepted": "documents_accepted",
            "rejected": "documents_rejected"}
    errors: list[str] = []
    for client in clients:
        for _c, query, url in google_specs(client):
            try:
                report = await run_google_query(maker, client, query, url, gate=gate, resolve_media=True)
            except Exception as exc:  # noqa: BLE001 - isolated per query, bounded in the response
                errors.append(f"{client}/{query}: {str(exc)[:200]}")
                continue
            if report.get("fetch_status") == "failed":
                errors.append(f"{client}/{query}: {str(report.get('fetch_error'))[:200]}")
                continue
            for name, key in keys.items():
                totals[name] += int(report.get(key, 0) or 0)
    return PipelineResponse(**totals, errors=errors[:50], clients_processed=clients, timestamp=datetime.now(timezone.utc))


@router.get("/clients", response_model=list[str], dependencies=[Depends(get_current_user)])
async def list_configured_clients(current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[str]:
    """Return list of configured Xenom client organizations.

    Returns canonical names for all six configured clients.
    """
    return await accessible_clients(session, current_user)


@router.get(
    "/clients/{client_name}/config",
    dependencies=[Depends(get_current_user)],
)
async def get_client_configuration(client_name: str, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """Retrieve full configuration for a specific client.

    Includes keywords, technology interests, Google News queries, etc.
    """
    from app.config.clients import get_client_config

    client_name = await require_client_access(session, current_user, client_name)
    config = get_client_config(client_name)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client not found: {client_name}",
        )

    return {
        "canonical_name": config.canonical_name,
        "aliases": config.aliases,
        "keywords": config.keywords,
        "technology_keywords": config.technology_keywords,
        "industry_keywords": config.industry_keywords,
        "google_news_queries": config.google_news_queries,
        "trusted_sites": config.trusted_sites,
        "exclusion_keywords": config.exclusion_keywords,
        "description": config.description,
    }


@router.post("/extract", response_model=StoredIntelligenceResponse, dependencies=[Depends(require_admin)])
async def extract_intelligence(
    payload: IntelligenceRequest, session: AsyncSession = Depends(get_session)
) -> StoredIntelligenceResponse:
    """DB-backed: search stored articles -> verbatim evidence -> entity context. No fixtures, no LLM."""
    try:
        return await extract_from_stored(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


# ---- client / topic intelligence over stored articles (read-only, deterministic) ---------------------------------

async def _client_view(session, client, limit, date_from, date_to, as_of, current_user: User | None = None) -> StoredIntelligenceResponse:
    try:
        if current_user is not None:
            client = await require_client_access(session, current_user, client)
        return await client_intelligence(session, client, limit=limit, date_from=date_from, date_to=date_to, as_of=as_of)
    except UnknownClient as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


_LIMIT = Query(default=100, ge=1, le=500)


@router.get("/clients/{client}/view", response_model=StoredIntelligenceResponse, dependencies=[Depends(get_current_user)])
async def client_view(client: str, limit: int = _LIMIT, date_from: datetime | None = None, date_to: datetime | None = None,
                      as_of: datetime | None = None, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    """Everything derived for one client: articles, entities, events, relationships, timeline, insights,
    opportunities and alerts, each traceable to stored articles and verbatim evidence."""
    return await _client_view(session, client, limit, date_from, date_to, as_of, current_user)


@router.get("/clients/{client}/insights", response_model=list[Insight], dependencies=[Depends(get_current_user)])
async def client_insights(client: str, limit: int = _LIMIT, date_from: datetime | None = None,
                          date_to: datetime | None = None, as_of: datetime | None = None,
                          session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    return (await _client_view(session, client, limit, date_from, date_to, as_of, current_user)).insights


@router.get("/clients/{client}/opportunities", response_model=list[Opportunity], dependencies=[Depends(get_current_user)])
async def client_opportunities(client: str, limit: int = _LIMIT, date_from: datetime | None = None,
                               date_to: datetime | None = None, as_of: datetime | None = None,
                               session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    return (await _client_view(session, client, limit, date_from, date_to, as_of, current_user)).opportunities


@router.get("/clients/{client}/alerts", response_model=list[Alert], dependencies=[Depends(get_current_user)])
async def client_alerts(client: str, limit: int = _LIMIT, date_from: datetime | None = None,
                        date_to: datetime | None = None, as_of: datetime | None = None,
                        session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    return (await _client_view(session, client, limit, date_from, date_to, as_of, current_user)).alerts


@router.get("/clients/{client}/briefing", response_model=Briefing, dependencies=[Depends(get_current_user)])
async def get_client_briefing(client: str, limit: int = _LIMIT, date_from: datetime | None = None,
                              date_to: datetime | None = None, as_of: datetime | None = None,
                              session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    try:
        client = await require_client_access(session, current_user, client)
        return await client_briefing(session, client, limit=limit, date_from=date_from, date_to=date_to, as_of=as_of)
    except UnknownClient as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/briefing", response_model=Briefing, dependencies=[Depends(get_current_user)])
async def get_topic_briefing(q: str = Query(min_length=1, max_length=500, pattern=r"^[^\x00]+$"), limit: int = Query(default=20, ge=1, le=50),
                             as_of: datetime | None = None, session: AsyncSession = Depends(get_session)):
    try:
        return await topic_briefing(session, q, limit=limit, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


class StoredBriefingOut(BaseModel):
    id: str
    briefing_date: str
    title: str | None
    payload: dict | None


@router.post("/clients/{client}/briefing/persist", response_model=StoredBriefingOut, dependencies=[Depends(require_admin)])
async def persist_client_briefing(client: str, limit: int = _LIMIT, as_of: datetime | None = None,
                                  session: AsyncSession = Depends(get_session), current_user: User = Depends(require_admin)):
    """The only write path: stores the computed briefing in the existing `briefings` table (broadcast row)."""
    try:
        row = await persist_briefing(session, await client_briefing(session, client, limit=limit, as_of=as_of))
    except UnknownClient as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return StoredBriefingOut(id=str(row.id), briefing_date=row.briefing_date.isoformat(), title=row.title, payload=row.payload)


@router.get("/briefings", response_model=list[StoredBriefingOut], dependencies=[Depends(get_current_user)])
async def list_stored_briefings(session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(BriefingRow).order_by(BriefingRow.briefing_date.desc(), BriefingRow.title))
    return [StoredBriefingOut(id=str(r.id), briefing_date=r.briefing_date.isoformat(), title=r.title, payload=r.payload)
            for r in rows]


# ---- cross-cutting views (all read-only, evidence-backed) ---------------------------------------------------------

@router.get("/clients/summary", response_model=ClientSummaryView, dependencies=[Depends(get_current_user)])
async def get_client_summary(session: AsyncSession = Depends(get_session)):
    """Per-client accepted/rejected counts straight from the database (the numbers the UI must show)."""
    return await client_summary(session)


@router.get("/technology", response_model=ThemeView, dependencies=[Depends(get_current_user)])
async def get_technology_view(per_theme: int = Query(default=5, ge=1, le=50), session: AsyncSession = Depends(get_session)):
    """AI / technology themes with verbatim evidence; themes without stored evidence are reported as such."""
    return await technology_view(session, per_theme)


@router.get("/industry", response_model=IndustryView, dependencies=[Depends(get_current_user)])
async def get_industry_view(per_term: int = Query(default=3, ge=1, le=20), session: AsyncSession = Depends(get_session)):
    return await industry_view(session, per_term)


@router.get("/research", response_model=ResearchView, dependencies=[Depends(get_current_user)])
async def get_research_view(session: AsyncSession = Depends(get_session)):
    """Research records (papers, labs, authors) or the explicit NO_RESEARCH_DATA_IN_STORED_CORPUS state."""
    return await research_view(session)


@router.get("/digest", response_model=MorningDigest, dependencies=[Depends(get_current_user)])
async def get_morning_digest(session: AsyncSession = Depends(get_session)):
    """Manager-requested morning digest (PDF s.11, s.15): a saved query over the existing search/index, not a
    new pipeline stage. Embodied AI, robotics, creative AI, AI models, global researchers/labs, and 3-5 items
    worth attention, each with its source, date, evidence and link."""
    return await morning_digest(session)


@router.get("/competitors", response_model=CompetitorView, dependencies=[Depends(get_current_user)])
async def get_competitor_view(client: str | None = None, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    if client:
        client = await require_client_access(session, current_user, client)
    return await competitor_view(session, client=client)


@router.get("/sources", response_model=SourceStatusReport, dependencies=[Depends(get_current_user)])
async def get_source_status(session: AsyncSession = Depends(get_session)):
    """Configured vs healthy vs producing, per source, from measured health rows and stored articles."""
    return await source_status_report(session)


@router.get("/pipeline/status", response_model=PipelineStatus, dependencies=[Depends(get_current_user)])
async def get_pipeline_status(session: AsyncSession = Depends(get_session)):
    """Continuous-update visibility: scheduler state in this process plus per-client freshness from the database."""
    return await pipeline_status(session)
