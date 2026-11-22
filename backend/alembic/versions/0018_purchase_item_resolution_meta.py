"""add resolution_metadata_json to purchase_items for Phase 7.4

Revision ID: 0018_purchase_item_resolution_meta
Revises: 0017_catalogue_variants
Create Date: 2026-11-21 19:15:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018_purchase_item_resolution_meta"
down_revision = "0017_catalogue_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_items", sa.Column("resolution_metadata_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("purchase_items", "resolution_metadata_json")
