"""m5 deterministic article normalization fields."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("normalized_url", sa.Text(), nullable=True))
    op.add_column("articles", sa.Column("content_hash", sa.Text(), nullable=True))
    op.create_index("ix_articles_normalized_url", "articles", ["normalized_url"])
    op.create_index("ix_articles_content_hash", "articles", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_articles_content_hash", table_name="articles")
    op.drop_index("ix_articles_normalized_url", table_name="articles")
    op.drop_column("articles", "content_hash")
    op.drop_column("articles", "normalized_url")