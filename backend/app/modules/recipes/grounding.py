"""Backend-controlled inventory grounding, unit validation, and recipe eligibility."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.modules.catalog.classifier import FoodNameNormalizer

RecommendationType = Literal["COOK_NOW", "COOK_WITH_SUBSTITUTIONS", "REQUIRES_SHOPPING"]

_BLOCKED_INGREDIENT_PHRASES: frozenset[str] = frozenset(
    {
        "expiring",
        "expiring items",
        "expiring soon",
        "available household inventory",
        "household inventory",
        "inventory",
        "pantry",
        "pantry items",
        "available ingredients",
        "missing ingredients",
        "recent recipes",
        "conversation history",
        "household preferences",
        "tool results",
        "context",
        "none",
        "n/a",
        "na",
        "something",
        "anything",
        "vegetables nearing expiry",
        "expiring vegetables",
    }
)

_COUNT_ONLY_ITEMS: frozenset[str] = frozenset(
    {
        "egg",
        "eggs",
        "onion",
        "onions",
        "tomato",
        "tomatoes",
        "banana",
        "bananas",
        "apple",
        "apples",
        "cucumber",
        "papaya",
        "coconut",
        "tender coconut",
        "carrot",
        "carrots",
        "bell pepper",
        "bell peppers",
        "lemon",
        "lemons",
        "bread",
        "slice",
        "slices",
        "clove",
        "cloves",
    }
)

_WEIGHT_OR_VOLUME_ITEMS: frozenset[str] = frozenset(
    {
        "spinach",
        "palak",
        "lettuce",
        "chickpeas",
        "chana",
        "toor dal",
        "dal",
        "lentils",
        "rice",
        "flour",
        "atta",
        "oil",
        "olive oil",
        "soyabean oil",
        "mustard oil",
        "coconut oil",
        "ghee",
        "butter",
        "paneer",
        "tofu",
        "milk",
        "curd",
        "yogurt",
        "sugar",
        "salt",
        "tamarind",
        "mustard",
        "mustard seeds",
        "turmeric",
        "chilli powder",
        "chili powder",
        "hing",
        "asafoetida",
        "cumin",
        "cumin seeds",
        "rava",
        "semolina",
        "grapes",
    }
)

_INGREDIENT_SYNONYMS: dict[str, set[str]] = {
    "oil": {"soyabean oil", "soybean oil", "refined soyabean oil", "olive oil", "cooking oil", "vegetable oil", "sunflower oil"},
    "olive oil": {"soyabean oil", "cooking oil", "vegetable oil", "sunflower oil"},
    "mustard seeds": {"mustard", "rai", "ral"},
    "mustard": {"mustard seeds", "rai", "ral"},
    "chilli powder": {"chili powder", "red chili powder", "red chilli powder", "lal mirch"},
    "red chili powder": {"chilli powder", "chili powder"},
    "asafoetida": {"hing"},
    "hing": {"asafoetida"},
    "spinach": {"palak"},
    "toor dal": {"arhar dal", "pigeon pea"},
    "chickpeas": {"kabuli chana", "chole", "garbanzo"},
}

_UNIT_ALIASES: dict[str, str] = {
    "g": "g",
    "gm": "g",
    "gms": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "kgs": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "ml": "ml",
    "l": "l",
    "ltr": "l",
    "litre": "l",
    "litres": "l",
    "liter": "l",
    "liters": "l",
    "pc": "pcs",
    "pcs": "pcs",
    "piece": "pcs",
    "pieces": "pcs",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "cup": "cup",
    "cups": "cup",
    "clove": "cloves",
    "cloves": "cloves",
    "leaf": "leaves",
    "leaves": "leaves",
    "slice": "slices",
    "slices": "slices",
    "pinch": "pinch",
    "medium": "pcs",
    "small": "pcs",
    "large": "pcs",
}

_QTY_UNIT_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?(?:\s+([a-zA-Z]+))?\s*$"
)


def is_blocked_ingredient_name(name: str) -> bool:
    """Return True if the string is internal prompt/context metadata rather than a real ingredient."""
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", (name or "").strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or len(cleaned) < 2:
        return True
    if cleaned in _BLOCKED_INGREDIENT_PHRASES:
        return True
    if any(phrase in cleaned for phrase in ("household inventory", "available inventory", "expiring item", "nearing expiry")):
        return True
    return False


def parse_quantity_and_unit(
    raw_qty: Any,
    raw_unit: Any = None,
    *,
    ingredient_name: str = "",
) -> tuple[Decimal, str]:
    """Parse numeric quantity and explicit unit without blindly collapsing to 'pcs'."""
    qty_str = str(raw_qty or "").strip()
    unit_str = str(raw_unit or "").strip().lower()

    qty_val = Decimal("1")
    parsed_unit = _UNIT_ALIASES.get(unit_str, unit_str) if unit_str else ""

    m = _QTY_UNIT_RE.match(qty_str)
    if m:
        try:
            qty_val = Decimal(m.group(1))
        except InvalidOperation:
            qty_val = Decimal("1")
        if not parsed_unit:
            tok1 = (m.group(2) or "").lower()
            tok2 = (m.group(3) or "").lower()
            if tok1 in _UNIT_ALIASES:
                parsed_unit = _UNIT_ALIASES[tok1]
            elif tok2 in _UNIT_ALIASES:
                parsed_unit = _UNIT_ALIASES[tok2]
            elif tok1:
                parsed_unit = tok1
    else:
        num_m = re.search(r"(\d+(?:\.\d+)?)", qty_str)
        if num_m:
            try:
                qty_val = Decimal(num_m.group(1))
            except InvalidOperation:
                qty_val = Decimal("1")
        for tok in re.findall(r"[a-zA-Z]+", qty_str.lower()):
            if tok in _UNIT_ALIASES and not parsed_unit:
                parsed_unit = _UNIT_ALIASES[tok]
                break

    if qty_val <= Decimal("0"):
        qty_val = Decimal("1")

    name_low = ingredient_name.strip().lower()
    if not parsed_unit:
        if any(w in name_low for w in ("oil", "milk", "water", "juice")):
            parsed_unit = "ml" if qty_val >= Decimal("15") else "tbsp"
        elif any(w in name_low for w in _WEIGHT_OR_VOLUME_ITEMS):
            parsed_unit = "g" if qty_val >= Decimal("10") else "tbsp"
        else:
            parsed_unit = "pcs"

    # Guard against nonsensical '100 pcs' on weight-based items like spinach
    if parsed_unit == "pcs" and any(w in name_low for w in _WEIGHT_OR_VOLUME_ITEMS):
        if qty_val >= Decimal("20"):
            parsed_unit = "ml" if "oil" in name_low or "milk" in name_low else "g"
        elif qty_val != qty_val.to_integral_value():
            parsed_unit = "cup"

    # Guard against fractional pcs on count-based whole items (e.g. 1.5 pcs cucumber -> 2 pcs)
    if parsed_unit == "pcs" and any(c in name_low for c in _COUNT_ONLY_ITEMS):
        if qty_val != qty_val.to_integral_value():
            qty_val = qty_val.quantize(Decimal("1"))
            if qty_val < Decimal("1"):
                qty_val = Decimal("1")

    return qty_val, parsed_unit


def _convert_compatible(qty: Decimal, from_unit: str, to_unit: str) -> Decimal | None:
    """Convert only between strictly compatible units via the single pantry conversion service."""
    from app.modules.inventory.ingredient_aggregation_service import convert_to_target_unit

    converted, _ = convert_to_target_unit(qty, from_unit, to_unit)
    return converted


def _fmt_dec(val: Decimal) -> str:
    s = f"{val:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def match_inventory_item(
    ingredient_name: str,
    inventory_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match a recipe ingredient against active household inventory using normalized names & synonyms."""
    target_norm = FoodNameNormalizer.normalize(ingredient_name)
    target_low = ingredient_name.strip().lower()
    if not target_norm and not target_low:
        return None

    # Phase 7.2: Family & variant-aware resolution first
    from app.modules.catalog.ingredient_resolver import IngredientResolver

    req_res = IngredientResolver().resolve_text(ingredient_name)
    if req_res.variant_id:
        active_items = [
            it
            for it in inventory_items
            if Decimal(
                str(
                    it.get("qty")
                    if it.get("qty") is not None
                    else (
                        it.get("quantity")
                        if it.get("quantity") is not None
                        else (it.get("normalized_qty") if it.get("normalized_qty") is not None else 1)
                    )
                )
            )
            > Decimal("0")
        ]
        # 1. Exact variant_id match
        for it in active_items:
            if it.get("variant_id") and it.get("variant_id") == req_res.variant_id:
                return it
        # 2. If recipe specifies generic family (unspecified), allow any variant in same family
        if req_res.is_unspecified_variant and req_res.family_id:
            for it in active_items:
                if it.get("family_id") and it.get("family_id") == req_res.family_id:
                    return it
        # 3. If recipe specifies a specific variant, do NOT silently treat a different variant in same family as exact match
        if not req_res.is_unspecified_variant and req_res.family_id:
            has_other_variant_same_family = any(
                it.get("family_id") == req_res.family_id and it.get("variant_id") != req_res.variant_id
                for it in active_items
            )
            if has_other_variant_same_family:
                return None

    candidate_terms = {t for t in (target_norm, target_low) if t}
    for syn_key, syn_vals in _INGREDIENT_SYNONYMS.items():
        if syn_key in candidate_terms or any(syn_key in t for t in candidate_terms):
            candidate_terms.update(syn_vals)

    target_tokens = {
        t for t in re.split(r"[\s\-_/]+", target_norm or target_low) if len(t) >= 3
    }

    best_item: dict[str, Any] | None = None
    best_score = 0

    for item in inventory_items:
        raw_qty_val = item.get("qty")
        if raw_qty_val is None:
            raw_qty_val = item.get("quantity")
        if raw_qty_val is None:
            raw_qty_val = item.get("normalized_qty")
        qty = Decimal(str(raw_qty_val if raw_qty_val is not None else 1))
        if qty <= Decimal("0"):
            continue
        raw_item_name = str(item.get("name") or "").strip().lower()
        norm_item_name = str(
            item.get("normalized_name") or FoodNameNormalizer.normalize(raw_item_name)
        ).strip().lower()
        if not raw_item_name and not norm_item_name:
            continue

        # Prevent modifier collisions: e.g. 'milk' must not match 'milk chocolate' or 'ice cream'
        if target_norm in {"milk", "whole milk", "fresh milk", "toned milk"} and any(
            bad in raw_item_name for bad in ("chocolate", "ice cream", "biscuit", "cookie", "powder")
        ):
            continue

        for term in candidate_terms:
            if term == norm_item_name or term == raw_item_name:
                return item
            if len(term) >= 4 and (term in norm_item_name or norm_item_name in term):
                score = 80 + len(term)
                if score > best_score:
                    best_score = score
                    best_item = item

        item_tokens = {
            t for t in re.split(r"[\s\-_/]+", norm_item_name or raw_item_name) if len(t) >= 3
        }
        overlap = len(target_tokens & item_tokens)
        if overlap > 0 and overlap * 10 > best_score:
            best_score = overlap * 10
            best_item = item

    return best_item


