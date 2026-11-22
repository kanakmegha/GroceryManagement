"""add canonical_ingredients and ingredient_mappings foundation

Revision ID: 0012_ingredient_first_foundation
Revises: 0011_classification_v2_fields
Create Date: 2026-11-20

"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "0012_ingredient_first_foundation"
down_revision: str | Sequence[str] | None = "0011_classification_v2_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_NAMESPACE = uuid.UUID("a3f1c8e2-4b6d-4f1a-9e3c-2b7d8e9f0a1b")
_SEED_JSON_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "modules" / "catalog" / "seed_ingredients.json"
)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    canonical_ingredients_tbl = op.create_table(
        "canonical_ingredients",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("subcategory", sa.String(length=64), nullable=True),
        sa.Column("standard_unit", sa.String(length=8), nullable=False),
        sa.Column("cooking_variants", sa.JSON(), nullable=False),
        sa.Column("supported_units", sa.JSON(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "standard_unit IN ('g', 'ml', 'pcs')",
            name="ck_canonical_ingredients_standard_unit",
        ),
    )
    op.create_index(
        "uq_canonical_ingredients_normalized_name",
        "canonical_ingredients",
        ["normalized_name"],
        unique=True,
    )
    op.create_index(
        "ix_canonical_ingredients_category",
        "canonical_ingredients",
        ["category", "subcategory"],
        unique=False,
    )

    op.create_table(
        "ingredient_mappings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("canonical_item_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("canonical_ingredient_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "matching_confidence",
            sa.Numeric(precision=4, scale=3),
            nullable=False,
            server_default=sa.text("1.000"),
        ),
        sa.Column(
            "mapping_source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'catalogue'"),
        ),
        sa.Column(
            "user_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_ingredient_mappings_household_id_households",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_item_id"],
            ["canonical_items.id"],
            name="fk_ingredient_mappings_canonical_item_id_canonical_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_ingredient_id"],
            ["canonical_ingredients.id"],
            name="fk_ingredient_mappings_canonical_ingredient_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "matching_confidence >= 0 AND matching_confidence <= 1",
            name="ck_ingredient_mappings_confidence",
        ),
        sa.CheckConstraint(
            "mapping_source IN "
            "('catalogue', 'classifier_v2', 'user_confirmed', 'user_correction', 'manual')",
            name="ck_ingredient_mappings_source",
        ),
    )
    op.create_index(
        "uq_ingredient_mappings_global_item",
        "ingredient_mappings",
        ["canonical_item_id"],
        unique=True,
        sqlite_where=sa.text("household_id IS NULL"),
        postgresql_where=sa.text("household_id IS NULL"),
    )
    op.create_index(
        "uq_ingredient_mappings_household_item",
        "ingredient_mappings",
        ["household_id", "canonical_item_id"],
        unique=True,
        sqlite_where=sa.text("household_id IS NOT NULL"),
        postgresql_where=sa.text("household_id IS NOT NULL"),
    )
    op.create_index(
        "ix_ingredient_mappings_ingredient",
        "ingredient_mappings",
        ["canonical_ingredient_id"],
        unique=False,
    )
    op.create_index(
        "ix_ingredient_mappings_household",
        "ingredient_mappings",
        ["household_id"],
        unique=False,
    )

    if is_postgres:
        op.execute("ALTER TABLE ingredient_mappings ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE ingredient_mappings FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY ingredient_mappings_read ON ingredient_mappings FOR SELECT
                USING (
                    household_id IS NULL
                    OR household_id = NULLIF(
                        current_setting('app.current_household_id', true), ''
                    )::uuid
                );
            """
        )
        op.execute(
            """
            CREATE POLICY ingredient_mappings_write ON ingredient_mappings FOR ALL
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

    if _SEED_JSON_PATH.exists():
        records = json.loads(_SEED_JSON_PATH.read_text(encoding="utf-8"))
        now = datetime.now(UTC)
        rows = [
            {
                "id": uuid.uuid5(_SEED_NAMESPACE, rec["normalized_name"]),
                "name": rec["name"],
                "normalized_name": rec["normalized_name"],
                "category": rec["category"],
                "subcategory": rec.get("subcategory"),
                "standard_unit": rec["standard_unit"],
                "cooking_variants": rec.get("cooking_variants") or [],
                "supported_units": rec.get("supported_units") or [rec["standard_unit"]],
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
            for rec in records
        ]
        if rows:
            op.bulk_insert(canonical_ingredients_tbl, rows)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS ingredient_mappings_write ON ingredient_mappings;")
        op.execute("DROP POLICY IF EXISTS ingredient_mappings_read ON ingredient_mappings;")
        op.execute("ALTER TABLE ingredient_mappings NO FORCE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE ingredient_mappings DISABLE ROW LEVEL SECURITY;")

    op.drop_index("ix_ingredient_mappings_household", table_name="ingredient_mappings")
    op.drop_index("ix_ingredient_mappings_ingredient", table_name="ingredient_mappings")
    op.drop_index("uq_ingredient_mappings_household_item", table_name="ingredient_mappings")
    op.drop_index("uq_ingredient_mappings_global_item", table_name="ingredient_mappings")
    op.drop_table("ingredient_mappings")

    op.drop_index("ix_canonical_ingredients_category", table_name="canonical_ingredients")
    op.drop_index("uq_canonical_ingredients_normalized_name", table_name="canonical_ingredients")
    op.drop_table("canonical_ingredients")
