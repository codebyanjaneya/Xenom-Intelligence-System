"""m3 source registry: source_type vocabulary + tier

Adds the M3 source-type vocabulary and a trust `tier` column to `sources`.
Only the `sources` table is touched; no other M1 table or semantics change.

Why required: M1 restricted source_type to ('rss','html','api','manual') and
had no tier concept. M3 defines a different source-type set and a TIER_1/2/3
trust model, neither expressible on the M1 schema. No source rows exist yet, so
the CHECK swap is safe.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT ck_sources_source_type_valid")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT ck_sources_source_type_valid "
        "CHECK (source_type IS NULL OR source_type IN "
        "('RSS','HTML','OFFICIAL_NEWSROOM','PRESS_RELEASE','GOVERNMENT','REGULATORY','OTHER_PUBLIC'))"
    )
    op.add_column(
        "sources",
        sa.Column("tier", sa.Text(), nullable=False, server_default="TIER_3"),
    )
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT ck_sources_tier_valid "
        "CHECK (tier IN ('TIER_1','TIER_2','TIER_3'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT ck_sources_tier_valid")
    op.drop_column("sources", "tier")
    op.execute("ALTER TABLE sources DROP CONSTRAINT ck_sources_source_type_valid")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT ck_sources_source_type_valid "
        "CHECK (source_type IS NULL OR source_type IN ('rss','html','api','manual'))"
    )
