"""Add generation ownership lease columns to daily_meal_plans.

Revision ID: 0022_daily_plan_generation_leases
Revises: 0021_recommendation_mode
Create Date: 2026-11-25 13:25:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0022_daily_plan_generation_leases"
down_revision: str | None = "0021_recommendation_mode"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_meal_plans",
        sa.Column("active_generation_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "daily_meal_plans",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "daily_meal_plans",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_daily_meal_plans_active_generation_id",
        "daily_meal_plans",
        ["active_generation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_daily_meal_plans_active_generation_id", table_name="daily_meal_plans")
    op.drop_column("daily_meal_plans", "lease_expires_at")
    op.drop_column("daily_meal_plans", "last_heartbeat_at")
    op.drop_column("daily_meal_plans", "active_generation_id")
