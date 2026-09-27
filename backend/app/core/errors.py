"""Safe global error handling (M0).

Returns a stable JSON envelope and never exposes stack traces to clients.
Full exception details are logged server-side only.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.error")


def _envelope(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", extra={"path": request.url.path})
    return _envelope("internal_error", "Internal server error.", 500)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _envelope("http_error", str(exc.detail), exc.status_code)
