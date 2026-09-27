"""Human review queue for low-confidence / needs-verification items."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class ReviewItem(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "review_items"

    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_table: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open", index=True)

    __table_args__ = (
        CheckConstraint("status in ('open','resolved','dismissed')", name="status_valid"),
    )
