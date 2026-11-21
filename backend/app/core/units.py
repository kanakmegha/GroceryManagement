import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

BaseUnit = Literal["g", "ml", "pcs"]

_COUNT_UNITS = {
    "pcs", "pc", "piece", "pieces", "nos", "no", "unit", "units",
    "pack", "packs", "pkt", "pkts", "packet", "packets",
    "bunch", "bunches", "ea", "each", "bottle", "bottles",
    "can", "cans", "jar", "jars", "bag", "bags",
    "box", "boxes", "tin", "tins", "carton", "cartons", "pouch", "pouches",
    "dozen", "doz",
}

_PACK_METRIC_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>kg|kgs|g|gm|gms|gram|grams|mg|ml|l|ltr|ltrs|liter|liters|litre|litres)\b",
    re.IGNORECASE,
)

_UNIT_CONVERSIONS: dict[str, tuple[Decimal, BaseUnit]] = {
    # Mass -> g
    "g": (Decimal("1"), "g"),
    "gm": (Decimal("1"), "g"),
    "gms": (Decimal("1"), "g"),
    "gram": (Decimal("1"), "g"),
    "grams": (Decimal("1"), "g"),
    "kg": (Decimal("1000"), "g"),
    "kgs": (Decimal("1000"), "g"),
    "kilogram": (Decimal("1000"), "g"),
    "kilograms": (Decimal("1000"), "g"),
    "mg": (Decimal("0.001"), "g"),
    "milligram": (Decimal("0.001"), "g"),
    "milligrams": (Decimal("0.001"), "g"),
    # Volume -> ml
    "ml": (Decimal("1"), "ml"),
    "milliliter": (Decimal("1"), "ml"),
    "milliliters": (Decimal("1"), "ml"),
    "millilitre": (Decimal("1"), "ml"),
    "millilitres": (Decimal("1"), "ml"),
    "l": (Decimal("1000"), "ml"),
    "ltr": (Decimal("1000"), "ml"),
    "ltrs": (Decimal("1000"), "ml"),
    "liter": (Decimal("1000"), "ml"),
    "liters": (Decimal("1000"), "ml"),
    "litre": (Decimal("1000"), "ml"),
    "litres": (Decimal("1000"), "ml"),
    # Count -> pcs
    "pcs": (Decimal("1"), "pcs"),
    "pc": (Decimal("1"), "pcs"),
    "piece": (Decimal("1"), "pcs"),
    "pieces": (Decimal("1"), "pcs"),
    "unit": (Decimal("1"), "pcs"),
    "units": (Decimal("1"), "pcs"),
    "pack": (Decimal("1"), "pcs"),
    "packs": (Decimal("1"), "pcs"),
    "pkt": (Decimal("1"), "pcs"),
    "pkts": (Decimal("1"), "pcs"),
    "packet": (Decimal("1"), "pcs"),
    "packets": (Decimal("1"), "pcs"),
    "bunch": (Decimal("1"), "pcs"),
    "bunches": (Decimal("1"), "pcs"),
    "ea": (Decimal("1"), "pcs"),
    "each": (Decimal("1"), "pcs"),
    "bottle": (Decimal("1"), "pcs"),
    "bottles": (Decimal("1"), "pcs"),
    "can": (Decimal("1"), "pcs"),
    "cans": (Decimal("1"), "pcs"),
    "jar": (Decimal("1"), "pcs"),
    "jars": (Decimal("1"), "pcs"),
    "bag": (Decimal("1"), "pcs"),
    "bags": (Decimal("1"), "pcs"),
    "box": (Decimal("1"), "pcs"),
    "boxes": (Decimal("1"), "pcs"),
    "tin": (Decimal("1"), "pcs"),
    "tins": (Decimal("1"), "pcs"),
    "dozen": (Decimal("12"), "pcs"),
    "doz": (Decimal("12"), "pcs"),
}


def normalize_to_base_unit(
    qty: Decimal | int | float | str,
    unit: str,
) -> tuple[Decimal, BaseUnit]:
    """Normalize a quantity and unit string to (Decimal, 'g'|'ml'|'pcs')."""
    try:
        dec_qty = qty if isinstance(qty, Decimal) else Decimal(str(qty))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid quantity: {qty!r}") from exc

    if not dec_qty.is_finite() or dec_qty < Decimal("0"):
        raise ValueError(f"Quantity must be a finite non-negative number: {qty!r}")

    normalized_unit = unit.strip().lower()
    conversion = _UNIT_CONVERSIONS.get(normalized_unit)
    if conversion is None:
        raise ValueError(f"Unsupported unit: {unit!r}")

    multiplier, base_unit = conversion
    normalized_qty = (dec_qty * multiplier).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return normalized_qty, base_unit


def resolve_purchase_measurement(
    qty: Decimal | int | float | str,
    unit: str,
    pack_size: str | None = None,
) -> tuple[Decimal, BaseUnit]:
    """Normalize purchase quantity & unit, multiplying count/pack units by explicit metric pack_size when available.

    Never invents a weight/volume conversion when pack_size is missing or non-metric.
    """
    norm_unit = (unit or "pcs").strip().lower()
    base_qty, base_unit = normalize_to_base_unit(qty, norm_unit)
    if norm_unit in _COUNT_UNITS and pack_size:
        m = _PACK_METRIC_RE.search(pack_size)
        if m:
            pack_qty, pack_unit = normalize_to_base_unit(m.group("qty"), m.group("unit"))
            if pack_unit in ("g", "ml") and pack_qty > Decimal("0"):
                total = (base_qty * pack_qty).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                return total, pack_unit
    return base_qty, base_unit
