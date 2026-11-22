"""extend canonical_items, item_aliases, and purchase_items with food classification metadata

Revision ID: 0006_food_catalogue_class
Revises: 0005_doc_type_and_attach
Create Date: 2026-11-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_food_catalogue_class"
down_revision: str | Sequence[str] | None = "0005_doc_type_and_attach"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("canonical_items") as batch_op:
        batch_op.add_column(sa.Column("secondary_category", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "food_state",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'unknown'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "is_prepared_food", sa.Boolean(), nullable=False, server_default=sa.text("false")
            )
        )
        batch_op.add_column(
            sa.Column(
                "pantry_eligible", sa.Boolean(), nullable=False, server_default=sa.text("true")
            )
        )
        batch_op.add_column(
            sa.Column(
                "budget_treatment",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'pantry_inventory'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "language", sa.String(length=16), nullable=False, server_default=sa.text("'en'")
            )
        )
        batch_op.add_column(sa.Column("source_dataset", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("source_record_id", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )

    with op.batch_alter_table("item_aliases") as batch_op:
        batch_op.add_column(
            sa.Column(
                "alias_kind",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'synonym'"),
            )
        )

    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.add_column(sa.Column("food_state", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("budget_treatment", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("classification_confidence", sa.Numeric(4, 3), nullable=True))
        batch_op.add_column(sa.Column("matching_method", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("classification_source", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.drop_column("classification_source")
        batch_op.drop_column("matching_method")
        batch_op.drop_column("classification_confidence")
        batch_op.drop_column("budget_treatment")
        batch_op.drop_column("food_state")

    with op.batch_alter_table("item_aliases") as batch_op:
        batch_op.drop_column("alias_kind")

    with op.batch_alter_table("canonical_items") as batch_op:
        batch_op.drop_column("verified")
        batch_op.drop_column("source_record_id")
        batch_op.drop_column("source_dataset")
        batch_op.drop_column("language")
        batch_op.drop_column("budget_treatment")
        batch_op.drop_column("pantry_eligible")
        batch_op.drop_column("is_prepared_food")
        batch_op.drop_column("food_state")
        batch_op.drop_column("secondary_category")
