"""Researcher intelligence API (PDF s.11): who works on what, recent work with source links, similar researchers, seed list."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.db.session import get_session
from app.services import llm as llm_service
from app.services import researchers

router = APIRouter(prefix="/intelligence/researchers", tags=["researchers"])


class SeedPerson(BaseModel):
    name: str = Field(min_length=3, max_length=120, pattern=r"^[^\x00]+$")
    organisation: str | None = Field(default=None, max_length=160)
    areas: list[str] = Field(default=[], max_length=12)


class SeedRequest(BaseModel):
    people: list[SeedPerson] = Field(min_length=1, max_length=200)


@router.get("", dependencies=[Depends(get_current_user)])
async def list_researchers(q: str | None = Query(default=None, max_length=100, pattern=r"^[^\x00]+$"), limit: int = Query(default=25, ge=1, le=100),
                           offset: int = Query(default=0, ge=0), session: AsyncSession = Depends(get_session)) -> dict:
    return await researchers.list_researchers(session, q=q, limit=limit, offset=offset)


@router.post("/seed", dependencies=[Depends(require_admin)])
async def seed_researchers(payload: SeedRequest, session: AsyncSession = Depends(get_session)) -> dict:
    """Starting list of researchers to track. Only PERSON entities (and their stated organisation) are created; ranking and
    every claim about their work still comes from stored documents."""
    return await researchers.seed(session, [p.model_dump() for p in payload.people])


@router.get("/{person_id}", dependencies=[Depends(get_current_user)])
async def researcher(person_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    detail = await researchers.researcher_detail(session, person_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Researcher not found")
    return detail


@router.get("/{person_id}/similar", dependencies=[Depends(get_current_user)])
async def similar(person_id: uuid.UUID, context: str | None = Query(default=None, max_length=200, pattern=r"^[^\x00]+$"),
                  limit: int = Query(default=5, ge=1, le=20), session: AsyncSession = Depends(get_session)) -> dict:
    """Researchers working in a similar style: organisation, what they published recently, and why relevant to ``context``."""
    result = await researchers.similar_researchers(session, person_id, context=context, limit=limit)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Researcher not found")
    return result


@router.post("/{person_id}/summary", dependencies=[Depends(require_admin)])
async def summary(person_id: uuid.UUID, context: str | None = Query(default=None, max_length=200, pattern=r"^[^\x00]+$"),
                  session: AsyncSession = Depends(get_session)) -> dict:
    """LLM summary of stored recent work, cited to those documents (503 when no LLM is configured)."""
    try:
        return await llm_service.summarize_researcher(session, person_id, context=context)
    except llm_service.LLMUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except llm_service.LLMOutputInvalid as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
