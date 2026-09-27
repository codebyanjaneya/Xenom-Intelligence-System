from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, get_current_user
from app.db.session import get_session
from app.models import User
from app.services.onboarding import OnboardingError, onboard, suggest

router = APIRouter(prefix="/clients", tags=["clients"])


class OnboardRequest(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    website: str | None = Field(default=None, max_length=300)


@router.get("/suggest", dependencies=[Depends(get_current_user)])
async def client_suggestions(q: str = Query(min_length=2, max_length=300)) -> list[dict]:
    try:
        return await suggest(q)
    except OnboardingError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


@router.post("/onboard", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def onboard_client(payload: OnboardRequest, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await onboard(session, query=payload.query, website=payload.website, actor=user.email)
    except OnboardingError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))