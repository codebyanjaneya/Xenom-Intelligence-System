"""PDF section 14 data model: profiles, client sources, labels, chunks, authors, recommendations, evaluation.

Revision ID: 0007
Revises: c3e1dcbbb2ca
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007"
down_revision: Union[str, None] = "c3e1dcbbb2ca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---- sources extension: fourth tier, class, config, per-source priority ------------------------------------------
    op.execute("ALTER TABLE sources DROP CONSTRAINT ck_sources_tier_valid")
    op.execute("ALTER TABLE sources ADD CONSTRAINT ck_sources_tier_valid CHECK (tier in ('TIER_1','TIER_2','TIER_3','TIER_4'))")
    op.execute("ALTER TABLE sources DROP CONSTRAINT ck_sources_source_type_valid")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT ck_sources_source_type_valid CHECK (source_type is null or source_type in "
        "('RSS','HTML','OFFICIAL_NEWSROOM','PRESS_RELEASE','GOVERNMENT','REGULATORY','OTHER_PUBLIC','RESEARCH_API','MODEL_HUB'))"
    )
    op.add_column("sources", sa.Column("source_class", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("config", pg.JSONB(), nullable=True))
    op.add_column("sources", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sources", sa.Column("notes", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_sources_source_class_valid", "sources",
        "source_class is null or source_class in ('official','trusted_news','news','directory','reference','research','forum')",
    )

    # ---- lexical search: generated tsvector + GIN, trigram index for names -------------------------------------------
    op.execute(
        "ALTER TABLE articles ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ("
        "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
        "setweight(to_tsvector('english', coalesce(summary, '')), 'B')) STORED"
    )
    op.execute("CREATE INDEX ix_articles_search_vector ON articles USING gin (search_vector)")
    op.execute("CREATE INDEX ix_articles_title_trgm ON articles USING gin (title gin_trgm_ops)")

    # ---- ai_usage: prompt version + outcome (every LLM call is logged, including failures) ----------------------------
    op.add_column("ai_usage", sa.Column("prompt_version", sa.Text(), nullable=True))
    op.add_column("ai_usage", sa.Column("status", sa.Text(), nullable=True))
    op.add_column("ai_usage", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("ai_usage", sa.Column("related_article_id", pg.UUID(as_uuid=True), nullable=True))

    op.create_table(
        "client_profiles", _uuid_pk(),
        sa.Column("client_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.Text()), sa.Column("change_note", sa.Text()), sa.Column("reverted_from", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("client_key", "version", name=op.f("uq_client_profiles_client_key")),
    )
    op.create_index("uq_client_profiles_one_active", "client_profiles", ["client_key"], unique=True, postgresql_where=sa.text("is_active"))

    op.create_table(
        "client_sources", _uuid_pk(),
        sa.Column("client_key", sa.Text(), nullable=False),
        sa.Column("source_id", pg.UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("role", sa.Text(), nullable=False, server_default="primary"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("client_key", "source_id", name=op.f("uq_client_sources_client_key")),
        sa.CheckConstraint("role in ('primary','secondary','reference','discovery')", name=op.f("ck_client_sources_role_valid")),
    )

    op.create_table(
        "config_audit", _uuid_pk(),
        sa.Column("actor", sa.Text()), sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text(), nullable=False), sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("before", pg.JSONB()), sa.Column("after", pg.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("ix_config_audit_created_at", "config_audit", ["created_at"])

    op.create_table(
        "profile_suggestions", _uuid_pk(),
        sa.Column("client_key", sa.Text(), nullable=False), sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False), sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("example_article_ids", pg.JSONB()),
        sa.Column("method", sa.Text(), nullable=False, server_default="corpus_terms"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("client_key", "kind", "value", name=op.f("uq_profile_suggestions_client_key")),
        sa.CheckConstraint("status in ('pending','accepted','dismissed')", name=op.f("ck_profile_suggestions_status_valid")),
    )
    op.create_index("ix_profile_suggestions_client_key", "profile_suggestions", ["client_key"])

    op.create_table(
        "article_labels", _uuid_pk(),
        sa.Column("article_id", pg.UUID(as_uuid=True), sa.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.Text(), nullable=False), sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()), sa.Column("evidence", pg.JSONB()),
        sa.Column("taxonomy_version", sa.Text(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("article_id", "label", "method", name=op.f("uq_article_labels_article_id")),
        sa.CheckConstraint("method in ('rule','embedding','llm','human')", name=op.f("ck_article_labels_method_valid")),
        sa.CheckConstraint("confidence is null or (confidence >= 0 and confidence <= 1)", name=op.f("ck_article_labels_confidence_range")),
    )
    op.create_index("ix_article_labels_article_id", "article_labels", ["article_id"])
    op.create_index("ix_article_labels_label", "article_labels", ["label"])

    op.create_table(
        "document_chunks", _uuid_pk(),
        sa.Column("article_id", pg.UUID(as_uuid=True), sa.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field", sa.Text(), nullable=False), sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False), sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text()), sa.Column("embedded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("article_id", "field", "chunk_index", name=op.f("uq_document_chunks_article_id")),
        sa.CheckConstraint("field in ('title','summary','raw_text')", name=op.f("ck_document_chunks_field_valid")),
        sa.CheckConstraint("char_end >= char_start", name=op.f("ck_document_chunks_offsets_valid")),
    )
    op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector")
    op.execute("ALTER TABLE document_chunks ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED")
    op.create_index("ix_document_chunks_article_id", "document_chunks", ["article_id"])
    op.execute("CREATE INDEX ix_document_chunks_search_vector ON document_chunks USING gin (search_vector)")
    op.execute("CREATE INDEX ix_document_chunks_embedding_model ON document_chunks (embedding_model) WHERE embedding IS NOT NULL")

    op.create_table(
        "document_authors", _uuid_pk(),
        sa.Column("article_id", pg.UUID(as_uuid=True), sa.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", pg.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("affiliation", sa.Text()),
        sa.Column("origin", sa.Text(), nullable=False, server_default="source_metadata"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("article_id", "entity_id", name=op.f("uq_document_authors_article_id")),
    )
    op.create_index("ix_document_authors_article_id", "document_authors", ["article_id"])
    op.create_index("ix_document_authors_entity_id", "document_authors", ["entity_id"])

    op.create_table(
        "recommendations", _uuid_pk(),
        sa.Column("client_key", sa.Text(), nullable=False), sa.Column("opportunity_key", sa.Text()),
        sa.Column("title", sa.Text(), nullable=False), sa.Column("action", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()), sa.Column("confidence_basis", pg.JSONB()),
        sa.Column("article_ids", pg.JSONB()), sa.Column("claim_ids", pg.JSONB()),
        sa.Column("origin", sa.Text(), nullable=False, server_default="rule"),
        sa.Column("generated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("reviewed_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("client_key", "opportunity_key", "origin", name=op.f("uq_recommendations_client_key")),
        sa.CheckConstraint("status in ('draft','reviewed','published','dismissed')", name=op.f("ck_recommendations_status_valid")),
        sa.CheckConstraint("confidence is null or (confidence >= 0 and confidence <= 1)", name=op.f("ck_recommendations_confidence_range")),
    )
    op.create_index("ix_recommendations_client_key", "recommendations", ["client_key"])

    op.create_table(
        "evaluation_sets", _uuid_pk(),
        sa.Column("name", sa.Text(), nullable=False), sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()), sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("name", name=op.f("uq_evaluation_sets_name")),
    )
    op.create_table(
        "evaluation_labels", _uuid_pk(),
        sa.Column("set_id", pg.UUID(as_uuid=True), sa.ForeignKey("evaluation_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_key", sa.Text(), nullable=False), sa.Column("item_key", sa.Text()),
        sa.Column("label", sa.Text(), nullable=False), sa.Column("grade", sa.Integer()), sa.Column("labeled_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("set_id", "subject_key", "item_key", name=op.f("uq_evaluation_labels_set_id")),
    )
    op.create_index("ix_evaluation_labels_set_id", "evaluation_labels", ["set_id"])
    op.create_table(
        "evaluation_runs", _uuid_pk(),
        sa.Column("set_id", pg.UUID(as_uuid=True), sa.ForeignKey("evaluation_sets.id", ondelete="SET NULL")),
        sa.Column("kind", sa.Text(), nullable=False), sa.Column("mode", sa.Text()),
        sa.Column("metrics", pg.JSONB(), nullable=False), sa.Column("corpus_digest", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("ix_evaluation_runs_set_id", "evaluation_runs", ["set_id"])


def downgrade() -> None:
    for table in ("evaluation_runs", "evaluation_labels", "evaluation_sets", "recommendations", "document_authors",
                  "document_chunks", "article_labels", "profile_suggestions", "config_audit", "client_sources", "client_profiles"):
        op.drop_table(table)
    for col in ("related_article_id", "error", "status", "prompt_version"):
        op.drop_column("ai_usage", col)
    op.execute("DROP INDEX IF EXISTS ix_articles_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_articles_search_vector")
    op.execute("ALTER TABLE articles DROP COLUMN search_vector")
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS ck_sources_source_class_valid")
    for col in ("notes", "priority", "config", "source_class"):
        op.drop_column("sources", col)
    op.execute("ALTER TABLE sources DROP CONSTRAINT ck_sources_source_type_valid")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT ck_sources_source_type_valid CHECK (source_type is null or source_type in "
        "('RSS','HTML','OFFICIAL_NEWSROOM','PRESS_RELEASE','GOVERNMENT','REGULATORY','OTHER_PUBLIC'))"
    )
    op.execute("ALTER TABLE sources DROP CONSTRAINT ck_sources_tier_valid")
    op.execute("ALTER TABLE sources ADD CONSTRAINT ck_sources_tier_valid CHECK (tier in ('TIER_1','TIER_2','TIER_3'))")
