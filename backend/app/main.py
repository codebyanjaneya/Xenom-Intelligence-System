"""XENOM FastAPI application factory, router setup, and process lifecycle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.sources import router as sources_router
from app.api.search import router as search_router
from app.api.intelligence import router as intelligence_router
from app.api.claims import router as claims_router
from app.api.entities import router as entities_router
from app.api.articles import router as articles_router
from app.api.copilot import router as copilot_router
from app.api.profiles import router as profiles_router
from app.api.engine import router as engine_router
from app.api.researchers import router as researchers_router
from app.api.clients import router as clients_router
from app.config.settings import get_settings, validate_for_production
from app.core.errors import http_exception_handler, unhandled_exception_handler
from app.core.logging import configure_logging
from app.db.session import dispose_engine, init_engine
from app.services.background_scheduler import get_scheduler
from app.services.onboarding import load_dynamic_clients
from app.db.session import get_sessionmaker


def create_app() -> FastAPI:
    settings = get_settings()
    validate_for_production(settings)
    configure_logging(settings.log_level)
    logger = logging.getLogger("app")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_engine()
        async with get_sessionmaker()() as session:
            await load_dynamic_clients(session)
        logger.info(
            "application_startup",
            extra={
                "service": settings.app_name,
                "version": settings.app_version,
                "environment": settings.environment,
            },
        )

        # Start background scheduler
        scheduler = get_scheduler()
        await scheduler.start()

        yield

        # Stop background scheduler
        await scheduler.stop()

        await dispose_engine()
        logger.info("application_shutdown", extra={"service": settings.app_name})

    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

    # Stable JSON error envelope; stack traces never reach clients.
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(sources_router)
    app.include_router(search_router)
    app.include_router(intelligence_router)
    app.include_router(claims_router)
    app.include_router(entities_router)
    app.include_router(articles_router)
    app.include_router(copilot_router)
    app.include_router(profiles_router)
    app.include_router(engine_router)
    app.include_router(researchers_router)
    app.include_router(clients_router)
    return app


app = create_app()
