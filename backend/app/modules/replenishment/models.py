import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ShoppingListItem(Base):
    __tablename__ = "shopping_list_items"
    __table_args__ = (
        Index("ix_shopping_list_household_status", "household_id", "status"),
        Index("ix_shopping_list_household_canonical", "household_id", "canonical_item_id"),
        Index(
            "uq_shopping_list_pending_canonical",
            "household_id",
            "canonical_item_id",
            unique=True,
            sqlite_where=text("status = 'pending' AND canonical_item_id IS NOT NULL"),
            postgresql_where=text("status = 'pending' AND canonical_item_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("canonical_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    primary_source: Mapped[str] = mapped_column(String(32), nullable=False)
    sources_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="pending",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
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


class ReplenishmentFeedback(Base):
    __tablename__ = "replenishment_feedback"
    __table_args__ = (
        Index(
            "ix_replenishment_feedback_lookup",
            "household_id",
            "canonical_item_id",
            "is_active",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("canonical_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    remind_after: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("1"),
    )
    reason_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
