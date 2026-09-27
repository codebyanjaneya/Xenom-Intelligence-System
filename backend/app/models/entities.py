"""Entity layer.

Entity types are ORG / PERSON / PRODUCT / TECHNOLOGY / INDUSTRY only.
"Client" and "competitor" are NOT entity types; they are business
relationships (see relationships table).
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

ENTITY_TYPES = "('ORG','PERSON','PRODUCT','TECHNOLOGY','INDUSTRY')"
RELATIONSHIP_TYPES = (
    "('CLIENT_OF','COMPETITOR_OF','WORKS_AT','PRODUCT_OF',"
    "'USES_TECHNOLOGY','SUPPLIES','PARTNER_OF')"
)


class Entity(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "entities"

    entity_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Normalized identity key; unique so the same real-world entity is stored once.
    canonical_key: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="needs_review")

    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_entities_canonical_key"),
        CheckConstraint(f"entity_type in {ENTITY_TYPES}", name="entity_type_valid"),
        CheckConstraint("status in ('needs_review','verified')", name="status_valid"),
    )


class EntityAlias(Base, UUIDMixin):
    __tablename__ = "entity_aliases"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("entity_id", "alias", name="uq_entity_aliases_entity_id"),
    )


class EntityMention(Base, UUIDMixin):
    __tablename__ = "entity_mentions"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE")
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE")
    )
    mention_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("entity_id", "article_id", name="uq_entity_mentions_entity_id"),
    )


class Relationship(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "relationships"

    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    verification_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="unverified"
    )

    __table_args__ = (
        UniqueConstraint(
            "source_entity_id",
            "target_entity_id",
            "relationship_type",
            name="uq_relationships_source_entity_id",
        ),
        CheckConstraint(
            f"relationship_type in {RELATIONSHIP_TYPES}", name="relationship_type_valid"
        ),
        CheckConstraint(
            "verification_status in ('unverified','verified','needs_review')",
            name="verification_status_valid",
        ),
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence_range",
        ),
        CheckConstraint("source_entity_id <> target_entity_id", name="no_self_relationship"),
    )


class RelationshipEvidence(Base, UUIDMixin):
    __tablename__ = "relationship_evidence"

    relationship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("relationships.id", ondelete="CASCADE"), nullable=False
    )
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="SET NULL")
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
