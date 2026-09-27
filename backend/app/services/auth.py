"""Authentication service: password hashing and JWT handling.

All password hashing/verification and token encode/decode live here so route
handlers never touch raw crypto. Passwords and tokens are never logged.
"""

from __future__ import annotations

import uuid
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

from app.config.settings import get_settings

MIN_PASSWORD_LENGTH = 10

# Argon2id is the argon2-cffi default.
_hasher = PasswordHasher()
_failed_logins: dict[str, deque[float]] = defaultdict(deque)
_revoked_tokens: set[str] = set()
_MAX_LOGIN_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 300
_LOCKOUT_SECONDS = 900


class AuthError(Exception):
    """Raised when a token is missing/invalid/expired."""


def check_login_rate(email: str) -> None:
    now = time.monotonic()
    failures = _failed_logins[email.casefold()]
    while failures and now - failures[0] > _LOGIN_WINDOW_SECONDS:
        failures.popleft()
    if len(failures) >= _MAX_LOGIN_FAILURES and now - failures[-1] < _LOCKOUT_SECONDS:
        raise AuthError("Too many login attempts; try again later")


def record_login_failure(email: str) -> None:
    _failed_logins[email.casefold()].append(time.monotonic())


def record_login_success(email: str) -> None:
    _failed_logins.pop(email.casefold(), None)


def revoke_access_token(token_id: str | None) -> None:
    if token_id:
        _revoked_tokens.add(token_id)


def is_access_token_revoked(token_id: str | None) -> bool:
    return bool(token_id and token_id in _revoked_tokens)


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except Argon2Error:
        return False


def create_access_token(
    user_id: uuid.UUID | str,
    role: str,
    expires_minutes: int | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    minutes = (
        expires_minutes
        if expires_minutes is not None
        else settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "uid": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid or expired token") from exc
