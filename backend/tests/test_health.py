"""Health endpoint tests (M1).

These do not require a live database: the DB session dependency is overridden
so both the success and safe-failure paths are covered deterministically.
Full DB integration is exercised separately via docker-compose during
validation.
"""

from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app


class _OkSession:
    async def execute(self, *args, **kwargs):
        return None


class _FailingSession:
    async def execute(self, *args, **kwargs):
        raise RuntimeError("db down: secret-connection-string")


def _use_session(obj):
    def _override():
        return obj

    return _override


def test_health_db_ok():
    app.dependency_overrides[get_session] = _use_session(_OkSession())
    try:
        client = TestClient(app)
        resp = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "xenom-intel-api"
    assert body["version"]
    assert body["db"] == "ok"


def test_health_db_failure_is_safe():
    app.dependency_overrides[get_session] = _use_session(_FailingSession())
    try:
        client = TestClient(app)
        resp = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error"
    assert body["db"] == "error"
    # No stack trace or internal error detail leaks to the client.
    assert "secret-connection-string" not in resp.text
    assert "Traceback" not in resp.text
