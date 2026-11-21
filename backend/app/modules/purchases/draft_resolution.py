"""Real-time ingredient standardisation and separate quantity/financial checks for purchase drafts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.ingredient_catalogue import get_ingredient_catalogue
from app.modules.catalog.ingredient_resolver import IngredientResolver
from app.modules.catalog.measurement_service import validate_receipt_quantity_against_price
from app.modules.inventory.models import CanonicalIngredient

_PACKAGE_UNITS = {"pack", "packet", "bottle", "jar", "box", "can", "loaf", "bunch", "head", "tin", "pouch"}


@dataclass(frozen=True)
class DraftItemResolution:
    standardized_name: str | None
    ingredient_family_id: str | None
    ingredient_family_name: str | None
    ingredient_variant_id: str | None
    canonical_ingredient_id: UUID | None
    ingredient_category: str | None
    is_unspecified_variant: bool
    ingredient_confidence_tier: str  # HIGH | MEDIUM | LOW | UNMATCHED | NON_PANTRY
    ingredient_confidence_score: int | None
    ingredient_match_source: str | None
    ingredient_review_reason: str | None
    user_approved_ingredient: bool
    ingredient_suggestions: list[dict[str, Any]]
    quantity_confidence_tier: str  # HIGH | MEDIUM | LOW
    quantity_review_reason: str | None
    financial_mismatch: bool
    financial_review_reason: str | None
    supported_units: list[str]
    raw_confidence_decimal: Decimal

    def to_metadata_dict(self) -> dict[str, Any]:
        return {
            "standardized_name": self.standardized_name,
            "ingredient_family_id": self.ingredient_family_id,
            "ingredient_family_name": self.ingredient_family_name,
            "ingredient_variant_id": self.ingredient_variant_id,
            "canonical_ingredient_id": (
                str(self.canonical_ingredient_id) if self.canonical_ingredient_id else None
            ),
            "ingredient_category": self.ingredient_category,
            "is_unspecified_variant": self.is_unspecified_variant,
            "ingredient_confidence_tier": self.ingredient_confidence_tier,
            "ingredient_confidence_score": self.ingredient_confidence_score,
            "ingredient_match_source": self.ingredient_match_source,
            "ingredient_review_reason": self.ingredient_review_reason,
            "user_approved_ingredient": self.user_approved_ingredient,
            "ingredient_suggestions": self.ingredient_suggestions,
            "quantity_confidence_tier": self.quantity_confidence_tier,
            "quantity_review_reason": self.quantity_review_reason,
            "financial_mismatch": self.financial_mismatch,
            "financial_review_reason": self.financial_review_reason,
            "supported_units": self.supported_units,
        }


async def _lookup_canonical_ingredient(
    db: AsyncSession,
    *,
    canonical_ingredient_id: UUID | None = None,
    variant_id: str | None = None,
) -> CanonicalIngredient | None:
    if canonical_ingredient_id is not None:
        obj = await db.get(CanonicalIngredient, canonical_ingredient_id)
        if obj is not None:
            return obj
    if variant_id:
        stmt = select(CanonicalIngredient).where(CanonicalIngredient.variant_id == variant_id)
        return (await db.execute(stmt)).scalar_one_or_none()
    return None


def _evaluate_financial_consistency(
    *,
    normalized_qty: Decimal,
    raw_qty: Decimal | None,
    unit_price_paise: int,
    line_total_paise: int,
    discount_paise: int,
    tax_paise: int,
) -> tuple[bool, str | None]:
    if unit_price_paise <= 0 or line_total_paise <= 0:
        return False, None

    effective_total = max(0, line_total_paise + discount_paise - tax_paise)
    tolerance = max(200, int(round(max(line_total_paise, effective_total) * 0.05)))

    # Check both normalized_qty * unit_price and raw_qty * unit_price,
    # as well as single-pack unit_price == effective_total when qty is weight/volume (e.g. 1000g @ Rs 50/pack).
    candidates = [
        int(round(float(normalized_qty) * unit_price_paise)),
    ]
    if raw_qty is not None and raw_qty > Decimal("0"):
        candidates.append(int(round(float(raw_qty) * unit_price_paise)))
    if unit_price_paise == line_total_paise or abs(unit_price_paise - effective_total) <= tolerance:
        candidates.append(unit_price_paise)

    if any(abs(c - effective_total) <= tolerance or abs(c - line_total_paise) <= tolerance for c in candidates):
        return False, None

    expected_qty = float(raw_qty) if raw_qty is not None and raw_qty > 0 else float(normalized_qty)
    return (
        True,
        f"Financial check: {expected_qty:g} × ₹{unit_price_paise / 100:.2f} does not match line total ₹{line_total_paise / 100:.2f}.",
    )


def _evaluate_quantity_and_unit(
    *,
    normalized_qty: Decimal,
    base_unit: str,
    raw_unit: str | None,
    unit_price_paise: int,
    line_total_paise: int,
    measurement_policy: dict[str, Any] | None,
    standardized_name: str | None,
) -> tuple[str, str | None, list[str]]:
    qty_price_check = validate_receipt_quantity_against_price(
        normalized_qty=normalized_qty,
        base_unit=base_unit,
        raw_unit=raw_unit or base_unit,
        unit_price_paise=max(0, int(unit_price_paise)),
        line_total_paise=max(0, int(line_total_paise)),
    )
    supported_units = [str(u) for u in ((measurement_policy or {}).get("supported_units") or [])]
    supported_lower = [u.lower() for u in supported_units]
    default_dim = (measurement_policy or {}).get("default_dimension")
    pkg_requires_net = bool(
        (measurement_policy or {}).get("package_conversion_requires_explicit_net_content", True)
    )
    raw_u_lower = (raw_unit or base_unit).strip().lower()
    base_u_lower = base_unit.strip().lower()

    if not qty_price_check.is_valid:
        return "LOW", qty_price_check.message, supported_units

    if (
        raw_u_lower in _PACKAGE_UNITS
        and base_u_lower == "pcs"
        and default_dim in {"mass", "volume"}
        and pkg_requires_net
    ):
        hint = f" ({', '.join(supported_units[:3])})" if supported_units else ""
        return (
            "MEDIUM",
            f"Package unit '{raw_unit}' without explicit net weight/volume; verify quantity or unit{hint}.",
            supported_units,
        )

    if supported_lower:
        compatible = base_u_lower in supported_lower or (
            (base_u_lower == "g" and "kg" in supported_lower)
            or (base_u_lower == "ml" and "l" in supported_lower)
            or (base_u_lower == "pcs" and "dozen" in supported_lower)
        )
        if not compatible:
            label = standardized_name or "this ingredient"
            return (
                "MEDIUM",
                f"Unit '{base_unit}' differs from standard units for {label} ({', '.join(supported_units)}).",
                supported_units,
            )

    return "HIGH", None, supported_units


async def evaluate_draft_item_resolution(
    db: AsyncSession,
    *,
    household_id: UUID,
    raw_text: str,
    display_name: str,
    canonical_item_id: UUID | None,
    normalized_qty: Decimal,
    base_unit: str,
    raw_unit: str | None = None,
    raw_qty: Decimal | None = None,
    unit_price_paise: int = 0,
    line_total_paise: int = 0,
    discount_paise: int = 0,
    tax_paise: int = 0,
    track_inventory: bool = True,
    classification_status: str = "pantry_eligible",
    explicit_canonical_ingredient_id: UUID | None = None,
    explicit_variant_id: str | None = None,
    user_approved_ingredient: bool = False,
) -> DraftItemResolution:
    catalogue = get_ingredient_catalogue()
    financial_mismatch, financial_reason = _evaluate_financial_consistency(
        normalized_qty=normalized_qty,
        raw_qty=raw_qty,
        unit_price_paise=unit_price_paise,
        line_total_paise=line_total_paise,
        discount_paise=discount_paise,
        tax_paise=tax_paise,
    )

    if not track_inventory or classification_status in {"non_food", "prepared_meal"}:
        qty_tier, qty_reason, supported = _evaluate_quantity_and_unit(
            normalized_qty=normalized_qty,
            base_unit=base_unit,
            raw_unit=raw_unit,
            unit_price_paise=unit_price_paise,
            line_total_paise=line_total_paise,
            measurement_policy=None,
            standardized_name=None,
        )
        return DraftItemResolution(
            standardized_name=None,
            ingredient_family_id=None,
            ingredient_family_name=None,
            ingredient_variant_id=None,
            canonical_ingredient_id=None,
            ingredient_category=None,
            is_unspecified_variant=False,
            ingredient_confidence_tier="NON_PANTRY",
            ingredient_confidence_score=None,
            ingredient_match_source="non_pantry",
            ingredient_review_reason=None,
            user_approved_ingredient=False,
            ingredient_suggestions=[],
            quantity_confidence_tier=qty_tier,
            quantity_review_reason=qty_reason,
            financial_mismatch=financial_mismatch,
            financial_review_reason=financial_reason,
            supported_units=supported,
            raw_confidence_decimal=Decimal("0.000"),
        )

    # Explicit selection from Review Purchase Draft UI
    if explicit_canonical_ingredient_id is not None or explicit_variant_id:
        ing = await _lookup_canonical_ingredient(
            db,
            canonical_ingredient_id=explicit_canonical_ingredient_id,
            variant_id=explicit_variant_id,
        )
        cat_var = (
            catalogue.get_variant(ing.variant_id)
            if ing is not None and ing.variant_id
            else (catalogue.get_variant(explicit_variant_id) if explicit_variant_id else None)
        )
        cat_fam = catalogue.get_family(cat_var.family_id) if cat_var else None
        measurement_policy = cat_fam.measurement if cat_fam else None
        std_name = ing.name if ing is not None else (cat_var.canonical_name if cat_var else display_name)
        qty_tier, qty_reason, supported = _evaluate_quantity_and_unit(
            normalized_qty=normalized_qty,
            base_unit=base_unit,
            raw_unit=raw_unit,
            unit_price_paise=unit_price_paise,
            line_total_paise=line_total_paise,
            measurement_policy=measurement_policy,
            standardized_name=std_name,
        )
        return DraftItemResolution(
            standardized_name=std_name,
            ingredient_family_id=(ing.family_id if ing else None) or (cat_fam.id if cat_fam else None),
            ingredient_family_name=(ing.family_name if ing else None) or (cat_fam.canonical_name if cat_fam else None),
            ingredient_variant_id=(ing.variant_id if ing else None) or (cat_var.id if cat_var else None),
            canonical_ingredient_id=ing.id if ing is not None else None,
            ingredient_category=(ing.category if ing else None) or (cat_fam.category if cat_fam else None),
            is_unspecified_variant=bool(
                ing.is_unspecified_variant if ing is not None else (cat_var.is_unspecified if cat_var else False)
            ),
            ingredient_confidence_tier="HIGH",
            ingredient_confidence_score=100,
            ingredient_match_source="user_corrected",
            ingredient_review_reason=None,
            user_approved_ingredient=True,
            ingredient_suggestions=[],
            quantity_confidence_tier=qty_tier,
            quantity_review_reason=qty_reason,
            financial_mismatch=financial_mismatch,
            financial_review_reason=financial_reason,
            supported_units=supported,
            raw_confidence_decimal=Decimal("1.000"),
        )

    resolver = IngredientResolver()
    receipt_text = (display_name or raw_text or "").strip()
    res = await resolver.resolve_for_household(
        db,
        household_id=household_id,
        receipt_text=receipt_text,
        canonical_item_id=canonical_item_id,
    )
    if res.variant_id is None and raw_text and raw_text.strip() and raw_text.strip() != receipt_text:
        raw_res = await resolver.resolve_for_household(
            db,
            household_id=household_id,
            receipt_text=raw_text.strip(),
            canonical_item_id=canonical_item_id,
        )
        if raw_res.variant_id is not None and raw_res.confidence > res.confidence:
            res = raw_res

    ing_obj = await _lookup_canonical_ingredient(
        db,
        canonical_ingredient_id=res.canonical_ingredient_id,
        variant_id=res.variant_id,
    )
    canonical_ing_id = ing_obj.id if ing_obj is not None else res.canonical_ingredient_id

    score = int(round(float(res.confidence) * 100))
    if res.variant_id is None or res.match_source == "unresolved_review" or score <= 0:
        tier = "UNMATCHED"
        review_reason = res.reason or "No reliable catalogue match found; select or enter a pantry ingredient."
    elif user_approved_ingredient:
        tier = "HIGH"
        score = max(score, 95)
        review_reason = None
    elif score >= 90 and not res.requires_review:
        tier = "HIGH"
        review_reason = None
    elif score >= 70:
        tier = "MEDIUM"
        review_reason = res.reason or "Partial or ambiguous ingredient match; please verify."
    else:
        tier = "LOW"
        review_reason = res.reason or "Low-confidence fuzzy match; explicit approval or correction required."

    suggestions_out: list[dict[str, Any]] = []
    for sug in (res.suggestions or ())[:5]:
        sug_ing = await _lookup_canonical_ingredient(db, variant_id=sug.variant_id)
        suggestions_out.append(
            {
                "variant_id": sug.variant_id,
                "variant_name": sug.variant_name,
                "family_id": sug.family_id,
                "family_name": sug.family_name,
                "canonical_ingredient_id": str(sug_ing.id) if sug_ing else None,
                "score": int(round(float(sug.score) * 100)),
            }
        )

    qty_tier, qty_reason, supported = _evaluate_quantity_and_unit(
        normalized_qty=normalized_qty,
        base_unit=base_unit,
        raw_unit=raw_unit,
        unit_price_paise=unit_price_paise,
        line_total_paise=line_total_paise,
        measurement_policy=res.measurement,
        standardized_name=res.variant_name,
    )

    return DraftItemResolution(
        standardized_name=res.variant_name,
        ingredient_family_id=res.family_id,
        ingredient_family_name=res.family_name,
        ingredient_variant_id=res.variant_id,
        canonical_ingredient_id=canonical_ing_id,
        ingredient_category=res.category,
        is_unspecified_variant=res.is_unspecified_variant,
        ingredient_confidence_tier=tier,
        ingredient_confidence_score=score,
        ingredient_match_source="user_approved" if user_approved_ingredient else res.match_source,
        ingredient_review_reason=review_reason,
        user_approved_ingredient=user_approved_ingredient,
        ingredient_suggestions=suggestions_out,
        quantity_confidence_tier=qty_tier,
        quantity_review_reason=qty_reason,
        financial_mismatch=financial_mismatch,
        financial_review_reason=financial_reason,
        supported_units=supported,
        raw_confidence_decimal=Decimal("1.000") if user_approved_ingredient else res.confidence,
    )
