"""m4 fixture-backed ingestion configuration.

Adds an explicit local fixture path to registered sources. M4 does not fetch
network URLs; the path is resolved only inside the checked-in fixture directory.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("fixture_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "fixture_path")