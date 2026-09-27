"""Reusable auth dependencies.

Authorization lives here (not in route bodies): get_current_user resolves and
validates the bearer token + user; require_admin enforces the ADMIN role.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.config.clients import CLIENT_CONFIG
from app.models import User, UserPreference
from app.repositories.users import get_user_by_id
from app.services.auth import AuthError, decode_access_token, is_access_token_revoked

_bearer = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED
    try:
        payload = decode_access_token(credentials.credentials)
    except AuthError:
        raise _UNAUTHENTICATED
    if is_access_token_revoked(credentials.credentials):
        raise _UNAUTHENTICATED

    user_id = payload.get("uid") or payload.get("sub")
    if not user_id:
        raise _UNAUTHENTICATED

    user = await get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise _UNAUTHENTICATED
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required",
        )
    return user


async def require_client_access(session: AsyncSession, user: User, client: str) -> str:
    """Return the canonical client name when the user is allowed to view it."""
    canonical = next((name for name, config in CLIENT_CONFIG.items()
                      if client.casefold() in {name.casefold(), *(alias.casefold() for alias in config.aliases)}), None)
    if canonical is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Client not found: {client}")
    if user.role == "ADMIN":
        return canonical
    preference = await session.scalar(select(UserPreference).where(UserPreference.user_id == user.id))
    if preference is None or preference.focus_clients is None:
        return canonical
    allowed = {str(value).casefold() for value in preference.focus_clients}
    if canonical.casefold() not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this client")
    return canonical


async def accessible_clients(session: AsyncSession, user: User) -> list[str]:
    if user.role == "ADMIN":
        return list(CLIENT_CONFIG)
    preference = await session.scalar(select(UserPreference).where(UserPreference.user_id == user.id))
    if preference is None or preference.focus_clients is None:
        return list(CLIENT_CONFIG)
    allowed = {str(value).casefold() for value in preference.focus_clients}
    return [name for name in CLIENT_CONFIG if name.casefold() in allowed]
