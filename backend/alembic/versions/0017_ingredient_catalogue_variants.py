"""add family/variant columns to canonical_ingredients and household learning fields
to ingredient_aliases

Revision ID: 0017_catalogue_variants
Revises: 0016_recipe_feedback
Create Date: 2026-11-21 17:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_catalogue_variants"
down_revision = "0016_recipe_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "canonical_ingredients", sa.Column("family_id", sa.String(length=128), nullable=True)
    )
    op.add_column("canonical_ingredients", sa.Column("family_name", sa.Text(), nullable=True))
    op.add_column(
        "canonical_ingredients", sa.Column("variant_id", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "canonical_ingredients", sa.Column("variant_status", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "canonical_ingredients",
        sa.Column(
            "is_unspecified_variant",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "canonical_ingredients",
        sa.Column("attributes_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "ix_canonical_ingredients_family_id",
        "canonical_ingredients",
        ["family_id"],
        unique=False,
    )
    op.create_index(
        "uq_canonical_ingredients_variant_id",
        "canonical_ingredients",
        ["variant_id"],
        unique=True,
        sqlite_where=sa.text("variant_id IS NOT NULL"),
        postgresql_where=sa.text("variant_id IS NOT NULL"),
    )

    op.add_column("ingredient_aliases", sa.Column("original_phrase", sa.Text(), nullable=True))
    op.add_column(
        "ingredient_aliases", sa.Column("target_family_id", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "ingredient_aliases", sa.Column("target_variant_id", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "ingredient_aliases",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingredient_aliases",
        sa.Column(
            "confirmation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column("ingredient_aliases", sa.Column("provenance", sa.Text(), nullable=True))

    with op.batch_alter_table("ingredient_aliases") as batch_op:
        batch_op.drop_constraint("ck_ingredient_aliases_type", type_="check")
        batch_op.create_check_constraint(
            "ck_ingredient_aliases_type",
            "alias_type IN ('synonym', 'transliteration', 'regional_name', "
            "'spelling_variant', 'ocr_variant', 'user_correction')",
        )


def downgrade() -> None:
    with op.batch_alter_table("ingredient_aliases") as batch_op:
        batch_op.drop_constraint("ck_ingredient_aliases_type", type_="check")
        batch_op.create_check_constraint(
            "ck_ingredient_aliases_type",
            "alias_type IN ('synonym', 'transliteration', 'regional_name', "
            "'spelling_variant', 'ocr_variant')",
        )

    op.drop_column("ingredient_aliases", "provenance")
    op.drop_column("ingredient_aliases", "confirmation_count")
    op.drop_column("ingredient_aliases", "confirmed_at")
    op.drop_column("ingredient_aliases", "target_variant_id")
    op.drop_column("ingredient_aliases", "target_family_id")
    op.drop_column("ingredient_aliases", "original_phrase")

    op.drop_index("uq_canonical_ingredients_variant_id", table_name="canonical_ingredients")
    op.drop_index("ix_canonical_ingredients_family_id", table_name="canonical_ingredients")
    op.drop_column("canonical_ingredients", "attributes_json")
    op.drop_column("canonical_ingredients", "is_unspecified_variant")
    op.drop_column("canonical_ingredients", "variant_status")
    op.drop_column("canonical_ingredients", "variant_id")
    op.drop_column("canonical_ingredients", "family_name")
    op.drop_column("canonical_ingredients", "family_id")
