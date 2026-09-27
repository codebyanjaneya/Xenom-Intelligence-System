"""Fine-tuning architecture: versioned datasets built from stored evidence + job records with evaluation hooks.

No training is performed by this migration or the code it supports; jobs are created 'blocked' until a real
fine-tuning provider is configured (manager-requested extension; never a fabricated trained model).

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False)


def upgrade() -> None:
    op.create_table(
        "fine_tune_datasets", _uuid_pk(),
        sa.Column("name", sa.Text(), nullable=False), sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source_query", pg.JSONB(), nullable=False), sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False), sa.Column("format", sa.Text(), nullable=False, server_default="jsonl"),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("name", name=op.f("uq_fine_tune_datasets_name")),
    )
    op.create_table(
        "fine_tune_jobs", _uuid_pk(),
        sa.Column("dataset_id", pg.UUID(as_uuid=True), sa.ForeignKey("fine_tune_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_model", sa.Text(), nullable=False), sa.Column("config", pg.JSONB(), nullable=False),
        sa.Column("provider", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="blocked"),
        sa.Column("status_reason", sa.Text()),
        sa.Column("baseline_eval_run_id", pg.UUID(as_uuid=True), sa.ForeignKey("evaluation_runs.id", ondelete="SET NULL")),
        sa.Column("result_eval_run_id", pg.UUID(as_uuid=True), sa.ForeignKey("evaluation_runs.id", ondelete="SET NULL")),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("status in ('blocked','pending','running','completed','failed')", name=op.f("ck_fine_tune_jobs_status_valid")),
    )
    op.create_index("ix_fine_tune_jobs_dataset_id", "fine_tune_jobs", ["dataset_id"])


def downgrade() -> None:
    op.drop_table("fine_tune_jobs")
    op.drop_table("fine_tune_datasets")
