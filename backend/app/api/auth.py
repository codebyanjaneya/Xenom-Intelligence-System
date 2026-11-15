"""Auth routes: POST /auth/login and GET /auth/me."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.db.session import get_session
from app.config.clients import CLIENT_CONFIG
from app.models import User, UserPreference
from app.repositories.users import get_user_by_email
from app.schemas.auth import LoginRequest, TokenResponse, UserOut
from app.services.auth import (AuthError, check_login_rate, create_access_token,
                               record_login_failure, record_login_success, revoke_access_token, verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])
_logout_bearer = HTTPBearer(auto_error=False)

# Single generic failure — never reveals whether the email exists.
_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password",
    headers={"WWW-Authenticate": "Bearer"},
)




@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    try:
        check_login_rate(body.email)
    except AuthError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "900"}) from exc
    user = await get_user_by_email(session, body.email)
    if (
        user is None
        or not user.is_active
        or not verify_password(user.password_hash, body.password)
    ):
        record_login_failure(body.email)
        raise _INVALID_CREDENTIALS

    record_login_success(body.email)
    token = create_access_token(user.id, user.role)
    return TokenResponse(access_token=token, user=UserOut.from_user(user))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.from_user(current_user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def logout(credentials: HTTPAuthorizationCredentials | None = Depends(_logout_bearer)):
    if credentials is not None:
        revoke_access_token(credentials.credentials)



# ---- client access grants (default-deny for non-admin users) ----------------------------------------------------------
class ClientAccess(BaseModel):
    clients: list[str] = Field(default_factory=list, max_length=100)


async def _grant_target(session: AsyncSession, user_id: uuid.UUID) -> User:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return target


@router.get("/users/{user_id}/clients", response_model=ClientAccess, dependencies=[Depends(require_admin)])
async def get_client_access(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ClientAccess:
    """Clients a user may view. Administrators implicitly see every client; others see only what is granted here."""
    target = await _grant_target(session, user_id)
    if target.role == "ADMIN":
        return ClientAccess(clients=list(CLIENT_CONFIG))
    pref = await session.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    return ClientAccess(clients=list(pref.focus_clients or []) if pref else [])


@router.put("/users/{user_id}/clients", response_model=ClientAccess, dependencies=[Depends(require_admin)])
async def set_client_access(user_id: uuid.UUID, body: ClientAccess, session: AsyncSession = Depends(get_session)) -> ClientAccess:
    """Replace a user's client grants. Every name must be a configured client (canonical name or alias)."""
    await _grant_target(session, user_id)
    canonical: list[str] = []
    for value in body.clients:
        folded = value.strip().casefold()
        name = next((n for n, cfg in CLIENT_CONFIG.items() if folded in {n.casefold(), *(a.casefold() for a in cfg.aliases)}), None)
        if name is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown client: {value}")
        if name not in canonical:
            canonical.append(name)
    pref = await session.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    if pref is None:
        pref = UserPreference(user_id=user_id)
        session.add(pref)
    pref.focus_clients = canonical
    await session.commit()
    return ClientAccess(clients=canonical)
