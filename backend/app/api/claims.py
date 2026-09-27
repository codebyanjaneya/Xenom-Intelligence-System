from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.session import get_session
from app.schemas.claims import ClaimCreate, ClaimOut, ClaimStatusUpdate
from app.services.claims import ClaimService, ClaimValidationError

router = APIRouter(prefix="/claims", tags=["claims"])


@router.post("", response_model=ClaimOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_claim(payload: ClaimCreate, session: AsyncSession = Depends(get_session)) -> ClaimOut:
    try:
        return await ClaimService(session=session).create(payload)
    except ClaimValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.patch("/{claim_id}/status", response_model=ClaimOut, dependencies=[Depends(require_admin)])
async def update_claim_status(claim_id: UUID, payload: ClaimStatusUpdate, session: AsyncSession = Depends(get_session)) -> ClaimOut:
    try:
        return await ClaimService(session=session).update_status(claim_id, payload)
    except ClaimValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("", response_model=list[ClaimOut], dependencies=[Depends(require_admin)])
async def list_claims(session: AsyncSession = Depends(get_session)) -> list[ClaimOut]:
    return await ClaimService(session=session).list()