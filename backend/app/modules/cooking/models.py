from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CookingPlan(Base):
    __tablename__ = "cooking_plans"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    recipe_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipe_title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="planned", server_default=text("'planned'")
    )
    planned_servings: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default=text("2")
    )
    ingredients: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    deductions: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    meal_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cuisine: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prep_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cook_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recipe_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MealHistory(Base):
    """Completed cooking session persisted ONLY on explicit cooking completion."""

    __tablename__ = "meal_history"
    __table_args__ = (
        Index("ix_meal_history_household_cooked_at", "household_id", "cooked_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    cooking_plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cooking_plans.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    cooked_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    recipe_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipe_title: Mapped[str] = mapped_column(Text, nullable=False)
    meal_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cuisine: Mapped[str | None] = mapped_column(String(64), nullable=True)
    servings: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default=text("2"))
    prep_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cook_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingredients_used: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    substitutions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    recipe_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cooked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    feedback_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    would_make_again: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feedback_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
