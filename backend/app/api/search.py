from __future__ import annotations

import uuid

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (accessible_article_ids, allowed_entity_keys, get_current_user, require_admin, require_article_access,
                          require_client_access)
from app.config.clients import ENTITY_KEY_BY_CLIENT
from app.models import User
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
    current_user: User = Depends(get_current_user),
) -> ArticleSearchResponse:
    entity_key, scope = await _scope(session, current_user, entity_key)
    try:
        return await retrieve(
            session, q, mode=mode, entity_key=entity_key, source=source, date_from=date_from, date_to=date_to,
            tier=tier, source_class=source_class, topic=topic, label=label, person=person, technology=technology, limit=limit,
            allowed_entity_keys=scope,
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
    current_user: User = Depends(get_current_user),
) -> ArticleEvidenceResponse:
    """Same ranking as /search/articles, plus verbatim quote spans from the stored article text."""
    entity_key, scope = await _scope(session, current_user, entity_key)
    try:
        return await search_with_evidence(
            session, q, entity_key=entity_key, source=source, date_from=date_from, date_to=date_to,
            tier=tier, source_class=source_class, topic=topic, label=label, person=person, technology=technology, mode=mode, limit=limit,
            allowed_entity_keys=scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/articles/{article_id}/similar", dependencies=[Depends(get_current_user)])
async def similar_stored_articles(article_id: uuid.UUID, limit: int = Query(default=5, ge=1, le=20), session: AsyncSession = Depends(get_session),
                                  current_user: User = Depends(get_current_user)) -> dict:
    """Related stored articles by embedding similarity of their chunks (empty state when nothing is embedded)."""
    from app.models import Article
    from app.services.retrieval import similar_articles
    article = await session.get(Article, article_id)
    if article is None or article.processing_status != "accepted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    await require_article_access(session, current_user, article.id)  # before any similarity retrieval runs
    result = await similar_articles(session, article_id, limit=limit)
    # Neighbours follow the same article rule as the source: granted client, or not attributed to any client.
    results = result.get("results", [])
    visible = await accessible_article_ids(session, current_user, [uuid.UUID(str(r["article_id"])) for r in results])
    result["results"] = [r for r in results if uuid.UUID(str(r["article_id"])) in visible]
    return result


async def _scope(session: AsyncSession, user: User, entity_key: str | None) -> tuple[str | None, list[str] | None]:
    """Authorization for article search: an explicit client filter must be a granted client; results are always
    limited to the user's granted clients (admins are unrestricted)."""
    if entity_key:
        canonical = await require_client_access(session, user, entity_key)
        entity_key = ENTITY_KEY_BY_CLIENT.get(canonical, canonical).lower()
    return entity_key, await allowed_entity_keys(session, user)
