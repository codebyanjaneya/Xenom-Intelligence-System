"""Health endpoint (M1). Now performs a real database connectivity check."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings, get_settings
from app.db.session import get_session

router = APIRouter()
logger = logging.getLogger("app.health")


@router.get("/health")
async def health(
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        # Log server-side only; never expose the error/stack trace to clients.
        logger.exception("database_health_check_failed")
        db_status = "error"

    body = {
        "status": "ok" if db_status == "ok" else "error",
        "service": settings.app_name,
        "version": settings.app_version,
        "db": db_status,
    }
    return JSONResponse(status_code=200 if db_status == "ok" else 503, content=body)
