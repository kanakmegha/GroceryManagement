from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Dish(Base):
    """Canonical global Indian dish definition for pantry-first discovery."""

    __tablename__ = "dishes"
    __table_args__ = (
        Index("ix_dishes_cuisine_region", "cuisine", "region"),
        Index("ix_dishes_is_active", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    alternative_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    regional_names: Mapped[dict[str, list[str]]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    cuisine: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Indian", server_default=text("'Indian'")
    )
    region: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Pan-Indian", server_default=text("'Pan-Indian'")
    )
    meal_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    diet_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    prep_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cook_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_servings: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default=text("2")
    )
    search_terms: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    catalogue_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1.0.0", server_default=text("'1.0.0'")
    )
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    ingredients: Mapped[list[DishIngredient]] = relationship(
        back_populates="dish",
        cascade="all, delete-orphan",
        order_by="DishIngredient.sort_order",
    )


class DishIngredient(Base):
    """Normalized ingredient requirement row for a canonical dish."""

    __tablename__ = "dish_ingredients"
    __table_args__ = (
        Index("ix_dish_ingredients_dish_id", "dish_id"),
        Index("ix_dish_ingredients_variant_id", "variant_id"),
        Index("ix_dish_ingredients_family_id", "family_id"),
        CheckConstraint(
            "requirement_mode IN ('exact_variant', 'compatible_family', 'generic_ingredient')",
            name="ck_dish_ingredients_req_mode",
        ),
        CheckConstraint(
            "requirement_type IN ('required', 'optional')",
            name="ck_dish_ingredients_req_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dish_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("dishes.id", ondelete="CASCADE"), nullable=False
    )
    canonical_ingredient_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("canonical_ingredients.id", ondelete="SET NULL"),
        nullable=True,
    )
    ingredient_name: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    variant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    family_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    compatible_variant_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    substitutions_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    dish: Mapped[Dish] = relationship(back_populates="ingredients")


class VideoRecord(Base):
    """Deduplicated external video metadata record (YouTube-first)."""

    __tablename__ = "video_records"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_video_id",
            name="uq_video_records_provider_external_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="youtube", server_default=text("'youtube'")
    )
    external_video_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_name: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_url: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    view_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class DishVideoCache(Base):
    """7-day cache + distributed refresh lease coordination per (dish_id, query_hash)."""

    __tablename__ = "dish_video_cache"
    __table_args__ = (
        UniqueConstraint(
            "dish_id",
            "query_hash",
            name="uq_dish_video_cache_dish_query",
        ),
        Index("ix_dish_video_cache_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    dish_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("dishes.id", ondelete="CASCADE"), nullable=False
    )
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    video_record_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    refresh_lock_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    refresh_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class YouTubeQuotaUsage(Base):
    """Daily YouTube API quota accounting scoped by (quota_date, quota_project_key)."""

    __tablename__ = "youtube_quota_usage"
    __table_args__ = (
        UniqueConstraint(
            "quota_date",
            "quota_project_key",
            name="uq_youtube_quota_date_project",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    quota_date: Mapped[date] = mapped_column(Date, nullable=False)
    quota_project_key: Mapped[str] = mapped_column(String(64), nullable=False)
    units_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    search_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    video_detail_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class SavedDish(Base):
    """Household-scoped saved/bookmarked dish (PostgreSQL RLS protected)."""

    __tablename__ = "saved_dishes"
    __table_args__ = (
        UniqueConstraint(
            "household_id",
            "dish_id",
            name="uq_saved_dishes_household_dish",
        ),
        Index("ix_saved_dishes_household_id", "household_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    dish_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("dishes.id", ondelete="RESTRICT"), nullable=False
    )
    saved_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class DishFeedback(Base):
    """Household-scoped dish feedback and cooking log (PostgreSQL RLS protected)."""

    __tablename__ = "dish_feedback"
    __table_args__ = (
        Index("ix_dish_feedback_household_dish", "household_id", "dish_id"),
        Index("ix_dish_feedback_household_action", "household_id", "action"),
        CheckConstraint(
            "action IN ('like', 'dislike', 'cooked', 'hide')",
            name="ck_dish_feedback_action",
        ),
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_dish_feedback_rating",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    dish_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("dishes.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
