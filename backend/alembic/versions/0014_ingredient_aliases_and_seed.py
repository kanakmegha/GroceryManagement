"""add ingredient_aliases table and expand canonical_ingredients seed

Revision ID: 0014_ingredient_aliases_and_seed
Revises: 0013_product_identity_provenance
Create Date: 2026-11-21

"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

from alembic import op
from app.modules.catalog.ingredient_identity_service import CANONICAL_NAMING_DICTIONARY

revision: str = "0014_ingredient_aliases_and_seed"
down_revision: str | Sequence[str] | None = "0013_product_identity_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_NAMESPACE = uuid.UUID("a3f1c8e2-4b6d-4f1a-9e3c-2b7d8e9f0a1b")
_ALIAS_NAMESPACE = uuid.UUID("b4e2d9f3-5c7e-4a2b-8f4d-3c8e9f0a1b2c")
_SEED_JSON_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "modules" / "catalog" / "seed_ingredients.json"
)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    ingredient_aliases_tbl = op.create_table(
        "ingredient_aliases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("canonical_ingredient_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column(
            "language", sa.String(length=16), nullable=False, server_default=sa.text("'en'")
        ),
        sa.Column(
            "alias_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'synonym'"),
        ),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'catalogue'"),
        ),
        sa.Column(
            "confidence",
            sa.Numeric(precision=4, scale=3),
            nullable=False,
            server_default=sa.text("1.000"),
        ),
        sa.Column(
            "is_approved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_ingredient_aliases_household_id_households",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_ingredient_id"],
            ["canonical_ingredients.id"],
            name="fk_ingredient_aliases_canonical_ingredient_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "alias_type IN "
            "('synonym', 'transliteration', 'regional_name', 'spelling_variant', 'ocr_variant')",
            name="ck_ingredient_aliases_type",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_ingredient_aliases_confidence",
        ),
    )
    op.create_index(
        "uq_ingredient_aliases_global_norm",
        "ingredient_aliases",
        ["normalized_alias"],
        unique=True,
        sqlite_where=sa.text("household_id IS NULL"),
        postgresql_where=sa.text("household_id IS NULL"),
    )
    op.create_index(
        "uq_ingredient_aliases_household_norm",
        "ingredient_aliases",
        ["household_id", "normalized_alias"],
        unique=True,
        sqlite_where=sa.text("household_id IS NOT NULL"),
        postgresql_where=sa.text("household_id IS NOT NULL"),
    )
    op.create_index(
        "ix_ingredient_aliases_ingredient",
        "ingredient_aliases",
        ["canonical_ingredient_id"],
        unique=False,
    )
    op.create_index(
        "ix_ingredient_aliases_household",
        "ingredient_aliases",
        ["household_id"],
        unique=False,
    )

    if is_postgres:
        op.execute("ALTER TABLE ingredient_aliases ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE ingredient_aliases FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY ingredient_aliases_read ON ingredient_aliases FOR SELECT
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
            CREATE POLICY ingredient_aliases_write ON ingredient_aliases FOR ALL
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

    now = datetime.now(UTC)

    # Idempotently seed any newly added CanonicalIngredients (preserving existing 43 rows)
    existing_ings = {
        row[1]: row[0]
        for row in bind.execute(
            sa.text("SELECT id, normalized_name FROM canonical_ingredients")
        ).fetchall()
    }
    if _SEED_JSON_PATH.exists():
        records = json.loads(_SEED_JSON_PATH.read_text(encoding="utf-8"))
        canonical_ingredients_tbl = sa.table(
            "canonical_ingredients",
            sa.column("id", sa.Uuid(as_uuid=True)),
            sa.column("name", sa.Text()),
            sa.column("normalized_name", sa.Text()),
            sa.column("category", sa.String(64)),
            sa.column("subcategory", sa.String(64)),
            sa.column("standard_unit", sa.String(8)),
            sa.column("cooking_variants", sa.JSON()),
            sa.column("supported_units", sa.JSON()),
            sa.column("is_active", sa.Boolean()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        new_ing_rows = []
        for rec in records:
            norm = rec["normalized_name"]
            if norm in existing_ings:
                continue
            ing_id = uuid.uuid5(_SEED_NAMESPACE, norm)
            new_ing_rows.append(
                {
                    "id": ing_id,
                    "name": rec["name"],
                    "normalized_name": norm,
                    "category": rec["category"],
                    "subcategory": rec.get("subcategory"),
                    "standard_unit": rec["standard_unit"],
                    "cooking_variants": rec.get("cooking_variants") or [],
                    "supported_units": rec.get("supported_units") or [rec["standard_unit"]],
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            existing_ings[norm] = ing_id
        if new_ing_rows:
            op.bulk_insert(canonical_ingredients_tbl, new_ing_rows)

    # Idempotently seed global IngredientAliases from CANONICAL_NAMING_DICTIONARY
    alias_rows = []
    seen_norms: set[str] = set()
    for phrase, (target_norm, _pref_name, lang) in CANONICAL_NAMING_DICTIONARY.items():
        ing_id_raw = existing_ings.get(target_norm)
        if ing_id_raw is None:
            continue
        ing_uuid = ing_id_raw if isinstance(ing_id_raw, uuid.UUID) else uuid.UUID(str(ing_id_raw))
        norm_alias = phrase.strip().lower()
        if not norm_alias or norm_alias in seen_norms:
            continue
        seen_norms.add(norm_alias)
        alias_rows.append(
            {
                "id": uuid.uuid5(_ALIAS_NAMESPACE, norm_alias),
                "household_id": None,
                "canonical_ingredient_id": ing_uuid,
                "alias": phrase,
                "normalized_alias": norm_alias,
                "language": lang,
                "alias_type": "transliteration" if lang != "en" else "synonym",
                "source": "catalogue",
                "confidence": Decimal("1.000"),
                "is_approved": True,
                "created_at": now,
                "updated_at": now,
            }
        )
    if alias_rows:
        op.bulk_insert(ingredient_aliases_tbl, alias_rows)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS ingredient_aliases_write ON ingredient_aliases;")
        op.execute("DROP POLICY IF EXISTS ingredient_aliases_read ON ingredient_aliases;")
        op.execute("ALTER TABLE ingredient_aliases NO FORCE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE ingredient_aliases DISABLE ROW LEVEL SECURITY;")

    op.drop_index("ix_ingredient_aliases_household", table_name="ingredient_aliases")
    op.drop_index("ix_ingredient_aliases_ingredient", table_name="ingredient_aliases")
    op.drop_index("uq_ingredient_aliases_household_norm", table_name="ingredient_aliases")
    op.drop_index("uq_ingredient_aliases_global_norm", table_name="ingredient_aliases")
    op.drop_table("ingredient_aliases")
