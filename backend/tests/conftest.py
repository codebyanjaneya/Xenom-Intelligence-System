"""Shared test fixtures.

Source-registry tests are integration tests against the real PostgreSQL used by
the project (predictable test DB). They skip cleanly if the DB is unavailable.
The `sources`/`source_health` tables are cleaned before each test; no other data
is touched. Authorization is exercised with real dependencies (require_admin /
get_current_user) by injecting fake ADMIN/VIEWER users.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

# Tests DELETE from articles/sources, so they must never touch the live database.
_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://xenom:change_me_local@localhost:5433/xenom_intel_test",
)
if not _DB_URL.rsplit("/", 1)[-1].endswith("_test"):
    raise RuntimeError(f"Refusing to run tests against non-test database: {_DB_URL.rsplit('/', 1)[-1]}")
os.environ["DATABASE_URL"] = _DB_URL
# The provider-backed POST /search is tested against its fixture provider; production defaults to "unconfigured".
os.environ.setdefault("SEARCH_PROVIDER", "fixture")
# Tests must never inherit a developer's real LLM key/provider from the root .env.
os.environ["LLM_PROVIDER"] = "none"

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.config.scheduler import SCHEDULER_CONFIG
from app.db.session import get_session
from app.main import app
from app.models import User

SCHEDULER_CONFIG.enabled = False


async def _ping(engine) -> None:
    async with engine.connect() as conn:
        # Confirms connectivity AND that the M3 migration (tier column) is applied.
        await conn.execute(text("SELECT tier FROM sources LIMIT 1"))


@pytest.fixture(scope="session")
def db_engine():
    engine = create_async_engine(_DB_URL, poolclass=NullPool)
    try:
        asyncio.run(_ping(engine))
    except Exception:
        asyncio.run(engine.dispose())
        pytest.skip("PostgreSQL (migrated) not available for integration tests")
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture(scope="session", autouse=True)
def _seed_client_entities(db_engine):
    """The six client ORG entities the matcher/relevance stages attach mentions to (test DB only)."""
    from app.db.seed import REGISTERED_CLIENTS, get_or_create_entity

    async def seed() -> None:
        maker = async_sessionmaker(db_engine, expire_on_commit=False)
        async with maker() as session:
            for name in REGISTERED_CLIENTS:
                await get_or_create_entity(session, name)
            await session.commit()

    asyncio.run(seed())


async def _truncate(maker) -> None:
    async with maker() as session:
        await session.execute(text("DELETE FROM articles"))
        for table in ("client_profiles", "config_audit", "profile_suggestions", "client_sources", "recommendations",
                      "evaluation_runs", "evaluation_labels", "evaluation_sets", "review_items", "ai_usage", "alert_reads",
                      "alerts", "insight_claims", "insights", "claims", "opportunities", "xenom_capabilities",
                      "fine_tune_jobs", "fine_tune_datasets", "saved_articles", "users"):
            await session.execute(text(f"DELETE FROM {table}"))
        await session.execute(text("DELETE FROM entities WHERE canonical_key NOT IN ('jcb','ace','definedge','ism','vips','vikas')"))
        await session.execute(text("DELETE FROM source_health"))
        await session.execute(text("DELETE FROM sources"))
        await session.commit()


@pytest.fixture
def _db_override(db_engine):
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    asyncio.run(_truncate(maker))

    async def _dep():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _dep
    yield
    app.dependency_overrides.pop(get_session, None)
    from app.config.clients import CLIENT_CONFIG
    from app.services.profiles import apply_overlay
    for client in list(CLIENT_CONFIG):  # a saved-profile overlay must never leak into the next test
        apply_overlay(client, None)


def _fake_user(role: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{role.lower()}@test.local",
        display_name=role.title(),
        role=role,
        password_hash="not-used-in-source-tests",
        is_active=True,
    )


async def _persist_user(maker, user: User) -> None:
    """User-scoped tables (e.g. saved_articles) FK to users.id, so the fixture user must actually exist."""
    async with maker() as session:
        session.add(user)
        await session.commit()


@pytest.fixture
def admin_client(_db_override, db_engine):
    user = _fake_user("ADMIN")
    asyncio.run(_persist_user(async_sessionmaker(db_engine, expire_on_commit=False), user))
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def viewer_client(_db_override, db_engine):
    user = _fake_user("VIEWER")
    asyncio.run(_persist_user(async_sessionmaker(db_engine, expire_on_commit=False), user))
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def anon_client(_db_override):
    with TestClient(app) as client:
        yield client
