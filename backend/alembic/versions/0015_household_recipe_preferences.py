"""add household_recipe_preferences table

Revision ID: 0015_recipe_prefs
Revises: 0014_ingredient_aliases_and_seed
Create Date: 2026-11-21
"""

import sqlalchemy as sa

from alembic import op

revision = "0015_recipe_prefs"
down_revision = "0014_ingredient_aliases_and_seed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "household_recipe_preferences",
        sa.Column(
            "household_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("location_name", sa.Text(), nullable=True),
        sa.Column("diet_type", sa.String(length=32), nullable=False, server_default="any"),
        sa.Column("dietary_restrictions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("preferred_cuisines", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("preferred_meal_types", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("max_prep_minutes", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("household_recipe_preferences")
