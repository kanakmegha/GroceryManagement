"""Add preferences_completed_at to household_recipe_preferences.

Revision ID: 0023_preferences_onboarding_completion
Revises: 0022_daily_plan_generation_leases
Create Date: 2026-11-25 21:22:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0023_preferences_onboarding_completion"
down_revision: str | None = "0022_daily_plan_generation_leases"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "household_recipe_preferences",
        sa.Column("preferences_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill existing persisted preference rows without altering any saved preference values.
    # Households without a preference row are intentionally NOT given synthetic rows; existing
    # households that already have a ready daily_meal_plans row are preserved at the service layer.
    op.execute(
        sa.text(
            "UPDATE household_recipe_preferences "
            "SET preferences_completed_at = COALESCE(updated_at, CURRENT_TIMESTAMP) "
            "WHERE preferences_completed_at IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("household_recipe_preferences", "preferences_completed_at")
