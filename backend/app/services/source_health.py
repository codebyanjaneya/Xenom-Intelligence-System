"""Source health validation and tracking.

Validates that configured sources are reachable and working.
Tracks source health status separately from article relevance.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source, SourceHealth
from app.repositories import sources as source_repo


HealthStatus = Literal["ok", "error", "timeout"]


async def validate_source_health(session: AsyncSession, source: Source) -> SourceHealth | None:
    """
    Check if a source is reachable and update its health status.

    For RSS feeds, validates feed URL reachability.
    For other sources, validates canonical URL.

    Returns the created SourceHealth record or None if validation fails.
    """
    url_to_check = source.feed_url or source.canonical_url
    if not url_to_check:
        return None

    checked_at = datetime.now(timezone.utc)
    status: HealthStatus = "ok"
    latency_ms: int | None = None
    error_message: str | None = None

    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            http2=False,
        ) as client:
            try:
                start = datetime.now(timezone.utc)
                response = await client.head(
                    url_to_check,
                    headers={"User-Agent": "Mozilla/5.0 XENOM-Intelligence/1.0"},
                    follow_redirects=True,
                )
                end = datetime.now(timezone.utc)
                latency_ms = int((end - start).total_seconds() * 1000)

                # 2xx/3xx = healthy, 4xx/5xx = error
                if response.status_code >= 400:
                    status = "error"
                    error_message = f"HTTP {response.status_code}"
            except httpx.TimeoutException:
                status = "timeout"
                error_message = "Request timeout (>10s)"
            except httpx.HTTPStatusError as e:
                status = "error"
                error_message = f"HTTP {e.response.status_code}"
            except httpx.RequestError as e:
                status = "error"
                error_message = str(e)

    except Exception as e:
        status = "error"
        error_message = f"Validation error: {str(e)}"

    health = SourceHealth(
        source_id=source.id,
        checked_at=checked_at,
        status=status,
        latency_ms=latency_ms,
        error_message=error_message,
    )
    session.add(health)
    await session.flush()
    return health
