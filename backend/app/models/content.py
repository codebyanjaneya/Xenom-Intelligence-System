"""Content layer: articles, events, threads, and their links.

Articles and events are distinct objects. Multiple articles may belong to one
event, and events may evolve over time (grouped into threads).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class Thread(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "threads"

    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)


class Article(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "articles"

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    normalized_url: Mapped[str | None] = mapped_column(Text, index=True)
    content_hash: Mapped[str | None] = mapped_column(Text, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    topic: Mapped[str | None] = mapped_column(Text)
    """Comma-separated topic tags (e.g., 'AI & Technology|Industry|Company')."""

    score_reason: Mapped[str | None] = mapped_column(Text)
    """Human-readable explanation of relevance_score."""

    processing_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="accepted", index=True
    )
    """Pipeline stage: fetched|parsed|cleaned|tagged|scored|accepted|rejected."""

    quality_gate_passed: Mapped[bool | None] = mapped_column(
        default=None, index=True
    )
    """Whether article meets minimum relevance threshold (60/100)."""

    source_metadata: Mapped[dict | None] = mapped_column(JSONB)
    """Real, source-stated metrics the adapter returned (e.g. Hugging Face downloads/likes/pipeline_tag,
    OpenAlex cited_by_count/venue). Never computed or estimated by XENOM; displayed as-is with attribution,
    or omitted when the source doesn't provide it. Not used for ranking/relevance — display only."""

    __table_args__ = (
        UniqueConstraint("url", name="uq_articles_url"),
        CheckConstraint(
            "relevance_score is null or (relevance_score >= 0 and relevance_score <= 1)",
            name="relevance_score_range",
        ),
        CheckConstraint(
            "processing_status in ('fetched','parsed','cleaned','tagged','scored','accepted','rejected')",
            name="processing_status_valid",
        ),
    )


class Event(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "events"

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="SET NULL")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), index=True
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), index=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")

    __table_args__ = (
        CheckConstraint("status in ('open','merged','archived')", name="status_valid"),
    )


class ArticleEventLink(Base, UUIDMixin):
    __tablename__ = "article_event_links"

    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("article_id", "event_id", name="uq_article_event_links_article_id"),
    )