def reconcile_ingredient(
    ing: dict[str, Any],
    inventory_items: list[dict[str, Any]],
    *,
    is_optional: bool = False,
) -> dict[str, Any] | None:
    """Validate and reconcile a single ingredient against backend inventory truth."""
    name = str(ing.get("name") or ing.get("item") or "").strip()
    if is_blocked_ingredient_name(name):
        return None

    raw_qty = ing.get("required_qty") or ing.get("qty") or ing.get("quantity") or "1"
    raw_unit = ing.get("base_unit") or ing.get("unit")
    req_qty, req_unit = parse_quantity_and_unit(raw_qty, raw_unit, ingredient_name=name)

    matched = match_inventory_item(name, inventory_items)
    if matched is None:
        return {
            "name": name,
            "canonical_item_id": None,
            "quantity": str(req_qty),
            "base_unit": req_unit,
            "essential": not is_optional,
            "status": "missing",
            "available": False,
            "available_qty": None,
            "missing_qty": str(req_qty),
            "expiring_soon": False,
            "matched_name": None,
            "unit_compatible": True,
        }

    raw_inv_qty = matched.get("qty")
    if raw_inv_qty is None:
        raw_inv_qty = matched.get("quantity")
    if raw_inv_qty is None:
        raw_inv_qty = matched.get("normalized_qty")
    inv_qty = Decimal(str(raw_inv_qty if raw_inv_qty is not None else 1))
    inv_unit = str(matched.get("base_unit") or matched.get("unit") or "pcs")
    conv_req = _convert_compatible(req_qty, req_unit, inv_unit)

    if conv_req is not None:
        unit_compatible = True
        if inv_qty >= conv_req:
            status = "available"
            missing_qty = None
        elif inv_qty > Decimal("0"):
            status = "partial"
            inv_in_req = _convert_compatible(inv_qty, inv_unit, req_unit)
            diff_in_req = (req_qty - inv_in_req) if inv_in_req is not None else (conv_req - inv_qty)
            missing_qty = _fmt_dec(max(Decimal("0"), diff_in_req))
        else:
            status = "missing"
            missing_qty = _fmt_dec(req_qty)
    else:
        # Incompatible units (e.g. pantry has 1 pcs vs recipe 50 g): never silently convert or claim full availability
        unit_compatible = False
        if inv_qty <= Decimal("0"):
            status = "missing"
            missing_qty = _fmt_dec(req_qty)
        elif req_unit in (
            "g", "kg", "ml", "l", "pcs", "dozen",
            "pack", "bottle", "packet", "box", "jar", "can", "loaf", "slice",
        ):
            status = "partial"
            missing_qty = _fmt_dec(req_qty)
        else:
            status = "available"
            missing_qty = None

    return {
        "name": name,
        "canonical_item_id": matched.get("canonical_item_id"),
        "quantity": _fmt_dec(req_qty),
        "base_unit": req_unit,
        "essential": not is_optional,
        "status": status,
        "available": status in ("available", "partial"),
        "available_qty": f"{_fmt_dec(inv_qty)} {inv_unit}",
        "missing_qty": missing_qty,
        "expiring_soon": bool(matched.get("expiring_soon")),
        "matched_name": matched.get("name"),
        "unit_compatible": unit_compatible,
    }


