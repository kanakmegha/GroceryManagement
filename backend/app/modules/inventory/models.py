from datetime import UTC, date, datetime
from decimal import Decimal
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
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CanonicalItem(Base):
    __tablename__ = "canonical_items"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        Text, nullable=False, default="general", server_default=text("'general'")
    )
    secondary_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    food_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default=text("'unknown'")
    )
    is_prepared_food: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    pantry_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    budget_treatment: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pantry_inventory",
        server_default=text("'pantry_inventory'"),
    )
    language: Mapped[str] = mapped_column(
        String(16), nullable=False, default="en", server_default=text("'en'")
    )
    source_dataset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    default_base_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    default_shelf_life_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    low_stock_threshold_qty: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    merged_into_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("canonical_items.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class CanonicalMergeLog(Base):
    __tablename__ = "canonical_merge_log"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    duplicate_canonical_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("canonical_items.id", ondelete="CASCADE"), nullable=False
    )
    survivor_canonical_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("canonical_items.id", ondelete="CASCADE"), nullable=False
    )
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    merged_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    merged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ItemAlias(Base):
    __tablename__ = "item_aliases"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    canonical_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("canonical_items.id", ondelete="CASCADE"), nullable=False
    )
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system", server_default=text("'system'")
    )
    alias_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="synonym", server_default=text("'synonym'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class InventoryLot(Base):
    __tablename__ = "inventory_lots"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    canonical_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("canonical_items.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("purchase_items.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    initial_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    remaining_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    base_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    unit_cost_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    purchased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    lot_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default=text("'active'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class StockLedger(Base):
    __tablename__ = "stock_ledger"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    lot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("inventory_lots.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("canonical_items.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    base_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    balance_after_lot: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_cost_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    reference_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    actor_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class CanonicalIngredient(Base):
    """Brand-agnostic generic culinary ingredient identity."""

    __tablename__ = "canonical_ingredients"
    __table_args__ = (
        CheckConstraint(
            "standard_unit IN ('g', 'ml', 'pcs')",
            name="ck_canonical_ingredients_standard_unit",
        ),
        Index("uq_canonical_ingredients_normalized_name", "normalized_name", unique=True),
        Index(
            "uq_canonical_ingredients_variant_id",
            "variant_id",
            unique=True,
            sqlite_where=text("variant_id IS NOT NULL"),
            postgresql_where=text("variant_id IS NOT NULL"),
        ),
        Index("ix_canonical_ingredients_family_id", "family_id"),
        Index("ix_canonical_ingredients_category", "category", "subcategory"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    family_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    family_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    variant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    variant_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_unspecified_variant: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    attributes_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    standard_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    cooking_variants: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    supported_units: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    mappings: Mapped[list["IngredientMapping"]] = relationship(
        back_populates="canonical_ingredient",
        passive_deletes=True,
    )


class IngredientMapping(Base):
    """Maps a purchased product (CanonicalItem) to a generic CanonicalIngredient.

    household_id=NULL represents a global catalogue mapping; non-null household_id
    represents a household-specific override or confirmed mapping.
    Partial unique indexes guarantee at most one mapping per (scope, canonical_item_id).
    """

    __tablename__ = "ingredient_mappings"
    __table_args__ = (
        CheckConstraint(
            "matching_confidence >= 0 AND matching_confidence <= 1",
            name="ck_ingredient_mappings_confidence",
        ),
        CheckConstraint(
            (
                "mapping_source IN ('catalogue', 'classifier_v2', 'user_confirmed', "
                "'user_correction', 'manual')"
            ),
            name="ck_ingredient_mappings_source",
        ),
        Index(
            "uq_ingredient_mappings_global_item",
            "canonical_item_id",
            unique=True,
            sqlite_where=text("household_id IS NULL"),
            postgresql_where=text("household_id IS NULL"),
        ),
        Index(
            "uq_ingredient_mappings_household_item",
            "household_id",
            "canonical_item_id",
            unique=True,
            sqlite_where=text("household_id IS NOT NULL"),
            postgresql_where=text("household_id IS NOT NULL"),
        ),
        Index("ix_ingredient_mappings_ingredient", "canonical_ingredient_id"),
        Index("ix_ingredient_mappings_household", "household_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    canonical_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("canonical_items.id", ondelete="CASCADE"), nullable=False
    )
    canonical_ingredient_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("canonical_ingredients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    matching_confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("1.000"), server_default=text("1.000")
    )
    mapping_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="catalogue", server_default=text("'catalogue'")
    )
    user_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    canonical_ingredient: Mapped[CanonicalIngredient] = relationship(back_populates="mappings")


class IngredientAlias(Base):
    """Database-backed searchable alias for a CanonicalIngredient."""

    __tablename__ = "ingredient_aliases"
    __table_args__ = (
        CheckConstraint(
            (
                "alias_type IN ('synonym', 'transliteration', 'regional_name', "
                "'spelling_variant', 'ocr_variant', 'user_correction')"
            ),
            name="ck_ingredient_aliases_type",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_ingredient_aliases_confidence",
        ),
        Index(
            "uq_ingredient_aliases_global_norm",
            "normalized_alias",
            unique=True,
            sqlite_where=text("household_id IS NULL"),
            postgresql_where=text("household_id IS NULL"),
        ),
        Index(
            "uq_ingredient_aliases_household_norm",
            "household_id",
            "normalized_alias",
            unique=True,
            sqlite_where=text("household_id IS NOT NULL"),
            postgresql_where=text("household_id IS NOT NULL"),
        ),
        Index("ix_ingredient_aliases_ingredient", "canonical_ingredient_id"),
        Index("ix_ingredient_aliases_household", "household_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    household_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    canonical_ingredient_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("canonical_ingredients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(
        String(16), nullable=False, default="en", server_default=text("'en'")
    )
    alias_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="synonym", server_default=text("'synonym'")
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="catalogue", server_default=text("'catalogue'")
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("1.000"), server_default=text("1.000")
    )
    is_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    original_phrase: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_family_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_variant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
