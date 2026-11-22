"""Add pantry discovery tables, indexes, and PostgreSQL RLS policies.

Revision ID: 0019_pantry_discovery_tables
Revises: 0018_purchase_item_resolution_meta
Create Date: 2026-11-22 22:50:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0019_pantry_discovery_tables"
down_revision: str | None = "0018_purchase_item_resolution_meta"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "dishes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False, unique=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("alternative_names", sa.JSON(), nullable=False),
        sa.Column("regional_names", sa.JSON(), nullable=False),
        sa.Column(
            "cuisine",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'Indian'"),
        ),
        sa.Column(
            "region",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'Pan-Indian'"),
        ),
        sa.Column("meal_types", sa.JSON(), nullable=False),
        sa.Column("diet_tags", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("prep_minutes", sa.Integer(), nullable=True),
        sa.Column("cook_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "default_servings",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
        ),
        sa.Column("search_terms", sa.JSON(), nullable=False),
        sa.Column(
            "catalogue_version",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'1.0.0'"),
        ),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dishes_cuisine_region", "dishes", ["cuisine", "region"])
    op.create_index("ix_dishes_is_active", "dishes", ["is_active"])

    op.create_table(
        "dish_ingredients",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "dish_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("dishes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "canonical_ingredient_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("canonical_ingredients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ingredient_name", sa.Text(), nullable=False),
        sa.Column("requirement_mode", sa.String(length=32), nullable=False),
        sa.Column("requirement_type", sa.String(length=16), nullable=False),
        sa.Column("variant_id", sa.String(length=128), nullable=True),
        sa.Column("family_id", sa.String(length=128), nullable=True),
        sa.Column("compatible_variant_ids", sa.JSON(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("substitutions_json", sa.JSON(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint(
            "requirement_mode IN ('exact_variant', 'compatible_family', 'generic_ingredient')",
            name="ck_dish_ingredients_req_mode",
        ),
        sa.CheckConstraint(
            "requirement_type IN ('required', 'optional')",
            name="ck_dish_ingredients_req_type",
        ),
    )
    op.create_index("ix_dish_ingredients_dish_id", "dish_ingredients", ["dish_id"])
    op.create_index("ix_dish_ingredients_variant_id", "dish_ingredients", ["variant_id"])
    op.create_index("ix_dish_ingredients_family_id", "dish_ingredients", ["family_id"])

    op.create_table(
        "video_records",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'youtube'"),
        ),
        sa.Column("external_video_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.String(length=128), nullable=True),
        sa.Column("channel_name", sa.Text(), nullable=False),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("watch_url", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "external_video_id",
            name="uq_video_records_provider_external_id",
        ),
    )

    op.create_table(
        "dish_video_cache",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "dish_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("dishes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("video_record_ids", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("refresh_lock_token", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("refresh_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("dish_id", "query_hash", name="uq_dish_video_cache_dish_query"),
    )
    op.create_index("ix_dish_video_cache_expires_at", "dish_video_cache", ["expires_at"])

    op.create_table(
        "youtube_quota_usage",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("quota_project_key", sa.String(length=64), nullable=False),
        sa.Column("units_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("search_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "video_detail_calls",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "quota_date",
            "quota_project_key",
            name="uq_youtube_quota_date_project",
        ),
    )

    op.create_table(
        "saved_dishes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "household_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dish_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("dishes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "saved_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "dish_id", name="uq_saved_dishes_household_dish"),
    )
    op.create_index("ix_saved_dishes_household_id", "saved_dishes", ["household_id"])

    op.create_table(
        "dish_feedback",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "household_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dish_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("dishes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('like', 'dislike', 'cooked', 'hide')",
            name="ck_dish_feedback_action",
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_dish_feedback_rating",
        ),
    )
    op.create_index(
        "ix_dish_feedback_household_dish",
        "dish_feedback",
        ["household_id", "dish_id"],
    )
    op.create_index(
        "ix_dish_feedback_household_action",
        "dish_feedback",
        ["household_id", "action"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE saved_dishes ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE saved_dishes FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_saved_dishes ON saved_dishes
            USING (
                household_id = NULLIF(
                    current_setting('app.current_household_id', true), ''
                )::uuid
            )
            WITH CHECK (
                household_id = NULLIF(
                    current_setting('app.current_household_id', true), ''
                )::uuid
            );
            """
        )
        op.execute("ALTER TABLE dish_feedback ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE dish_feedback FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY tenant_isolation_dish_feedback ON dish_feedback
            USING (
                household_id = NULLIF(
                    current_setting('app.current_household_id', true), ''
                )::uuid
            )
            WITH CHECK (
                household_id = NULLIF(
                    current_setting('app.current_household_id', true), ''
                )::uuid
            );
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table_name in ("dish_feedback", "saved_dishes"):
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name};")
            op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;")

    op.drop_index("ix_dish_feedback_household_action", table_name="dish_feedback")
    op.drop_index("ix_dish_feedback_household_dish", table_name="dish_feedback")
    op.drop_table("dish_feedback")

    op.drop_index("ix_saved_dishes_household_id", table_name="saved_dishes")
    op.drop_table("saved_dishes")

    op.drop_table("youtube_quota_usage")

    op.drop_index("ix_dish_video_cache_expires_at", table_name="dish_video_cache")
    op.drop_table("dish_video_cache")

    op.drop_table("video_records")

    op.drop_index("ix_dish_ingredients_family_id", table_name="dish_ingredients")
    op.drop_index("ix_dish_ingredients_variant_id", table_name="dish_ingredients")
    op.drop_index("ix_dish_ingredients_dish_id", table_name="dish_ingredients")
    op.drop_table("dish_ingredients")

    op.drop_index("ix_dishes_is_active", table_name="dishes")
    op.drop_index("ix_dishes_cuisine_region", table_name="dishes")
    op.drop_table("dishes")
