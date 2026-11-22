"""add purchase accounting and inventory eligibility fields

Revision ID: 0003_purchase_accounting_fields
Revises: 0002_mvp_core_tables
Create Date: 2026-11-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_purchase_accounting_fields"
down_revision: str | Sequence[str] | None = "0002_mvp_core_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("purchases") as batch_op:
        batch_op.add_column(sa.Column("receipt_number", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("purchased_time", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column(
                "receipt_category",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'grocery'"),
            )
        )
        batch_op.add_column(sa.Column("payment_method", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("subtotal_paise", sa.BigInteger(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "discount_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            )
        )
        batch_op.add_column(
            sa.Column(
                "additional_charges_paise",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("cgst_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("sgst_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("igst_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column(
                "other_tax_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            )
        )
        batch_op.add_column(
            sa.Column("tax_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column(
                "rounding_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            )
        )

    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.add_column(sa.Column("normalized_name", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("brand", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("variant", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("pack_size", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "discount_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            )
        )
        batch_op.add_column(
            sa.Column("tax_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("tax_rate_bps", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("confidence", sa.Numeric(4, 2), nullable=True))
        batch_op.add_column(sa.Column("warnings_json", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "track_inventory",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.drop_column("track_inventory")
        batch_op.drop_column("warnings_json")
        batch_op.drop_column("confidence")
        batch_op.drop_column("tax_rate_bps")
        batch_op.drop_column("tax_paise")
        batch_op.drop_column("discount_paise")
        batch_op.drop_column("pack_size")
        batch_op.drop_column("variant")
        batch_op.drop_column("brand")
        batch_op.drop_column("normalized_name")

    with op.batch_alter_table("purchases") as batch_op:
        batch_op.drop_column("rounding_paise")
        batch_op.drop_column("tax_paise")
        batch_op.drop_column("other_tax_paise")
        batch_op.drop_column("igst_paise")
        batch_op.drop_column("sgst_paise")
        batch_op.drop_column("cgst_paise")
        batch_op.drop_column("additional_charges_paise")
        batch_op.drop_column("discount_paise")
        batch_op.drop_column("subtotal_paise")
        batch_op.drop_column("payment_method")
        batch_op.drop_column("receipt_category")
        batch_op.drop_column("purchased_time")
        batch_op.drop_column("receipt_number")
