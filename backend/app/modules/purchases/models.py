from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default=text("'draft'")
    )
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default=text("'manual'")
    )
    document_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default=text("'unknown'")
    )
    ocr_adapter: Mapped[str | None] = mapped_column(Text, nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purchased_on: Mapped[date] = mapped_column(Date, nullable=False)
    purchased_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default=text("'INR'")
    )
    receipt_category: Mapped[str] = mapped_column(
        String(32), nullable=False, default="grocery", server_default=text("'grocery'")
    )
    payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subtotal_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discount_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    additional_charges_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    cgst_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    sgst_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    igst_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    other_tax_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    tax_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    rounding_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    total_amount_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    raw_ocr_payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    missing_fields_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    attachments_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    items: Mapped[list["PurchaseItem"]] = relationship(
        back_populates="purchase",
        cascade="all, delete-orphan",
        order_by="PurchaseItem.line_index",
    )


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    purchase_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False
    )
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    line_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    raw_text: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    canonical_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("canonical_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(Text, nullable=True)
    variant: Mapped[str | None] = mapped_column(Text, nullable=True)
    pack_size: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(
        Text, nullable=False, default="general", server_default=text("'general'")
    )
    secondary_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    match_confidence: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unmatched", server_default=text("'unmatched'")
    )
    matching_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    eligibility_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit_price_provenance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    normalized_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    base_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    line_total_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    discount_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    tax_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    tax_rate_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    warnings_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    track_inventory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    food_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    budget_treatment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    classification_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    matching_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    resolution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    classification_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    purchase: Mapped[Purchase] = relationship(back_populates="items")


class FoodConsumption(Base):
    __tablename__ = "food_consumptions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    purchase_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("purchases.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    consumed_on: Mapped[date] = mapped_column(Date, nullable=False)
    meal_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="other", server_default=text("'other'")
    )
    dining_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default="restaurant", server_default=text("'restaurant'")
    )
    merchant_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default=text("'INR'")
    )
    items_summary: Mapped[list | None] = mapped_column(JSON, nullable=True)
    people_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
