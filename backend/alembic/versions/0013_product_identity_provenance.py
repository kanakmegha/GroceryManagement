"""add product identity resolution provenance fields on purchase_items

Revision ID: 0013_product_identity_provenance
Revises: 0012_ingredient_first_foundation
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_product_identity_provenance"
down_revision: str | Sequence[str] | None = "0012_ingredient_first_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.add_column(sa.Column("resolution_method", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("resolution_score", sa.Numeric(precision=4, scale=3), nullable=True)
        )
        batch_op.add_column(sa.Column("resolution_status", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.drop_column("resolution_status")
        batch_op.drop_column("resolution_score")
        batch_op.drop_column("resolution_method")
