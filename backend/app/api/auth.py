"""Auth routes: POST /auth/login and GET /auth/me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.db.session import get_session
from app.models import User
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
