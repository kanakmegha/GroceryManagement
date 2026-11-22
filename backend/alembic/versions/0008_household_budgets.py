"""add household_budgets table for configurable period and category budgets

Revision ID: 0008_household_budgets
Revises: 0007_meal_history
Create Date: 2026-11-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_household_budgets"
down_revision: str | Sequence[str] | None = "0007_meal_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "household_budgets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("period_type", sa.String(length=16), nullable=False),
        sa.Column("overall_limit_paise", sa.Integer(), nullable=False),
        sa.Column("grocery_limit_paise", sa.Integer(), nullable=True),
        sa.Column("outside_food_limit_paise", sa.Integer(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "household_id",
            "period_type",
            "effective_from",
            name="uq_household_budgets_scope_effective",
        ),
    )
    op.create_index(
        "ix_household_budgets_lookup",
        "household_budgets",
        ["household_id", "period_type", "effective_from"],
    )


def downgrade() -> None:
    op.drop_index("ix_household_budgets_lookup", table_name="household_budgets")
    op.drop_table("household_budgets")
