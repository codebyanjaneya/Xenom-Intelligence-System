"""Evidence layer: claims, their sources, groupings, and contradictions.

Claims preserve source-level evidence. Contradictory claims remain SEPARATE
rows; the contradictions table records the conflict rather than merging them.
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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class Claim(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "claims"

    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    entity_key: Mapped[str | None] = mapped_column(Text)
    event_key: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    claim_type: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    verification_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="unverified"
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL")
    )
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint(
            "claim_type is null or claim_type in "
            "('fact','opinion','forecast','announcement','financial','other')",
            name="claim_type_valid",
        ),
        CheckConstraint(
            "verification_status in ('unverified','unreviewed','corroborated','contradicted','verified','rejected')",
            name="verification_status_valid",
        ),
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence_range",
        ),
    )


class ClaimSource(Base, UUIDMixin):
    __tablename__ = "claim_sources"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    excerpt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("claim_id", "article_id", name="uq_claim_sources_claim_id"),
    )


class ClaimGroup(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "claim_groups"

    canonical_statement: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)


class ClaimGroupMember(Base, UUIDMixin):
    __tablename__ = "claim_group_members"

    claim_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claim_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    stance: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("claim_group_id", "claim_id", name="uq_claim_group_members_claim_group_id"),
        CheckConstraint(
            "stance is null or stance in ('support','contradict','neutral')",
            name="stance_valid",
        ),
    )


class Contradiction(Base, UUIDMixin):
    __tablename__ = "contradictions"

    claim_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claim_groups.id", ondelete="SET NULL")
    )
    claim_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    claim_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("claim_a_id", "claim_b_id", name="uq_contradictions_claim_a_id"),
        CheckConstraint("claim_a_id <> claim_b_id", name="distinct_claims"),
    )
