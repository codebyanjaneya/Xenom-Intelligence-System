"""Auth tests (M2).

These do not require a live database: the session dependency is overridden with
a fake that returns a preset user, and JWTs are minted with the real service.
"""

import asyncio
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import require_admin
from app.db.session import get_session
from app.main import app
from app.models import User
from app.services.auth import create_access_token, decode_access_token, hash_password

PASSWORD = "test-password-123"
_HASH = hash_password(PASSWORD)


def _make_user(role="VIEWER", is_active=True, email="user@xenom.local") -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        display_name="Test User",
        role=role,
        password_hash=_HASH,
        is_active=is_active,
    )


class _FakeSession:
    def __init__(self, user):
        self._user = user

    async def scalar(self, *args, **kwargs):
        return self._user


def _client_for(user) -> TestClient:
    app.dependency_overrides[get_session] = lambda: _FakeSession(user)
    return TestClient(app)


def _clear():
    app.dependency_overrides.pop(get_session, None)


def _auth_header(user, **token_kwargs):
    token = create_access_token(user.id, user.role, **token_kwargs)
    return {"Authorization": f"Bearer {token}"}


# --- login ---------------------------------------------------------------


def test_valid_login():
    user = _make_user(role="ADMIN")
    try:
        resp = _client_for(user).post(
            "/auth/login", json={"email": user.email, "password": PASSWORD}
        )
    finally:
        _clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == user.email
    assert body["user"]["role"] == "ADMIN"
    assert "password_hash" not in resp.text


def test_invalid_password():
    user = _make_user()
    try:
        resp = _client_for(user).post(
            "/auth/login", json={"email": user.email, "password": "wrong-password-x"}
        )
    finally:
        _clear()
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "Invalid email or password"


def test_nonexistent_email():
    try:
        resp = _client_for(None).post(
            "/auth/login", json={"email": "ghost@xenom.local", "password": PASSWORD}
        )
    finally:
        _clear()
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "Invalid email or password"


def test_inactive_user_login():
    user = _make_user(is_active=False)
    try:
        resp = _client_for(user).post(
            "/auth/login", json={"email": user.email, "password": PASSWORD}
        )
    finally:
        _clear()
    assert resp.status_code == 401


# --- /auth/me + tokens ---------------------------------------------------


def test_valid_token_me():
    user = _make_user(role="VIEWER")
    try:
        resp = _client_for(user).get("/auth/me", headers=_auth_header(user))
    finally:
        _clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(user.id)
    assert body["email"] == user.email
    assert body["name"] == "Test User"
    assert body["role"] == "VIEWER"
    assert body["is_active"] is True
    assert "password_hash" not in resp.text


def test_missing_token():
    try:
        resp = _client_for(_make_user()).get("/auth/me")
    finally:
        _clear()
    assert resp.status_code == 401


def test_invalid_token():
    try:
        resp = _client_for(_make_user()).get(
            "/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
    finally:
        _clear()
    assert resp.status_code == 401


def test_expired_token():
    user = _make_user()
    try:
        resp = _client_for(user).get(
            "/auth/me", headers=_auth_header(user, expires_minutes=-1)
        )
    finally:
        _clear()
    assert resp.status_code == 401


# --- role guard ----------------------------------------------------------


def test_admin_role_recognized_via_me():
    user = _make_user(role="ADMIN")
    try:
        resp = _client_for(user).get("/auth/me", headers=_auth_header(user))
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json()["role"] == "ADMIN"


def test_viewer_role_recognized_via_me():
    user = _make_user(role="VIEWER")
    try:
        resp = _client_for(user).get("/auth/me", headers=_auth_header(user))
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json()["role"] == "VIEWER"


def test_viewer_rejected_by_require_admin():
    viewer = _make_user(role="VIEWER")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_admin(user=viewer))
    assert exc.value.status_code == 403


def test_admin_accepted_by_require_admin():
    admin = _make_user(role="ADMIN")
    result = asyncio.run(require_admin(user=admin))
    assert result is admin


# --- leak checks ---------------------------------------------------------


def test_password_hash_never_in_response():
    user = _make_user(role="ADMIN")
    try:
        client = _client_for(user)
        login = client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
        me = client.get("/auth/me", headers=_auth_header(user))
    finally:
        _clear()
    assert _HASH not in login.text
    assert "password_hash" not in login.text
    assert _HASH not in me.text
    assert "password_hash" not in me.text


def test_token_has_no_sensitive_fields():
    user = _make_user(role="ADMIN", email="secret@xenom.local")
    payload = decode_access_token(create_access_token(user.id, user.role))
    assert set(payload.keys()) <= {"sub", "uid", "role", "iat", "exp", "iss", "aud"}
    for leaked in ("email", "password", "password_hash", "name", "display_name"):
        assert leaked not in payload
    assert payload["sub"] == str(user.id)
    assert payload["role"] == "ADMIN"
