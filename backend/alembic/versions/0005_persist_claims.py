"""Persist claim identity and evidence provenance."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("claim_key", sa.Text(), nullable=True))
    op.add_column("claims", sa.Column("entity_key", sa.Text(), nullable=True))
    op.add_column("claims", sa.Column("event_key", sa.Text(), nullable=True))
    op.add_column("claims", sa.Column("evidence_json", postgresql.JSONB(), nullable=True))
    op.execute("UPDATE claims SET claim_key = id::text WHERE claim_key IS NULL")
    op.execute("UPDATE claims SET evidence_json = '[]'::jsonb WHERE evidence_json IS NULL")
    op.alter_column("claims", "claim_key", nullable=False)
    op.alter_column("claims", "evidence_json", nullable=False)
    op.create_unique_constraint("uq_claims_claim_key", "claims", ["claim_key"])
    op.execute("ALTER TABLE claims DROP CONSTRAINT ck_claims_verification_status_valid")
    op.execute(
        "ALTER TABLE claims ADD CONSTRAINT ck_claims_verification_status_valid "
        "CHECK (verification_status in ('unverified','unreviewed','corroborated','contradicted','verified','rejected'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE claims DROP CONSTRAINT ck_claims_verification_status_valid")
    op.execute(
        "ALTER TABLE claims ADD CONSTRAINT ck_claims_verification_status_valid "
        "CHECK (verification_status in ('unverified','corroborated','contradicted','verified'))"
    )
    op.drop_constraint("uq_claims_claim_key", "claims", type_="unique")
    op.drop_column("claims", "evidence_json")
    op.drop_column("claims", "event_key")
    op.drop_column("claims", "entity_key")
    op.drop_column("claims", "claim_key")