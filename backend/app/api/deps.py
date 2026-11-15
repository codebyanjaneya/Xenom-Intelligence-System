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
from app.config.clients import CLIENT_CONFIG, ENTITY_KEY_BY_CLIENT
from app.models import Entity, EntityMention, User, UserPreference
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


async def _focus_clients(session: AsyncSession, user: User) -> set[str]:
    """Clients (casefolded canonical names) a non-admin user was explicitly granted.

    Default-deny: no preference row, or a row without ``focus_clients``, grants no client data."""
    preference = await session.scalar(select(UserPreference).where(UserPreference.user_id == user.id))
    if preference is None or not preference.focus_clients:
        return set()
    return {str(value).casefold() for value in preference.focus_clients}


def _canonical_client(client: str) -> str | None:
    folded = client.casefold()
    return next((name for name, config in CLIENT_CONFIG.items()
                 if folded in {name.casefold(), *(alias.casefold() for alias in config.aliases)}), None)


async def require_client_access(session: AsyncSession, user: User, client: str) -> str:
    """Return the canonical client name when the user is allowed to view it (admins: every client)."""
    canonical = _canonical_client(client)
    if canonical is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Client not found: {client}")
    if user.role == "ADMIN":
        return canonical
    if canonical.casefold() not in await _focus_clients(session, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this client")
    return canonical


async def accessible_clients(session: AsyncSession, user: User) -> list[str]:
    if user.role == "ADMIN":
        return list(CLIENT_CONFIG)
    allowed = await _focus_clients(session, user)
    return [name for name in CLIENT_CONFIG if name.casefold() in allowed]


async def allowed_entity_keys(session: AsyncSession, user: User) -> list[str] | None:
    """Entity keys for query-level scoping: ``None`` = unrestricted (admin), otherwise the user's granted clients
    (possibly empty, which leaves only data that is not attributed to any client)."""
    if user.role == "ADMIN":
        return None
    return sorted({ENTITY_KEY_BY_CLIENT.get(name, name).lower() for name in await accessible_clients(session, user)}
                  | {name.lower() for name in await accessible_clients(session, user)})


def client_key_allowed(client_key: str | None, allowed: list[str] | None) -> bool:
    """Row-level check for objects that carry a client key (recommendations, insights, recall items)."""
    if allowed is None:
        return True
    return bool(client_key) and client_key.strip().lower() in set(allowed)


async def article_client_names(session: AsyncSession, article_ids: list) -> dict:
    """``{article_id: {casefolded client names}}`` for the client ORG entities each article is attributed to."""
    out: dict = {article_id: set() for article_id in article_ids}
    if not article_ids:
        return out
    rows = await session.execute(
        select(EntityMention.article_id, Entity.canonical_name).join(Entity, EntityMention.entity_id == Entity.id)
        .where(EntityMention.article_id.in_(article_ids), Entity.entity_type == "ORG"))
    for article_id, name in rows.all():
        out[article_id].add(str(name).casefold())
    return out


async def accessible_article_ids(session: AsyncSession, user: User, article_ids: list) -> set:
    """The subset of ``article_ids`` the user may see. Same rule as article search: admins see everything; others see an
    article only if it is attributed to a granted client, or is not attributed to any client (research / technology)."""
    if user.role == "ADMIN":
        return set(article_ids)
    allowed = {name.casefold() for name in await accessible_clients(session, user)}
    return {article_id for article_id, clients in (await article_client_names(session, article_ids)).items()
            if not clients or clients & allowed}


async def require_article_access(session: AsyncSession, user: User, article_id) -> None:
    if article_id not in await accessible_article_ids(session, user, [article_id]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this article")
