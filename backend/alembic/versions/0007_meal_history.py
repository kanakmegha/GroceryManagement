"""add meal_history table and cooking_plans recipe metadata columns

Revision ID: 0007_meal_history
Revises: 0006_food_catalogue_class
Create Date: 2026-11-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_meal_history"
down_revision: str | Sequence[str] | None = "0006_food_catalogue_class"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cooking_plans") as batch_op:
        batch_op.add_column(sa.Column("meal_type", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("cuisine", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("prep_minutes", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cook_minutes", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("recipe_snapshot", sa.JSON(), nullable=True))

    op.create_table(
        "meal_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("cooking_plan_id", sa.Uuid(), nullable=False),
        sa.Column("cooked_by", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Text(), nullable=True),
        sa.Column("recipe_title", sa.Text(), nullable=False),
        sa.Column("meal_type", sa.String(length=32), nullable=True),
        sa.Column("cuisine", sa.String(length=64), nullable=True),
        sa.Column("servings", sa.Integer(), server_default=sa.text("2"), nullable=False),
        sa.Column("prep_minutes", sa.Integer(), nullable=True),
        sa.Column("cook_minutes", sa.Integer(), nullable=True),
        sa.Column("ingredients_used", sa.JSON(), nullable=False),
        sa.Column("substitutions", sa.JSON(), nullable=False),
        sa.Column("recipe_snapshot", sa.JSON(), nullable=True),
        sa.Column("cooked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("feedback_tags", sa.JSON(), nullable=False),
        sa.Column("feedback_comment", sa.Text(), nullable=True),
        sa.Column("would_make_again", sa.Boolean(), nullable=True),
        sa.Column("feedback_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cooking_plan_id"], ["cooking_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cooked_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cooking_plan_id"),
    )
    op.create_index(
        "ix_meal_history_household_cooked_at", "meal_history", ["household_id", "cooked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_meal_history_household_cooked_at", table_name="meal_history")
    op.drop_table("meal_history")

    with op.batch_alter_table("cooking_plans") as batch_op:
        batch_op.drop_column("recipe_snapshot")
        batch_op.drop_column("cook_minutes")
        batch_op.drop_column("prep_minutes")
        batch_op.drop_column("cuisine")
        batch_op.drop_column("meal_type")
