"""Phase-1 intelligence pipeline fields for articles.

Adds fields for:
- topic classification (AI & Technology, Industry, Company, etc.)
- relevance score breakdown (reasons for scoring)
- processing status (pipeline state: accepted/rejected/etc.)
- quality gate tracking (passed/failed QUALITY_GATE_DEFAULT=60)

Updates relevance_score to be 0-100 scale instead of 0-1.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Add Phase-1 intelligence fields to articles table."""
    # topic: comma-separated topic tags (AI & Technology|Industry|Company)
    op.add_column(
        "articles",
        sa.Column("topic", sa.Text(), nullable=True),
    )

    # score_reason: human-readable explanation of relevance_score
    op.add_column(
        "articles",
        sa.Column("score_reason", sa.Text(), nullable=True),
    )

    # processing_status: pipeline stage (accepted/rejected/etc.)
    op.add_column(
        "articles",
        sa.Column(
            "processing_status",
            sa.Text(),
            nullable=False,
            server_default="accepted",
        ),
    )

    # Add constraint for valid processing statuses
    op.create_check_constraint(
        "processing_status_valid",
        "articles",
        "processing_status in ('fetched','parsed','cleaned','tagged','scored','accepted','rejected')",
    )

    # quality_gate_passed: whether article meets minimum relevance threshold (60/100)
    op.add_column(
        "articles",
        sa.Column("quality_gate_passed", sa.Boolean(), nullable=True),
    )

    # Create index on quality_gate_passed for filtering
    op.create_index("ix_articles_quality_gate_passed", "articles", ["quality_gate_passed"])

    # Create index on topic for filtering by topic
    op.create_index("ix_articles_topic", "articles", ["topic"])


def downgrade() -> None:
    """Revert Phase-1 intelligence fields."""
    op.drop_index("ix_articles_topic", table_name="articles")
    op.drop_index("ix_articles_quality_gate_passed", table_name="articles")
    op.drop_column("articles", "quality_gate_passed")
    op.drop_constraint("processing_status_valid", table_name="articles", type_="check")
    op.drop_column("articles", "processing_status")
    op.drop_column("articles", "score_reason")
    op.drop_column("articles", "topic")
