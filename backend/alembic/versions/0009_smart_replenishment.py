"""add shopping_list_items and replenishment_feedback tables

Revision ID: 0009_smart_replenishment
Revises: 0008_household_budgets
Create Date: 2026-11-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_smart_replenishment"
down_revision: str | Sequence[str] | None = "0008_household_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shopping_list_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_item_id", sa.Uuid(), nullable=True),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("primary_source", sa.String(length=32), nullable=False),
        sa.Column("sources_json", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["canonical_item_id"], ["canonical_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_shopping_list_household_status",
        "shopping_list_items",
        ["household_id", "status"],
    )
    op.create_index(
        "ix_shopping_list_household_canonical",
        "shopping_list_items",
        ["household_id", "canonical_item_id"],
    )
    op.create_index(
        "uq_shopping_list_pending_canonical",
        "shopping_list_items",
        ["household_id", "canonical_item_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending' AND canonical_item_id IS NOT NULL"),
        postgresql_where=sa.text("status = 'pending' AND canonical_item_id IS NOT NULL"),
    )

    op.create_table(
        "replenishment_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_item_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("remind_after", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("reason_snapshot", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["canonical_item_id"], ["canonical_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_replenishment_feedback_lookup",
        "replenishment_feedback",
        ["household_id", "canonical_item_id", "is_active", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_replenishment_feedback_lookup", table_name="replenishment_feedback")
    op.drop_table("replenishment_feedback")
    op.drop_index("uq_shopping_list_pending_canonical", table_name="shopping_list_items")
    op.drop_index("ix_shopping_list_household_canonical", table_name="shopping_list_items")
    op.drop_index("ix_shopping_list_household_status", table_name="shopping_list_items")
    op.drop_table("shopping_list_items")
