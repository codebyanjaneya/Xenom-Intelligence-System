"""Intelligence layer: insights, their claim traceability, and AI usage log.

Insights are traceable back to claims/evidence via insight_claims.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class Insight(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "insights"

    client_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    insight_type: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[float | None] = mapped_column(Float)
    business_impact: Mapped[float | None] = mapped_column(Float)
    urgency: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")

    __table_args__ = (
        Index("ix_insights_client_entity_id_priority", "client_entity_id", "priority"),
        CheckConstraint("status in ('draft','published','archived')", name="status_valid"),
        CheckConstraint(
            "priority is null or (priority >= 0 and priority <= 1)", name="priority_range"
        ),
        CheckConstraint(
            "business_impact is null or (business_impact >= 0 and business_impact <= 1)",
            name="business_impact_range",
        ),
        CheckConstraint(
            "urgency is null or (urgency >= 0 and urgency <= 1)", name="urgency_range"
        ),
    )


class InsightClaim(Base, UUIDMixin):
    __tablename__ = "insight_claims"

    insight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("insights.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("insight_id", "claim_id", name="uq_insight_claims_insight_id"),
    )


class AiUsage(Base, UUIDMixin):
    __tablename__ = "ai_usage"

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    related_insight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("insights.id", ondelete="SET NULL")
    )
    prompt_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    related_article_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "prompt_tokens is null or prompt_tokens >= 0", name="prompt_tokens_non_negative"
        ),
        CheckConstraint(
            "completion_tokens is null or completion_tokens >= 0",
            name="completion_tokens_non_negative",
        ),
        CheckConstraint(
            "total_tokens is null or total_tokens >= 0", name="total_tokens_non_negative"
        ),
    )
