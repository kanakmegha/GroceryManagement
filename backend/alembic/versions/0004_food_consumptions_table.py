"""add food_consumptions table for outside-food and meal tracking

Revision ID: 0004_food_consumptions_table
Revises: 0003_purchase_accounting_fields
Create Date: 2026-11-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_food_consumptions_table"
down_revision: str | Sequence[str] | None = "0003_purchase_accounting_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.create_table(
        "food_consumptions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "household_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "purchase_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("purchases.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("consumed_on", sa.Date(), nullable=False),
        sa.Column(
            "meal_type",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'other'"),
        ),
        sa.Column(
            "dining_mode",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'restaurant'"),
        ),
        sa.Column("merchant_name", sa.Text(), nullable=True),
        sa.Column(
            "amount_paise",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'INR'"),
        ),
        sa.Column("items_summary", sa.JSON(), nullable=True),
        sa.Column("people_count", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_food_consumptions_household_date",
        "food_consumptions",
        ["household_id", "consumed_on"],
    )

    if is_postgres:
        op.execute("ALTER TABLE food_consumptions ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE food_consumptions FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_food_consumptions ON food_consumptions
            USING (
                household_id = NULLIF(current_setting('app.current_household_id', true), '')::uuid
            )
            WITH CHECK (
                household_id = NULLIF(current_setting('app.current_household_id', true), '')::uuid
            );
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS tenant_isolation_food_consumptions ON food_consumptions;"
        )
    op.drop_index("idx_food_consumptions_household_date", table_name="food_consumptions")
    op.drop_table("food_consumptions")
