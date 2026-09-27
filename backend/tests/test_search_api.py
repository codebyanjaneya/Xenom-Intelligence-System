from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.config.settings import get_settings
from app.main import app
from app.models import User


def _user(role: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{role.lower()}@search.test",
        display_name=role.title(),
        role=role,
        password_hash="unused",
        is_active=True,
    )


def _client(role: str | None) -> TestClient:
    if role is not None:
        app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app)


def _clear() -> None:
    app.dependency_overrides.pop(get_current_user, None)


def test_admin_can_search_fixture_and_response_schema() -> None:
    try:
        response = _client("ADMIN").post(
            "/search", json={"query": "AI robotics", "limit": 10}
        )
    finally:
        _clear()

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "AI robotics"
    assert body["provider"] == "fixture"
    assert body["count"] == 3
    assert set(body["results"][0]) >= {
        "title",
        "url",
        "snippet",
        "domain",
        "discovered_at",
        "published_at",
        "provider",
        "relevance_score",
        "score_breakdown",
    }


def test_search_limit_is_enforced() -> None:
    try:
        response = _client("ADMIN").post(
            "/search", json={"query": "artificial intelligence robotics", "limit": 2}
        )
    finally:
        _clear()

    assert response.status_code == 200
    assert response.json()["count"] == 2


@pytest.mark.parametrize("payload", [{"query": "   "}, {"query": "AI", "limit": 0}])
def test_invalid_search_request_is_rejected(payload) -> None:
    try:
        response = _client("ADMIN").post("/search", json=payload)
    finally:
        _clear()

    assert response.status_code == 422


def test_viewer_is_rejected() -> None:
    try:
        response = _client("VIEWER").post("/search", json={"query": "AI robotics"})
    finally:
        _clear()
    assert response.status_code == 403


def test_unauthenticated_is_rejected() -> None:
    try:
        response = _client(None).post("/search", json={"query": "AI robotics"})
    finally:
        _clear()
    assert response.status_code == 401


def test_unconfigured_live_provider_returns_safe_error(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_PROVIDER", "live")
    get_settings.cache_clear()
    try:
        response = _client("ADMIN").post("/search", json={"query": "AI robotics"})
    finally:
        _clear()
        monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
        get_settings.cache_clear()

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "No live search provider is configured"
    assert "Traceback" not in response.text

def test_default_search_provider_never_serves_fixture_data(monkeypatch) -> None:
    """Production default: no SEARCH_PROVIDER -> POST /search is unconfigured (503), not fixture results."""
    from app.config.settings import Settings

    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    assert Settings(_env_file=None).search_provider == "unconfigured"
    get_settings.cache_clear()
    try:
        response = _client("ADMIN").post("/search", json={"query": "AI robotics"})
    finally:
        _clear()
        get_settings.cache_clear()
    assert response.status_code == 503
    assert "synthetic" not in response.text
