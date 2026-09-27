"""Persist self-service client onboarding.

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "dynamic_clients",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("client_key", sa.Text(), nullable=False, unique=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("official_domain", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("evidence", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

def downgrade() -> None:
    op.drop_table("dynamic_clients")