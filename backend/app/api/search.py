from __future__ import annotations

import uuid

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.db.session import get_session
from app.schemas.article_evidence import ArticleEvidenceResponse
from app.schemas.article_search import ArticleSearchResponse
from app.services.article_evidence import search_with_evidence
from app.services.retrieval import search as retrieve
from app.schemas.search import SearchRequest, SearchResponse
from app.services.search import SearchService
from app.providers.search import SearchProviderError

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse, dependencies=[Depends(require_admin)])
async def search(payload: SearchRequest) -> SearchResponse:
    try:
        return await SearchService().search(payload)
    except SearchProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )


@router.get("/articles", response_model=ArticleSearchResponse, dependencies=[Depends(get_current_user)])
async def search_stored_articles(
    q: str = Query(min_length=1, max_length=500, pattern=r"^[^\x00]+$"),
    entity_key: str | None = None,
    source: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    tier: Literal["TIER_1", "TIER_2", "TIER_3", "TIER_4"] | None = None,
    source_class: Literal["news", "trusted_news", "official", "directory", "reference", "research", "forum"] | None = None,
    topic: str | None = Query(default=None, max_length=60),
    label: str | None = Query(default=None, max_length=40),
    person: str | None = Query(default=None, max_length=100, pattern=r"^[^\x00]+$"),
    technology: str | None = Query(default=None, max_length=100, pattern=r"^[^\x00]+$"),
    mode: Literal["lexical", "semantic", "hybrid"] = "lexical",
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> ArticleSearchResponse:
    try:
        return await retrieve(
            session, q, mode=mode, entity_key=entity_key, source=source, date_from=date_from, date_to=date_to,
            tier=tier, source_class=source_class, topic=topic, label=label, person=person, technology=technology, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/articles/evidence", response_model=ArticleEvidenceResponse, dependencies=[Depends(get_current_user)])
async def search_stored_articles_with_evidence(
    q: str = Query(min_length=1, max_length=500, pattern=r"^[^\x00]+$"),
    entity_key: str | None = None,
    source: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    tier: Literal["TIER_1", "TIER_2", "TIER_3", "TIER_4"] | None = None,
    source_class: Literal["news", "trusted_news", "official", "directory", "reference", "research", "forum"] | None = None,
    topic: str | None = Query(default=None, max_length=60),
    label: str | None = Query(default=None, max_length=40),
    person: str | None = Query(default=None, max_length=100, pattern=r"^[^\x00]+$"),
    technology: str | None = Query(default=None, max_length=100, pattern=r"^[^\x00]+$"),
    mode: Literal["lexical", "semantic", "hybrid"] = "lexical",
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> ArticleEvidenceResponse:
    """Same ranking as /search/articles, plus verbatim quote spans from the stored article text."""
    try:
        return await search_with_evidence(
            session, q, entity_key=entity_key, source=source, date_from=date_from, date_to=date_to,
            tier=tier, source_class=source_class, topic=topic, label=label, person=person, technology=technology, mode=mode, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/articles/{article_id}/similar", dependencies=[Depends(get_current_user)])
async def similar_stored_articles(article_id: uuid.UUID, limit: int = Query(default=5, ge=1, le=20), session: AsyncSession = Depends(get_session)) -> dict:
    """Related stored articles by embedding similarity of their chunks (empty state when nothing is embedded)."""
    from app.services.retrieval import similar_articles
    return await similar_articles(session, article_id, limit=limit)
