"""Source registry and source health checks."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class Source(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    feed_url: Mapped[str | None] = mapped_column(Text)
    fixture_path: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(Text)
    # Trust tier (TIER_1..TIER_4; TIER_4 = discovery signal only) plus an optional numeric reliability weight.
    tier: Mapped[str] = mapped_column(Text, nullable=False, server_default="TIER_3")
    reliability_score: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    source_class: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict | None] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("canonical_url", name="uq_sources_canonical_url"),
        CheckConstraint(
            "source_type is null or source_type in "
            "('RSS','HTML','OFFICIAL_NEWSROOM','PRESS_RELEASE','GOVERNMENT','REGULATORY','OTHER_PUBLIC','RESEARCH_API','MODEL_HUB')",
            name="source_type_valid",
        ),
        CheckConstraint(
            "tier in ('TIER_1','TIER_2','TIER_3','TIER_4')",
            name="tier_valid",
        ),
        CheckConstraint(
            "source_class is null or source_class in ('official','trusted_news','news','directory','reference','research','forum')",
            name="source_class_valid",
        ),
        CheckConstraint(
            "reliability_score is null or (reliability_score >= 0 and reliability_score <= 1)",
            name="reliability_score_range",
        ),
    )


class SourceHealth(Base, UUIDMixin):
    __tablename__ = "source_health"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("status in ('ok','error','timeout')", name="status_valid"),
    )
