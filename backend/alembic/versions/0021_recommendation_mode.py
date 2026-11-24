"""Add recommendation_mode and personalisation_prompt_dismissed_at to household_recipe_preferences."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_recommendation_mode"
down_revision = "0020_daily_meal_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "household_recipe_preferences",
        sa.Column(
            "recommendation_mode",
            sa.String(length=24),
            nullable=False,
            server_default="balanced",
        ),
    )
    op.add_column(
        "household_recipe_preferences",
        sa.Column("personalisation_prompt_dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("household_recipe_preferences", "personalisation_prompt_dismissed_at")
    op.drop_column("household_recipe_preferences", "recommendation_mode")
