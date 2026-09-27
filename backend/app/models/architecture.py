"""PDF section 14 data model: profiles, client sources, labels, chunks, authorship, recommendations, evaluation.

Every table is additive to the existing schema (migration 0007). Embeddings live in ``document_chunks.embedding``
(pgvector ``vector`` without a fixed dimension; each row records the model that produced it).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin

_NOW = text("now()")


class ClientProfile(Base, UUIDMixin):
    """One immutable, numbered version of a client profile. Exactly one version per client is active."""

    __tablename__ = "client_profiles"

    client_key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[str | None] = mapped_column(Text)
    change_note: Mapped[str | None] = mapped_column(Text)
    reverted_from: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("client_key", "version", name="uq_client_profiles_client_key"),
        Index("uq_client_profiles_one_active", "client_key", unique=True, postgresql_where=text("is_active")),
    )


class DynamicClient(Base, UUIDMixin):
    """Persisted company identity created through self-service onboarding."""

    __tablename__ = "dynamic_clients"

    client_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    official_domain: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)


class SavedArticle(Base, UUIDMixin):
    __tablename__ = "saved_articles"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("user_id", "article_id", name="uq_saved_articles_user_article"),
        Index("ix_saved_articles_user_created", "user_id", "created_at"),
    )


class ClientSource(Base, UUIDMixin):
    __tablename__ = "client_sources"

    client_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="primary")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("client_key", "source_id", name="uq_client_sources_client_key"),
        CheckConstraint("role in ('primary','secondary','reference','discovery')", name="role_valid"),
    )


class ConfigAudit(Base, UUIDMixin):
    """Who changed what and when (profiles, sources, suggestions)."""

    __tablename__ = "config_audit"

    actor: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW, index=True)


class ProfileSuggestion(Base, UUIDMixin):
    """Suggest, then confirm: nothing takes effect until a manager accepts it."""

    __tablename__ = "profile_suggestions"

    client_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # keyword | query | exclusion | competitor | technology
    value: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    example_article_ids: Mapped[list | None] = mapped_column(JSONB)
    method: Mapped[str] = mapped_column(Text, nullable=False, server_default="corpus_terms")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    decided_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("client_key", "kind", "value", name="uq_profile_suggestions_client_key"),
        CheckConstraint("status in ('pending','accepted','dismissed')", name="status_valid"),
    )


class ArticleLabel(Base, UUIDMixin):
    __tablename__ = "article_labels"

    article_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    taxonomy_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("article_id", "label", "method", name="uq_article_labels_article_id"),
        CheckConstraint("method in ('rule','embedding','llm','human')", name="method_valid"),
        CheckConstraint("confidence is null or (confidence >= 0 and confidence <= 1)", name="confidence_range"),
    )


class DocumentChunk(Base, UUIDMixin):
    """A slice of a stored article with character offsets into its source field, an optional vector and a FTS vector."""

    __tablename__ = "document_chunks"

    article_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    field: Mapped[str] = mapped_column(Text, nullable=False)  # title | summary | raw_text
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text_: Mapped[str] = mapped_column("text", Text, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # `embedding vector` and `search_vector tsvector` are created in the migration and read/written with SQL.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("article_id", "field", "chunk_index", name="uq_document_chunks_article_id"),
        CheckConstraint("field in ('title','summary','raw_text')", name="field_valid"),
        CheckConstraint("char_end >= char_start", name="offsets_valid"),
    )


class DocumentAuthor(Base, UUIDMixin):
    __tablename__ = "document_authors"

    article_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    affiliation: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default="source_metadata")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (UniqueConstraint("article_id", "entity_id", name="uq_document_authors_article_id"),)


class Recommendation(Base, UUIDMixin):
    """A recommended action with rationale, confidence and review status (built only from cited, stored evidence)."""

    __tablename__ = "recommendations"

    client_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    opportunity_key: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_basis: Mapped[dict | None] = mapped_column(JSONB)
    article_ids: Mapped[list | None] = mapped_column(JSONB)
    claim_ids: Mapped[list | None] = mapped_column(JSONB)
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default="rule")  # rule | llm
    generated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("client_key", "opportunity_key", "origin", name="uq_recommendations_client_key"),
        CheckConstraint("status in ('draft','reviewed','published','dismissed')", name="status_valid"),
        CheckConstraint("confidence is null or (confidence >= 0 and confidence <= 1)", name="confidence_range"),
    )


class EvaluationSet(Base, UUIDMixin):
    __tablename__ = "evaluation_sets"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # relevance | taxonomy | retrieval | event | evidence
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (UniqueConstraint("name", name="uq_evaluation_sets_name"),)


class EvaluationLabel(Base, UUIDMixin):
    """A human judgement. Only ever written by a person, never by the system under test."""

    __tablename__ = "evaluation_labels"

    set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_sets.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_key: Mapped[str] = mapped_column(Text, nullable=False)  # query text / article id / event key
    item_key: Mapped[str | None] = mapped_column(Text)  # judged result, e.g. article id
    label: Mapped[str] = mapped_column(Text, nullable=False)
    grade: Mapped[int | None] = mapped_column(Integer)
    labeled_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (UniqueConstraint("set_id", "subject_key", "item_key", name="uq_evaluation_labels_set_id"),)


class EvaluationRun(Base, UUIDMixin):
    __tablename__ = "evaluation_runs"

    set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_sets.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str | None] = mapped_column(Text)  # lexical | vector | hybrid ...
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    corpus_digest: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)


class FineTuneDataset(Base, UUIDMixin):
    """A versioned, reproducible export of stored evidence for a fine-tuning target (PDF s.16 open decision;
    manager-requested extension). Built only from real stored data (reviewed claims/labels); never synthetic.
    """

    __tablename__ = "fine_tune_datasets"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # relevance_adjudication | taxonomy_labels | claim_extraction
    source_query: Mapped[dict] = mapped_column(JSONB, nullable=False)  # the selection filter used to build it
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False)  # content hash: reproducibility / drift detection
    format: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'jsonl'"))
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (UniqueConstraint("name", name="uq_fine_tune_datasets_name"),)


class FineTuneJob(Base, UUIDMixin):
    """A fine-tuning job record. Status is never fabricated: with no provider configured, a job is created as
    'blocked' and stays there; only a real provider integration can move it to 'running'/'completed'/'failed'.
    `baseline_metrics`/`result_metrics` reuse the evaluation_runs infrastructure (Section 13) as the before/after hook.
    """

    __tablename__ = "fine_tune_jobs"

    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("fine_tune_datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    base_model: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)  # hyperparameters / provider-specific settings
    provider: Mapped[str | None] = mapped_column(Text)  # null until a real fine-tuning provider is configured
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'blocked'"))
    status_reason: Mapped[str | None] = mapped_column(Text)
    baseline_eval_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.id", ondelete="SET NULL"))
    result_eval_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.id", ondelete="SET NULL"))
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW)

    __table_args__ = (
        CheckConstraint("status in ('blocked','pending','running','completed','failed')", name="fine_tune_job_status_valid"),
    )
