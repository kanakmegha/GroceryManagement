import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class HouseholdBudget(Base):
    __tablename__ = "household_budgets"
    __table_args__ = (
        UniqueConstraint(
            "household_id",
            "period_type",
            "effective_from",
            name="uq_household_budgets_scope_effective",
        ),
        Index(
            "ix_household_budgets_lookup",
            "household_id",
            "period_type",
            "effective_from",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    overall_limit_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    grocery_limit_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outside_food_limit_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
