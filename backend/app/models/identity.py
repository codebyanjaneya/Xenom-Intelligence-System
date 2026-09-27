"""Identity / user tables.

Auth logic is NOT implemented here (that is M2). These tables exist so the
schema is complete and referential integrity holds for later milestones.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="VIEWER")
    # Nullable: auth is M2. Any value seeded now is a documented dev placeholder.
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("role in ('ADMIN','VIEWER')", name="role_valid"),
    )


class UserPreference(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    focus_clients: Mapped[list | None] = mapped_column(JSONB)
    muted_categories: Mapped[list | None] = mapped_column(JSONB)
    digest_frequency: Mapped[str] = mapped_column(Text, nullable=False, server_default="daily")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_preferences_user_id"),
        CheckConstraint(
            "digest_frequency in ('daily','weekly','none')", name="digest_frequency_valid"
        ),
    )


class AlertRead(Base, UUIDMixin):
    __tablename__ = "alert_reads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "alert_id", name="uq_alert_reads_user_id"),
        Index("ix_alert_reads_user_id_alert_id", "user_id", "alert_id"),
    )


class UserNote(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_notes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL")
    )
    insight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("insights.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
