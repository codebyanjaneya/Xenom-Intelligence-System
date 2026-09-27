from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.session import get_session
from app.schemas.copilot import CopilotRequest, CopilotResponse
from app.services.copilot import CopilotUnavailable, answer

router = APIRouter(prefix="/copilot", tags=["copilot"])


@router.post("", response_model=CopilotResponse, dependencies=[Depends(require_admin)])
async def copilot(payload: CopilotRequest, session: AsyncSession = Depends(get_session)) -> CopilotResponse:
    try:
        return await answer(session, payload)
    except CopilotUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))