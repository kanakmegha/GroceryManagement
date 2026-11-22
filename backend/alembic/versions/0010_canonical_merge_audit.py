"""add canonical_merge_log and soft-merge columns on canonical_items

Revision ID: 0010_canonical_merge_audit
Revises: 0009_smart_replenishment
Create Date: 2026-11-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_canonical_merge_audit"
down_revision: str | Sequence[str] | None = "0009_smart_replenishment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("canonical_items") as batch_op:
        batch_op.add_column(sa.Column("merged_into_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true"))
        )
        batch_op.create_foreign_key(
            "fk_canonical_items_merged_into_id",
            "canonical_items",
            ["merged_into_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "canonical_merge_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("duplicate_canonical_id", sa.Uuid(), nullable=False),
        sa.Column("survivor_canonical_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("merged_by", sa.Uuid(), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["duplicate_canonical_id"], ["canonical_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["survivor_canonical_id"], ["canonical_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["merged_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_merge_log_household_id",
        "canonical_merge_log",
        ["household_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_canonical_merge_log_household_id", table_name="canonical_merge_log")
    op.drop_table("canonical_merge_log")
    with op.batch_alter_table("canonical_items") as batch_op:
        batch_op.drop_constraint("fk_canonical_items_merged_into_id", type_="foreignkey")
        batch_op.drop_column("is_active")
        batch_op.drop_column("merged_into_id")
