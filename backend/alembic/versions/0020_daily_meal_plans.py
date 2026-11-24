"""Add daily_meal_plans and daily_meal_plan_recipes tables.

Revision ID: 0020_daily_meal_plans
Revises: 0019_pantry_discovery_tables
Create Date: 2026-11-23 17:05:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0020_daily_meal_plans"
down_revision: str | None = "0019_pantry_discovery_tables"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_meal_plans",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "household_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'Asia/Kolkata'"),
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "auto_attempted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "manual_regeneration_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("context_snapshot", sa.JSON(), nullable=False),
        sa.Column("weather_snapshot", sa.JSON(), nullable=True),
        sa.Column("provider_metadata", sa.JSON(), nullable=False),
        sa.Column("generation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "household_id",
            "local_date",
            name="uq_daily_meal_plans_household_date",
        ),
    )
    op.create_index(
        "ix_daily_meal_plans_status_date",
        "daily_meal_plans",
        ["status", "local_date"],
    )

    op.create_table(
        "daily_meal_plan_recipes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "daily_meal_plan_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("daily_meal_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("meal_category", sa.String(length=24), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("recipe_data", sa.JSON(), nullable=False),
        sa.Column("availability_data", sa.JSON(), nullable=False),
        sa.Column(
            "quality_status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'valid'"),
        ),
        sa.Column("quality_warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "daily_meal_plan_id",
            "meal_category",
            "position",
            name="uq_daily_meal_plan_recipes_slot",
        ),
    )
    op.create_index(
        "ix_daily_meal_plan_recipes_plan_id",
        "daily_meal_plan_recipes",
        ["daily_meal_plan_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_daily_meal_plan_recipes_plan_id", table_name="daily_meal_plan_recipes")
    op.drop_table("daily_meal_plan_recipes")
    op.drop_index("ix_daily_meal_plans_status_date", table_name="daily_meal_plans")
    op.drop_table("daily_meal_plans")