def classify_recommendation_type(
    reconciled_ingredients: list[dict[str, Any]],
    substitutions: list[str],
    inventory_items: list[dict[str, Any]],
) -> RecommendationType:
    """Classify recipe into COOK_NOW, COOK_WITH_SUBSTITUTIONS, or REQUIRES_SHOPPING."""
    essential = [i for i in reconciled_ingredients if i.get("essential", True)]
    if not essential:
        return "REQUIRES_SHOPPING"

    avail_count = sum(1 for i in essential if i["status"] == "available")
    partial_count = sum(1 for i in essential if i["status"] == "partial")
    partial_or_avail = avail_count + partial_count
    missing = [i for i in essential if i["status"] == "missing"]

    if avail_count == len(essential):
        return "COOK_NOW"

    # Almost ready when at most 1 essential ingredient is missing or insufficient
    if (len(missing) + partial_count) == 1 and len(essential) >= 2:
        return "COOK_WITH_SUBSTITUTIONS"

    if partial_or_avail >= max(1, (len(essential) + 1) // 2) and (missing or partial_count > 0):
        # Check if substitutions reference pantry items or only partial stock is short
        pantry_tokens = {
            FoodNameNormalizer.normalize(str(it.get("name") or "")).lower()
            for it in inventory_items
        }
        has_pantry_sub = any(
            any(tok and tok in sub.lower() for tok in pantry_tokens)
            for sub in substitutions
        )
        if (has_pantry_sub and partial_or_avail >= len(essential) - 1) or len(missing) == 0:
            return "COOK_WITH_SUBSTITUTIONS"

    return "REQUIRES_SHOPPING"
