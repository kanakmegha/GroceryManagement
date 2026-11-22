"""add document_type, order_id, missing_fields_json, and attachments_json to purchases

Revision ID: 0005_doc_type_and_attach
Revises: 0004_food_consumptions_table
Create Date: 2026-11-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_doc_type_and_attach"
down_revision: str | Sequence[str] | None = "0004_food_consumptions_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("purchases") as batch_op:
        batch_op.add_column(
            sa.Column(
                "document_type",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'unknown'"),
            )
        )
        batch_op.add_column(sa.Column("order_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("missing_fields_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("attachments_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("purchases") as batch_op:
        batch_op.drop_column("attachments_json")
        batch_op.drop_column("missing_fields_json")
        batch_op.drop_column("order_id")
        batch_op.drop_column("document_type")
