"""XENOM capabilities and client opportunities.

A capability is never presented as confirmed unless verification_status is
VERIFIED. Opportunities default to POTENTIAL and carry an explicit
capability_match flag (VERIFIED / UNVERIFIED) so an unverified capability is
never implied to be confirmed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class XenomCapability(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "xenom_capabilities"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="UNVERIFIED"
    )
    source_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("name", name="uq_xenom_capabilities_name"),
        CheckConstraint(
            "verification_status in ('UNVERIFIED','VERIFIED')",
            name="verification_status_valid",
        ),
    )


class Opportunity(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "opportunities"

    client_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL")
    )
    capability_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("xenom_capabilities.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="POTENTIAL")
    capability_match: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="UNVERIFIED"
    )
    opportunity_score: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("ix_opportunities_client_entity_id_status", "client_entity_id", "status"),
        CheckConstraint(
            "status in ('POTENTIAL','QUALIFIED','DISMISSED')", name="status_valid"
        ),
        CheckConstraint(
            "capability_match in ('VERIFIED','UNVERIFIED')", name="capability_match_valid"
        ),
        CheckConstraint(
            "opportunity_score is null or (opportunity_score >= 0 and opportunity_score <= 1)",
            name="opportunity_score_range",
        ),
        CheckConstraint(
            "priority is null or (priority >= 0 and priority <= 1)", name="priority_range"
        ),
    )
