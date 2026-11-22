"""mvp core tables

Revision ID: 0002_mvp_core_tables
Revises: 0001_auth_and_households
Create Date: 2026-11-17

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_mvp_core_tables"
down_revision: str | Sequence[str] | None = "0001_auth_and_households"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_ONLY_TABLES = (
    "purchases",
    "purchase_items",
    "inventory_lots",
    "stock_ledger",
    "cooking_plans",
)
_SHARED_CATALOG_TABLES = ("canonical_items", "item_aliases")


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.create_table(
        "canonical_items",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False, server_default=sa.text("'general'")),
        sa.Column("default_base_unit", sa.String(length=8), nullable=False),
        sa.Column("default_shelf_life_days", sa.Integer(), nullable=True),
        sa.Column(
            "low_stock_threshold_qty",
            sa.Numeric(12, 3),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_canonical_items_household_id_households",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "default_base_unit IN ('g', 'ml', 'pcs')",
            name="ck_canonical_items_base_unit",
        ),
        sa.CheckConstraint(
            "low_stock_threshold_qty >= 0",
            name="ck_canonical_items_low_stock_non_neg",
        ),
    )
    op.create_index(
        "uq_canonical_items_global_slug",
        "canonical_items",
        ["slug"],
        unique=True,
        sqlite_where=sa.text("household_id IS NULL"),
        postgresql_where=sa.text("household_id IS NULL"),
    )
    op.create_index(
        "uq_canonical_items_household_slug",
        "canonical_items",
        ["household_id", "slug"],
        unique=True,
        sqlite_where=sa.text("household_id IS NOT NULL"),
        postgresql_where=sa.text("household_id IS NOT NULL"),
    )

    op.create_table(
        "item_aliases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("canonical_item_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column(
            "source", sa.String(length=32), nullable=False, server_default=sa.text("'system'")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_item_aliases_household_id_households",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_item_id"],
            ["canonical_items.id"],
            name="fk_item_aliases_canonical_item_id_canonical_items",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "uq_item_aliases_global",
        "item_aliases",
        ["normalized_alias"],
        unique=True,
        sqlite_where=sa.text("household_id IS NULL"),
        postgresql_where=sa.text("household_id IS NULL"),
    )
    op.create_index(
        "uq_item_aliases_household",
        "item_aliases",
        ["household_id", "normalized_alias"],
        unique=True,
        sqlite_where=sa.text("household_id IS NOT NULL"),
        postgresql_where=sa.text("household_id IS NOT NULL"),
    )
    op.create_index("idx_item_aliases_canonical", "item_aliases", ["canonical_item_id"])

    op.create_table(
        "purchases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'draft'")
        ),
        sa.Column(
            "source_type", sa.String(length=16), nullable=False, server_default=sa.text("'manual'")
        ),
        sa.Column("ocr_adapter", sa.Text(), nullable=True),
        sa.Column("merchant_name", sa.Text(), nullable=True),
        sa.Column("purchased_on", sa.Date(), nullable=False),
        sa.Column(
            "currency_code", sa.String(length=3), nullable=False, server_default=sa.text("'INR'")
        ),
        sa.Column(
            "total_amount_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("raw_ocr_payload", sa.JSON(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_purchases_household_id_households",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_purchases_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'discarded')",
            name="ck_purchases_status",
        ),
        sa.CheckConstraint("total_amount_paise >= 0", name="ck_purchases_total_non_neg"),
    )
    op.create_index(
        "idx_purchases_household_status",
        "purchases",
        ["household_id", "status", "created_at"],
    )

    op.create_table(
        "purchase_items",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("purchase_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("canonical_item_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False, server_default=sa.text("'general'")),
        sa.Column(
            "match_confidence",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unmatched'"),
        ),
        sa.Column("normalized_qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("base_unit", sa.String(length=8), nullable=False),
        sa.Column("unit_price_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("line_total_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["purchase_id"],
            ["purchases.id"],
            name="fk_purchase_items_purchase_id_purchases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_purchase_items_household_id_households",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_item_id"],
            ["canonical_items.id"],
            name="fk_purchase_items_canonical_item_id_canonical_items",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("normalized_qty > 0", name="ck_purchase_items_qty_pos"),
        sa.CheckConstraint("base_unit IN ('g', 'ml', 'pcs')", name="ck_purchase_items_base_unit"),
        sa.CheckConstraint("unit_price_paise >= 0", name="ck_purchase_items_unit_price_non_neg"),
        sa.CheckConstraint("line_total_paise >= 0", name="ck_purchase_items_line_total_non_neg"),
    )
    op.create_index("idx_purchase_items_purchase", "purchase_items", ["purchase_id", "line_index"])

    op.create_table(
        "inventory_lots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("canonical_item_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("purchase_item_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("initial_qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("remaining_qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("base_unit", sa.String(length=8), nullable=False),
        sa.Column("unit_cost_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column(
            "lot_status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_inventory_lots_household_id_households",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_item_id"],
            ["canonical_items.id"],
            name="fk_inventory_lots_canonical_item_id_canonical_items",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_item_id"],
            ["purchase_items.id"],
            name="fk_inventory_lots_purchase_item_id_purchase_items",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("purchase_item_id", name="uq_inventory_lots_purchase_item_id"),
        sa.CheckConstraint("initial_qty > 0", name="ck_inventory_lots_initial_pos"),
        sa.CheckConstraint(
            "remaining_qty >= 0 AND remaining_qty <= initial_qty",
            name="ck_inventory_lots_remaining_bounds",
        ),
        sa.CheckConstraint("base_unit IN ('g', 'ml', 'pcs')", name="ck_inventory_lots_base_unit"),
        sa.CheckConstraint(
            "lot_status IN ('active', 'depleted', 'expired', 'written_off')",
            name="ck_inventory_lots_status",
        ),
    )
    op.create_index(
        "idx_inventory_lots_fefo",
        "inventory_lots",
        ["household_id", "canonical_item_id", "lot_status", "expires_on", "purchased_at", "id"],
    )

    op.create_table(
        "stock_ledger",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("canonical_item_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("quantity_delta", sa.Numeric(12, 3), nullable=False),
        sa.Column("base_unit", sa.String(length=8), nullable=False),
        sa.Column("balance_after_lot", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_cost_paise", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("reference_type", sa.String(length=32), nullable=True),
        sa.Column("reference_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_stock_ledger_household_id_households",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["inventory_lots.id"],
            name="fk_stock_ledger_lot_id_inventory_lots",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_item_id"],
            ["canonical_items.id"],
            name="fk_stock_ledger_canonical_item_id_canonical_items",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_stock_ledger_actor_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "event_type IN ('PURCHASE_IN', 'COOK_OUT', 'ADJUSTMENT_IN', "
            "'ADJUSTMENT_OUT', 'WASTE_OUT')",
            name="ck_stock_ledger_event_type",
        ),
        sa.CheckConstraint("quantity_delta <> 0", name="ck_stock_ledger_delta_nonzero"),
        sa.CheckConstraint("balance_after_lot >= 0", name="ck_stock_ledger_balance_non_neg"),
    )
    op.create_index(
        "idx_stock_ledger_household_created",
        "stock_ledger",
        ["household_id", "created_at"],
    )
    op.create_index("idx_stock_ledger_lot", "stock_ledger", ["lot_id", "created_at"])

    op.create_table(
        "cooking_plans",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("recipe_id", sa.Text(), nullable=True),
        sa.Column("recipe_title", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'planned'")
        ),
        sa.Column("planned_servings", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.Column("ingredients", sa.JSON(), nullable=False),
        sa.Column("deductions", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_cooking_plans_household_id_households",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_cooking_plans_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'in_progress', 'completed', 'cancelled')",
            name="ck_cooking_plans_status",
        ),
        sa.CheckConstraint("planned_servings > 0", name="ck_cooking_plans_servings_pos"),
    )
    op.create_index(
        "idx_cooking_plans_household_status",
        "cooking_plans",
        ["household_id", "status", "started_at"],
    )

    if is_postgres:
        op.execute(
            """
            CREATE OR REPLACE FUNCTION prevent_stock_ledger_mutation()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION
                    'stock_ledger is append-only; UPDATE and DELETE are forbidden'
                    USING ERRCODE = '42501';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_stock_ledger_immutable
                BEFORE UPDATE OR DELETE ON stock_ledger
                FOR EACH ROW
                EXECUTE FUNCTION prevent_stock_ledger_mutation();
            """
        )

        for table in _TENANT_ONLY_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
            op.execute(
                f"""
                CREATE POLICY tenant_isolation_{table} ON {table}
                    USING (
                        household_id = NULLIF(
                            current_setting('app.current_household_id', true), ''
                        )::uuid
                    )
                    WITH CHECK (
                        household_id = NULLIF(
                            current_setting('app.current_household_id', true), ''
                        )::uuid
                    );
                """
            )

        for table in _SHARED_CATALOG_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
            op.execute(
                f"""
                CREATE POLICY {table}_read ON {table} FOR SELECT
                    USING (
                        household_id IS NULL
                        OR household_id = NULLIF(
                            current_setting('app.current_household_id', true), ''
                        )::uuid
                    );
                """
            )
            op.execute(
                f"""
                CREATE POLICY {table}_write ON {table} FOR ALL
                    USING (
                        household_id = NULLIF(
                            current_setting('app.current_household_id', true), ''
                        )::uuid
                    )
                    WITH CHECK (
                        household_id = NULLIF(
                            current_setting('app.current_household_id', true), ''
                        )::uuid
                    );
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _SHARED_CATALOG_TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_write ON {table};")
            op.execute(f"DROP POLICY IF EXISTS {table}_read ON {table};")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

        for table in _TENANT_ONLY_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

        op.execute("DROP TRIGGER IF EXISTS trg_stock_ledger_immutable ON stock_ledger;")
        op.execute("DROP FUNCTION IF EXISTS prevent_stock_ledger_mutation();")

    op.drop_index("idx_cooking_plans_household_status", table_name="cooking_plans")
    op.drop_table("cooking_plans")
    op.drop_index("idx_stock_ledger_lot", table_name="stock_ledger")
    op.drop_index("idx_stock_ledger_household_created", table_name="stock_ledger")
    op.drop_table("stock_ledger")
    op.drop_index("idx_inventory_lots_fefo", table_name="inventory_lots")
    op.drop_table("inventory_lots")
    op.drop_index("idx_purchase_items_purchase", table_name="purchase_items")
    op.drop_table("purchase_items")
    op.drop_index("idx_purchases_household_status", table_name="purchases")
    op.drop_table("purchases")
    op.drop_index("idx_item_aliases_canonical", table_name="item_aliases")
    op.drop_index("uq_item_aliases_household", table_name="item_aliases")
    op.drop_index("uq_item_aliases_global", table_name="item_aliases")
    op.drop_table("item_aliases")
    op.drop_index("uq_canonical_items_household_slug", table_name="canonical_items")
    op.drop_index("uq_canonical_items_global_slug", table_name="canonical_items")
    op.drop_table("canonical_items")
