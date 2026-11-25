from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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


class DailyMealPlan(Base):
    """Persistent daily AI meal plan (at most one automatic generation per household/day)."""

    __tablename__ = "daily_meal_plans"
    __table_args__ = (
        UniqueConstraint("household_id", "local_date", name="uq_daily_meal_plans_household_date"),
        Index("ix_daily_meal_plans_status_date", "status", "local_date"),
        Index("ix_daily_meal_plans_active_generation_id", "active_generation_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Kolkata", server_default=text("'Asia/Kolkata'")
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default=text("'pending'")
    )
    auto_attempted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    manual_regeneration_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    weather_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    generation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    generation_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_generation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    recipes: Mapped[list[DailyMealPlanRecipe]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="DailyMealPlanRecipe.meal_category, DailyMealPlanRecipe.position",
    )


class DailyMealPlanRecipe(Base):
    """Single validated recipe within a household's daily meal plan."""

    __tablename__ = "daily_meal_plan_recipes"
    __table_args__ = (
        UniqueConstraint(
            "daily_meal_plan_id",
            "meal_category",
            "position",
            name="uq_daily_meal_plan_recipes_slot",
        ),
        Index("ix_daily_meal_plan_recipes_plan_id", "daily_meal_plan_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    daily_meal_plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("daily_meal_plans.id", ondelete="CASCADE"), nullable=False
    )
    meal_category: Mapped[str] = mapped_column(String(24), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    recipe_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    availability_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    quality_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="valid", server_default=text("'valid'")
    )
    quality_warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    plan: Mapped[DailyMealPlan] = relationship(back_populates="recipes")
