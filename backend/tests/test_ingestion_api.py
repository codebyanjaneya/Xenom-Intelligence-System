from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.session import get_session
from app.main import app
from app.models import Source, User
from app.schemas.ingestion import IngestionResult


def _user(role: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{role.lower()}@test.local",
        display_name=role.title(),
        role=role,
        password_hash="unused",
        is_active=True,
    )


def _source(**overrides) -> Source:
    values = {
        "id": uuid.uuid4(),
        "name": "Synthetic fixture source",
        "canonical_url": "https://fixture.test/source",
        "source_type": "RSS",
        "tier": "TIER_3",
        "fixture_path": "m4-example-feed.xml",
        "is_active": True,
    }
    values.update(overrides)
    return Source(**values)


class _Session:
    pass


def _client(role: str | None) -> TestClient:
    if role is not None:
        app.dependency_overrides[get_current_user] = lambda: _user(role)
    app.dependency_overrides[get_session] = lambda: _Session()
    return TestClient(app)


def _clear() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)


def test_fetch_requires_authentication() -> None:
    try:
        response = _client(None).post(f"/sources/{uuid.uuid4()}/fetch")
    finally:
        _clear()
    assert response.status_code == 401


def test_viewer_cannot_fetch() -> None:
    try:
        response = _client("VIEWER").post(f"/sources/{uuid.uuid4()}/fetch")
    finally:
        _clear()
    assert response.status_code == 403


def test_unsupported_source_returns_safe_error(monkeypatch) -> None:
    source = _source(fixture_path=None)
    monkeypatch.setattr("app.api.sources.service.get_source", _get_source(source))
    try:
        response = _client("ADMIN").post(f"/sources/{source.id}/fetch")
    finally:
        _clear()
    assert response.status_code == 422
    assert "not configured" in response.json()["error"]["message"]


def test_admin_fetch_route_returns_ingestion_result(monkeypatch) -> None:
    source = _source()
    monkeypatch.setattr("app.api.sources.service.get_source", _get_source(source))

    async def fake_ingest(session, source):
        return IngestionResult(
            source_id=source.id,
            adapter="rss_fixture",
            documents_found=2,
            articles_created=2,
        )

    monkeypatch.setattr("app.api.sources.ingest_source", fake_ingest)
    try:
        response = _client("ADMIN").post(f"/sources/{source.id}/fetch")
    finally:
        _clear()
    assert response.status_code == 200
    assert response.json()["documents_found"] == 2
    assert response.json()["articles_created"] == 2


def test_admin_fixture_fetch_persists_articles(admin_client) -> None:
    payload = {
        "name": "Synthetic fixture source",
        "url": "https://fixture.test/persisted-feed",
        "source_type": "RSS",
        "tier": "TIER_3",
        "fixture_path": "m4-example-feed.xml",
    }
    source_response = admin_client.post("/sources", json=payload)
    assert source_response.status_code == 201, source_response.text

    response = admin_client.post(f"/sources/{source_response.json()['id']}/fetch")

    assert response.status_code == 200, response.text
    assert response.json()["documents_found"] == 2
    assert response.json()["articles_created"] == 2

    repeated = admin_client.post(f"/sources/{source_response.json()['id']}/fetch")

    assert repeated.status_code == 200
    assert repeated.json()["articles_created"] == 0
    assert repeated.json()["articles_deduplicated"] == 2


def _get_source(source):
    async def getter(session, source_id):
        return source if source_id == source.id else None

    return getter
