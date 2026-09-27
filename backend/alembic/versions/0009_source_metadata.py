"""Add articles.source_metadata: real, source-stated metrics (HF downloads/likes, OpenAlex citations/venue)
for the Research/AI dashboard. Never computed by XENOM; display-only, not used in ranking or relevance.

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("source_metadata", pg.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "source_metadata")
