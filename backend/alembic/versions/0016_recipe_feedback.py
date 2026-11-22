"""add recipe_feedback table

Revision ID: 0016_recipe_feedback
Revises: 0015_recipe_prefs
Create Date: 2026-11-21
"""

import sqlalchemy as sa

from alembic import op

revision = "0016_recipe_feedback"
down_revision = "0015_recipe_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recipe_feedback",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "household_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipe_id", sa.String(length=128), nullable=False),
        sa.Column("recipe_title", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_recipe_feedback_hh_recipe", "recipe_feedback", ["household_id", "recipe_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_recipe_feedback_hh_recipe", table_name="recipe_feedback")
    op.drop_table("recipe_feedback")
