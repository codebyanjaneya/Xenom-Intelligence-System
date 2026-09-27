"""Auth request/response schemas. password_hash is never exposed."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models import User


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    role: str
    is_active: bool

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            name=user.display_name,
            role=user.role,
            is_active=user.is_active,
        )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
