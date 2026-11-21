from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class HouseholdRecipePreference(Base):
    """Explicit household food, cuisine, dietary, and location/timezone preferences."""

    __tablename__ = "household_recipe_preferences"

    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("households.id", ondelete="CASCADE"),
        primary_key=True,
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    location_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    diet_type: Mapped[str] = mapped_column(String(32), nullable=False, default="any")
    dietary_restrictions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    preferred_cuisines: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    preferred_meal_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_prep_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class RecipeFeedback(Base):
    """Lightweight per-household feedback for recipe recommendations (like, dislike, cooked, hide)."""

    __tablename__ = "recipe_feedback"
    __table_args__ = (
        Index("ix_recipe_feedback_hh_recipe", "household_id", "recipe_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipe_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recipe_title: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
