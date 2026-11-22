"""Grocery-specific measurement profiles, explicit unit normalization, package parsing,
and price-based receipt quantity validation (Phase 5A).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

MeasurementDimension = Literal["MASS", "VOLUME", "COUNT", "OTHER"]


@dataclass(frozen=True)
class MeasurementProfile:
    canonical_unit: str
    dimension: MeasurementDimension
    accepted_units: tuple[str, ...]
    allows_multi_dimension: bool = False
    requires_explicit_net_qty_for_pack: bool = True
    verified_count_to_mass_g: Decimal | None = None
    globally_valid: bool = True


_MASS_PROFILE = MeasurementProfile(
    canonical_unit="g",
    dimension="MASS",
    accepted_units=("g", "kg"),
    allows_multi_dimension=False,
)
_VOL_PROFILE = MeasurementProfile(
    canonical_unit="ml",
    dimension="VOLUME",
    accepted_units=("ml", "l"),
    allows_multi_dimension=False,
)
_COUNT_PROFILE = MeasurementProfile(
    canonical_unit="pcs",
    dimension="COUNT",
    accepted_units=("pcs", "dozen"),
    allows_multi_dimension=False,
)
_PRODUCE_MASS_OR_COUNT_PROFILE = MeasurementProfile(
    canonical_unit="g",
    dimension="MASS",
    accepted_units=("g", "kg", "pcs", "dozen"),
    allows_multi_dimension=True,
)
_PRODUCE_COUNT_OR_MASS_PROFILE = MeasurementProfile(
    canonical_unit="pcs",
    dimension="COUNT",
    accepted_units=("pcs", "dozen", "g", "kg"),
    allows_multi_dimension=True,
)

# Explicit profiles for all 50 seeded CanonicalIngredients
MEASUREMENT_PROFILES_BY_INGREDIENT: dict[str, MeasurementProfile] = {
    # Dairy
    "milk": _VOL_PROFILE,
    "curd": _MASS_PROFILE,
    "paneer": _MASS_PROFILE,
    "butter": _MASS_PROFILE,
    "cheese": _MASS_PROFILE,
    # Grains & flours
    "basmati_rice": _MASS_PROFILE,
    "sona_masoori_rice": _MASS_PROFILE,
    "idli_rice": _MASS_PROFILE,
    "rice_flour": _MASS_PROFILE,
    "whole_wheat_flour": _MASS_PROFILE,
    "semolina": _MASS_PROFILE,
    "rolled_oats": _MASS_PROFILE,
    # Pulses
    "toor_dal": _MASS_PROFILE,
    "moong_dal": _MASS_PROFILE,
    "chana_dal": _MASS_PROFILE,
    "urad_dal": _MASS_PROFILE,
    "chickpeas": _MASS_PROFILE,
    # Vegetables (produce supporting separate mass & count groups)
    "onion": _PRODUCE_MASS_OR_COUNT_PROFILE,
    "tomato": _PRODUCE_MASS_OR_COUNT_PROFILE,
    "potato": _PRODUCE_MASS_OR_COUNT_PROFILE,
    "garlic": _PRODUCE_MASS_OR_COUNT_PROFILE,
    "ginger": _PRODUCE_MASS_OR_COUNT_PROFILE,
    "spinach": _PRODUCE_MASS_OR_COUNT_PROFILE,
    # Fruits
    "banana": _PRODUCE_COUNT_OR_MASS_PROFILE,
    "apple": _PRODUCE_MASS_OR_COUNT_PROFILE,
    "lemon": _PRODUCE_COUNT_OR_MASS_PROFILE,
    "mango": _PRODUCE_MASS_OR_COUNT_PROFILE,
    "pomegranate": _PRODUCE_MASS_OR_COUNT_PROFILE,
    # Oils & fats
    "sunflower_oil": _VOL_PROFILE,
    "mustard_oil": _VOL_PROFILE,
    "coconut_oil": _VOL_PROFILE,
    "rice_bran_oil": _VOL_PROFILE,
    "ghee": _VOL_PROFILE,
    # Spices
    "turmeric_powder": _MASS_PROFILE,
    "cumin_seeds": _MASS_PROFILE,
    "red_chilli_powder": _MASS_PROFILE,
    "coriander_powder": _MASS_PROFILE,
    "coriander_seeds": _MASS_PROFILE,
    "mustard_seeds": _MASS_PROFILE,
    "garam_masala": _MASS_PROFILE,
    "ginger_powder": _MASS_PROFILE,
    # Nuts & seeds
    "almonds": _MASS_PROFILE,
    "cashews": _MASS_PROFILE,
    "peanuts": _MASS_PROFILE,
    "sesame_seeds": _MASS_PROFILE,
    "flax_seeds": _MASS_PROFILE,
    "walnuts": _MASS_PROFILE,
    # Sweeteners & packaged
    "sugar": _MASS_PROFILE,
    "tomato_puree": _MASS_PROFILE,
    "coconut_milk": _VOL_PROFILE,
    # Common count staples
    "eggs": _COUNT_PROFILE,
}


def get_measurement_profile(
    normalized_name: str,
    category: str | None = None,
    standard_unit: str | None = None,
) -> MeasurementProfile:
    """Return the measurement profile for an ingredient, falling back to standard_unit/category."""
    key = (normalized_name or "").strip().lower()
    if key in MEASUREMENT_PROFILES_BY_INGREDIENT:
        return MEASUREMENT_PROFILES_BY_INGREDIENT[key]
    unit = (standard_unit or "").strip().lower()
    if unit == "ml":
        return _VOL_PROFILE
    if unit == "pcs":
        if (category or "").strip().lower() in {"fruit", "vegetable", "produce"}:
            return _PRODUCE_COUNT_OR_MASS_PROFILE
        return _COUNT_PROFILE
    if (category or "").strip().lower() in {"fruit", "vegetable", "produce"}:
        return _PRODUCE_MASS_OR_COUNT_PROFILE
    return _MASS_PROFILE


_UNIT_FACTORS: dict[str, tuple[MeasurementDimension, str, Decimal]] = {
    "g": ("MASS", "g", Decimal("1")),
    "gm": ("MASS", "g", Decimal("1")),
    "gms": ("MASS", "g", Decimal("1")),
    "gram": ("MASS", "g", Decimal("1")),
    "grams": ("MASS", "g", Decimal("1")),
    "kg": ("MASS", "g", Decimal("1000")),
    "kgs": ("MASS", "g", Decimal("1000")),
    "kilogram": ("MASS", "g", Decimal("1000")),
    "kilograms": ("MASS", "g", Decimal("1000")),
    "ml": ("VOLUME", "ml", Decimal("1")),
    "millilitre": ("VOLUME", "ml", Decimal("1")),
    "milliliter": ("VOLUME", "ml", Decimal("1")),
    "l": ("VOLUME", "ml", Decimal("1000")),
    "lt": ("VOLUME", "ml", Decimal("1000")),
    "ltr": ("VOLUME", "ml", Decimal("1000")),
    "liter": ("VOLUME", "ml", Decimal("1000")),
    "litre": ("VOLUME", "ml", Decimal("1000")),
    "liters": ("VOLUME", "ml", Decimal("1000")),
    "litres": ("VOLUME", "ml", Decimal("1000")),
    "pc": ("COUNT", "pcs", Decimal("1")),
    "pcs": ("COUNT", "pcs", Decimal("1")),
    "piece": ("COUNT", "pcs", Decimal("1")),
    "pieces": ("COUNT", "pcs", Decimal("1")),
    "unit": ("COUNT", "pcs", Decimal("1")),
    "units": ("COUNT", "pcs", Decimal("1")),
    "doz": ("COUNT", "pcs", Decimal("12")),
    "dozen": ("COUNT", "pcs", Decimal("12")),
}

_PACK_WORDS = {
    "pack",
    "packs",
    "pkt",
    "pkts",
    "packet",
    "packets",
    "box",
    "boxes",
    "pouch",
    "pouches",
    "bottle",
    "bottles",
    "carton",
    "cartons",
}

_PACK_COUNT_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:x|\*)?\s*(?:pack|packs|pkt|pkts|packet|packets|box|boxes|pouch|pouches|bottle|bottles)\b",
    re.IGNORECASE,
)
_NET_QTY_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(kg|kgs|g|gm|gms|grams|gram|l|lt|ltr|liter|litre|liters|litres|ml|dozen|doz|pcs|pc)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GroceryQuantityResult:
    normalized_qty: Decimal | None
    normalized_unit: str | None
    dimension: MeasurementDimension
    package_count: Decimal | None
    net_qty_per_package: Decimal | None
    net_unit_per_package: str | None
    conversion_factor: Decimal | None
    provenance: str
    needs_review: bool
    warning: str | None = None


def normalize_grocery_quantity(
    qty: Decimal | float | int,
    unit: str | None,
    *,
    raw_description: str | None = None,
) -> GroceryQuantityResult:
    """Explicitly normalize grocery quantities, separating package count from net content."""
    qty_dec = Decimal(str(qty))
    u = (unit or "").strip().lower()

    # Check if unit is a container/package word (packet, box, pouch, bottle)
    if u in _PACK_WORDS or (raw_description and _PACK_COUNT_RE.search(raw_description)):
        pack_match = _PACK_COUNT_RE.search(raw_description or "")
        pack_count = Decimal(pack_match.group(1)) if pack_match else qty_dec
        net_match = _NET_QTY_RE.search(raw_description or "")
        if net_match is None:
            return GroceryQuantityResult(
                normalized_qty=None,
                normalized_unit=None,
                dimension="OTHER",
                package_count=pack_count,
                net_qty_per_package=None,
                net_unit_per_package=None,
                conversion_factor=None,
                provenance="unknown_package_net_qty",
                needs_review=True,
                warning=(
                    f"Package quantity '{pack_count} {u or 'packet'}' has no explicit net "
                    "weight/volume."
                ),
            )
        net_val = Decimal(net_match.group(1))
        net_unit = net_match.group(2).lower()
        dim, base_u, factor = _UNIT_FACTORS[net_unit]
        total_norm = (pack_count * net_val * factor).quantize(Decimal("0.001"))
        return GroceryQuantityResult(
            normalized_qty=total_norm,
            normalized_unit=base_u,
            dimension=dim,
            package_count=pack_count,
            net_qty_per_package=net_val,
            net_unit_per_package=net_unit,
            conversion_factor=factor,
            provenance="package_count_x_net_qty",
            needs_review=False,
        )

    if u not in _UNIT_FACTORS:
        return GroceryQuantityResult(
            normalized_qty=None,
            normalized_unit=None,
            dimension="OTHER",
            package_count=None,
            net_qty_per_package=None,
            net_unit_per_package=None,
            conversion_factor=None,
            provenance="unknown_unit",
            needs_review=True,
            warning=f"Unknown or unconvertible unit '{unit}'.",
        )

    dim, base_u, factor = _UNIT_FACTORS[u]
    norm_val = (qty_dec * factor).quantize(Decimal("0.001"))
    return GroceryQuantityResult(
        normalized_qty=norm_val,
        normalized_unit=base_u,
        dimension=dim,
        package_count=None,
        net_qty_per_package=qty_dec,
        net_unit_per_package=u,
        conversion_factor=factor,
        provenance="explicit_unit_conversion",
        needs_review=False,
    )


@dataclass(frozen=True)
class PriceQuantityValidationResult:
    is_valid: bool
    needs_review: bool
    code: str | None
    message: str | None
    ratio: Decimal | None


def validate_receipt_quantity_against_price(
    *,
    normalized_qty: Decimal,
    base_unit: str,
    raw_unit: str | None,
    unit_price_paise: int,
    line_total_paise: int,
) -> PriceQuantityValidationResult:
    """Validate derived normalized quantity against unit price and line total.

    Catches 1,000x unit-conversion errors (e.g. CHAWAL 5 KG stored as 5,000,000 g
    at Rs 40/kg = Rs 200) without silently rewriting quantities.
    """
    if unit_price_paise <= 0 or line_total_paise <= 0 or normalized_qty <= Decimal("0"):
        return PriceQuantityValidationResult(
            is_valid=True, needs_review=False, code=None, message=None, ratio=None
        )

    raw_u = (raw_unit or "").strip().lower()
    # Determine quantity in the unit that unit_price is likely quoted in
    if base_unit in ("g", "ml"):
        # If raw_unit was kg/l, or unit_price is per kg/L (>= 100 paise and qty >= 500 g/ml)
        if raw_u in ("kg", "kgs", "l", "ltr", "litre", "liter") or normalized_qty >= Decimal("500"):
            qty_for_price = normalized_qty / Decimal("1000")
        else:
            qty_for_price = normalized_qty
    else:
        qty_for_price = normalized_qty

    expected_total = qty_for_price * Decimal(unit_price_paise)
    actual_total = Decimal(line_total_paise)
    if actual_total <= Decimal("0") or expected_total <= Decimal("0"):
        return PriceQuantityValidationResult(
            is_valid=True, needs_review=False, code=None, message=None, ratio=None
        )

    ratio = (expected_total / actual_total).quantize(Decimal("0.01"))
    # Flag severe mismatches (>= 10x or <= 0.1x), e.g., 1000x unit-conversion bugs
    if ratio >= Decimal("10.00") or ratio <= Decimal("0.10"):
        return PriceQuantityValidationResult(
            is_valid=False,
            needs_review=True,
            code="QUANTITY_PRICE_MISMATCH",
            message=(
                f"Derived quantity ({normalized_qty} {base_unit}) implies total "
                f"{expected_total:.0f} paise at unit_price={unit_price_paise} paise, "
                f"which disagrees with line_total={line_total_paise} paise (ratio={ratio}x)."
            ),
            ratio=ratio,
        )

    return PriceQuantityValidationResult(
        is_valid=True, needs_review=False, code=None, message=None, ratio=ratio
    )
