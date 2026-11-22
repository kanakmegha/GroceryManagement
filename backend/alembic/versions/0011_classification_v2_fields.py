"""add classification v2 fields (secondary_category, matching_confidence, eligibility_reason,
unit_price_provenance)

Revision ID: 0011_classification_v2_fields
Revises: 0010_canonical_merge_audit
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_classification_v2_fields"
down_revision: str | Sequence[str] | None = "0010_canonical_merge_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.add_column(sa.Column("secondary_category", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("matching_confidence", sa.Numeric(4, 3), nullable=True))
        batch_op.add_column(sa.Column("eligibility_reason", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("unit_price_provenance", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.drop_column("unit_price_provenance")
        batch_op.drop_column("eligibility_reason")
        batch_op.drop_column("matching_confidence")
        batch_op.drop_column("secondary_category")
