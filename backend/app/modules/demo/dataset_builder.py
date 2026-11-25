"""Pure, deterministic builder for the isolated public demo dataset.

Design guarantees:
- Fixed UUID5 namespace (``DEMO_UUID_NAMESPACE``) for repeatable deterministic remapping.
- Explicit ``FKRemappingRegistry`` — never blindly rewrites UUID-looking strings or retains
  unresolved references to excluded source entities.
- Allowlist-first field sanitization — free-text, raw OCR payloads, attachments, receipt/order
  numbers, payment info, and Clerk identifiers are unconditionally stripped or replaced.
- Supplementary regex PII assertion gate that aborts if any PII pattern survives.
- Global shared tables (``canonical_ingredients``, ``dishes``, ``dish_ingredients``, global
  ``ingredient_aliases``) are referenced read-only and never duplicated or modified.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid5

from app.modules.demo.static_daily_plan import build_static_demo_daily_plan

DEMO_UUID_NAMESPACE = UUID("d3a04b6f-9c12-4a51-9f31-1e8b47c9024d")
DEMO_CLERK_USER_ID = "demo_public_readonly"
DEMO_USER_ID = uuid5(DEMO_UUID_NAMESPACE, "user:demo_public_readonly")
DEMO_HOUSEHOLD_ID = uuid5(DEMO_UUID_NAMESPACE, "household:demo_public_readonly")

# Deterministic allowlisted store/restaurant labels replacing source merchant names
_GROCERY_STORE_LABELS: tuple[str, ...] = (
    "FreshMart Groceries",
    "Green Basket Market",
    "Neighborhood Kirana",
    "Daily Essentials Mart",
    "Organic Pantry Co.",
    "City Fresh Supermarket",
)
_RESTAURANT_LABELS: tuple[str, ...] = (
    "Spice Garden Kitchen",
    "Urban Cafe & Bakery",
    "HomeStyle Bistro",
    "Corner Tiffin House",
)

# Supplementary PII regexes (defense-in-depth after explicit field allowlisting)
_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}\b|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),
    "address_kw": re.compile(
        r"\b(?:road|street|st\.|lane|nagar|colony|pincode|pin\s*code|"
        r"deliver(?:y|ed)\s+to|address|(?:flat|apt|floor|sector|block)\s+(?:no\.?\s*)?\d+[a-z]?)\b",
        re.IGNORECASE,
    ),
    "payment_or_order_kw": re.compile(
        r"\b(?:upi|gpay|phonepe|paytm|visa|mastercard|rupay|card\s*(?:no|ending|xxxx)|"
        r"cvv|utr|txn|transaction\s*id|order\s*id|invoice\s*no|gstin)\b",
        re.IGNORECASE,
    ),
    "secret_or_clerk_id": re.compile(
        r"\b(?:user_[0-9][A-Za-z0-9]{15,}|(?:pk|sk)_(?:test|live)_\w+|hf_[A-Za-z0-9]{10,})\b"
    ),
}

_UUID_FIELDS_SKIP_PII_SCAN = frozenset(
    {
        "id",
        "household_id",
        "user_id",
        "created_by",
        "updated_by",
        "merged_by",
        "actor_user_id",
        "cooked_by",
        "canonical_item_id",
        "canonical_ingredient_id",
        "merged_into_id",
        "duplicate_canonical_id",
        "survivor_canonical_id",
        "purchase_id",
        "purchase_item_id",
        "lot_id",
        "reference_id",
        "cooking_plan_id",
        "daily_meal_plan_id",
    }
)


class FKRemappingRegistry:
    """Explicit per-table UUID remapping registry.

    Only registered source IDs can be resolved; unknown/excluded IDs return ``None`` rather
    than leaking source UUIDs or leaving dangling foreign keys.
    """

    def __init__(self, namespace: UUID = DEMO_UUID_NAMESPACE) -> None:
        self._namespace = namespace
        self._maps: dict[str, dict[UUID, UUID]] = {}

    def register(self, table_name: str, source_id: UUID) -> UUID:
        table_map = self._maps.setdefault(table_name, {})
        if source_id not in table_map:
            table_map[source_id] = uuid5(self._namespace, f"{table_name}:{source_id}")
        return table_map[source_id]

    def resolve(self, table_name: str, source_id: Any) -> UUID | None:
        if source_id is None:
            return None
        uid = source_id if isinstance(source_id, UUID) else _try_uuid(source_id)
        if uid is None:
            return None
        return self._maps.get(table_name, {}).get(uid)

    def has(self, table_name: str, source_id: Any) -> bool:
        return self.resolve(table_name, source_id) is not None


def _try_uuid(val: Any) -> UUID | None:
    if isinstance(val, UUID):
        return val
    try:
        return UUID(str(val).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _clean_merchant_label(raw_name: str | None, *, is_outside_food: bool = False) -> str:
    palette = _RESTAURANT_LABELS if is_outside_food else _GROCERY_STORE_LABELS
    key = (raw_name or "default").strip().lower()
    idx = int(uuid5(DEMO_UUID_NAMESPACE, f"merchant:{key}").int % len(palette))
    return palette[idx]


def _scrub_item_label(raw_text: str | None, fallback: str = "Grocery Item") -> str:
    """Remove any stray phone/email/address fragments from grocery display names."""
    text = (raw_text or "").strip()
    if not text:
        return fallback
    for pat_name in ("email", "phone", "secret_or_clerk_id"):
        text = _PII_PATTERNS[pat_name].sub("", text)
    if _PII_PATTERNS["address_kw"].search(text) or _PII_PATTERNS["payment_or_order_kw"].search(
        text
    ):
        return fallback
    cleaned = re.sub(r"\s+", " ", text).strip(" -,:;")
    return cleaned[:120] if cleaned else fallback


def _sanitize_cooking_ingredients(
    raw_items: Any, registry: FKRemappingRegistry
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        remapped_cid = registry.resolve("canonical_items", entry.get("canonical_item_id"))
        rec: dict[str, Any] = {
            "name": _scrub_item_label(str(entry.get("name") or ""), fallback="Ingredient"),
        }
        if remapped_cid is not None:
            rec["canonical_item_id"] = str(remapped_cid)
        for safe_k in (
            "quantity",
            "qty",
            "required_qty",
            "base_unit",
            "unit",
            "status",
            "essential",
        ):
            if entry.get(safe_k) is not None:
                rec[safe_k] = entry[safe_k]
        out.append(rec)
    return out


def _sanitize_deductions(raw_items: Any, registry: FKRemappingRegistry) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        remapped_lot = registry.resolve("inventory_lots", entry.get("lot_id"))
        remapped_cid = registry.resolve("canonical_items", entry.get("canonical_item_id"))
        rec: dict[str, Any] = {}
        if remapped_lot is not None:
            rec["lot_id"] = str(remapped_lot)
        if remapped_cid is not None:
            rec["canonical_item_id"] = str(remapped_cid)
        for safe_k in ("name", "quantity", "deducted_qty", "base_unit", "unit"):
            if entry.get(safe_k) is not None:
                rec[safe_k] = (
                    _scrub_item_label(str(entry[safe_k]), fallback="Ingredient")
                    if safe_k == "name"
                    else entry[safe_k]
                )
        if rec:
            out.append(rec)
    return out


def scan_dataset_for_pii(dataset: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Supplementary regex PII scan over the entire built demo dataset.

    Returns a list of violation descriptions (field/category only; never includes raw values).
    """
    violations: list[str] = []
    for tname, rows in dataset.items():
        for idx, row in enumerate(rows):
            for col_name, val in row.items():
                if val is None or col_name in _UUID_FIELDS_SKIP_PII_SCAN:
                    continue
                if isinstance(val, (dict, list)):
                    serialized = json.dumps(val, default=str)
                elif isinstance(val, (int, float, bool, date, datetime, UUID)):
                    continue
                else:
                    serialized = str(val)
                for pat_name, rx in _PII_PATTERNS.items():
                    if rx.search(serialized):
                        violations.append(
                            f"PII pattern '{pat_name}' detected in {tname}[{idx}].{col_name}"
                        )
    return violations


@dataclass
class DemoBuildReport:
    demo_user_id: UUID
    demo_household_id: UUID
    source_household_id: UUID
    dataset: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    table_counts: dict[str, int] = field(default_factory=dict)
    sanitization_summary: dict[str, Any] = field(default_factory=dict)
    pii_violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.pii_violations) == 0


def build_demo_dataset(
    *,
    source_household_id: UUID,
    source_rows: dict[str, list[dict[str, Any]]],
    global_canonical_ingredient_ids: set[UUID],
    now_utc: datetime | None = None,
    plan_date: date | None = None,
) -> DemoBuildReport:
    """Build a complete, isolated, allowlist-sanitized demo dataset from source household rows."""
    if source_household_id == DEMO_HOUSEHOLD_ID:
        raise ValueError("Source household ID must never equal DEMO_HOUSEHOLD_ID.")

    ref_now = now_utc or datetime.now(UTC)
    registry = FKRemappingRegistry(DEMO_UUID_NAMESPACE)
    sanitization_stats: dict[str, int] = {
        "merchants_anonymized": 0,
        "raw_ocr_payloads_stripped": 0,
        "attachments_stripped": 0,
        "receipt_and_order_ids_stripped": 0,
        "payment_methods_stripped": 0,
        "free_text_notes_stripped": 0,
        "unresolved_fk_refs_dropped": 0,
    }

    # 1. Pre-register all source PKs for explicit FK remapping
    src_canonical_items = [
        r
        for r in source_rows.get("canonical_items", [])
        if r.get("household_id") == source_household_id
    ]
    for r in src_canonical_items:
        registry.register("canonical_items", r["id"])

    src_purchases = [
        r
        for r in source_rows.get("purchases", [])
        if r.get("household_id") == source_household_id and r.get("status") != "draft"
    ]
    for r in src_purchases:
        registry.register("purchases", r["id"])

    src_purchase_items = [
        r
        for r in source_rows.get("purchase_items", [])
        if r.get("household_id") == source_household_id
        and registry.has("purchases", r.get("purchase_id"))
    ]
    for r in src_purchase_items:
        registry.register("purchase_items", r["id"])

    src_lots = [
        r
        for r in source_rows.get("inventory_lots", [])
        if r.get("household_id") == source_household_id
    ]
    for r in src_lots:
        if registry.has("canonical_items", r.get("canonical_item_id")):
            registry.register("inventory_lots", r["id"])

    src_cooking_plans = [
        r
        for r in source_rows.get("cooking_plans", [])
        if r.get("household_id") == source_household_id
    ]
    for r in src_cooking_plans:
        registry.register("cooking_plans", r["id"])

    src_meal_history = [
        r
        for r in source_rows.get("meal_history", [])
        if r.get("household_id") == source_household_id
        and registry.has("cooking_plans", r.get("cooking_plan_id"))
    ]
    for r in src_meal_history:
        registry.register("meal_history", r["id"])

    # 2. Synthetic User, Household, Member, Preferences
    dataset: dict[str, list[dict[str, Any]]] = {
        "users": [
            {
                "id": DEMO_USER_ID,
                "clerk_user_id": DEMO_CLERK_USER_ID,
                "email": None,
                "display_name": "Demo Kitchen",
                "created_at": ref_now,
                "updated_at": ref_now,
            }
        ],
        "households": [
            {
                "id": DEMO_HOUSEHOLD_ID,
                "name": "Demo Kitchen",
                "currency_code": "INR",
                "created_by": DEMO_USER_ID,
                "created_at": ref_now,
                "updated_at": ref_now,
            }
        ],
        "household_members": [
            {
                "household_id": DEMO_HOUSEHOLD_ID,
                "user_id": DEMO_USER_ID,
                "role": "owner",
                "joined_at": ref_now,
            }
        ],
        "household_recipe_preferences": [
            {
                "household_id": DEMO_HOUSEHOLD_ID,
                "timezone": "Asia/Kolkata",
                "location_name": None,
                "diet_type": "vegetarian",
                "dietary_restrictions": [],
                "preferred_cuisines": ["Indian"],
                "preferred_meal_types": ["breakfast", "lunch", "snacks", "dinner"],
                "max_prep_minutes": 30,
                "recommendation_mode": "balanced",
                "personalisation_prompt_dismissed_at": None,
                "updated_at": ref_now,
            }
        ],
    }

    # 3. canonical_items (allowlisted columns only)
    demo_canonical_items: list[dict[str, Any]] = []
    canonical_name_by_demo_id: dict[UUID, dict[str, Any]] = {}
    for r in src_canonical_items:
        new_id = registry.resolve("canonical_items", r["id"])
        assert new_id is not None
        merged_into = registry.resolve("canonical_items", r.get("merged_into_id"))
        if r.get("merged_into_id") is not None and merged_into is None:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
        clean_name = _scrub_item_label(r.get("name"), fallback="Pantry Item")
        row = {
            "id": new_id,
            "household_id": DEMO_HOUSEHOLD_ID,
            "slug": r["slug"],
            "name": clean_name,
            "category": r["category"],
            "secondary_category": r.get("secondary_category"),
            "food_state": r["food_state"],
            "is_prepared_food": bool(r["is_prepared_food"]),
            "pantry_eligible": bool(r["pantry_eligible"]),
            "budget_treatment": r["budget_treatment"],
            "language": r["language"],
            "source_dataset": "demo_curated",
            "source_record_id": None,
            "verified": bool(r["verified"]),
            "default_base_unit": r["default_base_unit"],
            "default_shelf_life_days": r.get("default_shelf_life_days"),
            "low_stock_threshold_qty": r["low_stock_threshold_qty"],
            "merged_into_id": merged_into,
            "is_active": bool(r["is_active"]),
            "created_at": r["created_at"],
        }
        demo_canonical_items.append(row)
        canonical_name_by_demo_id[new_id] = row
    dataset["canonical_items"] = demo_canonical_items

    # 4. canonical_merge_log (evidence_json stripped to {})
    demo_merge_logs: list[dict[str, Any]] = []
    for r in source_rows.get("canonical_merge_log", []):
        if r.get("household_id") != source_household_id:
            continue
        dup_id = registry.resolve("canonical_items", r.get("duplicate_canonical_id"))
        surv_id = registry.resolve("canonical_items", r.get("survivor_canonical_id"))
        if dup_id is None or surv_id is None:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
            continue
        demo_merge_logs.append(
            {
                "id": registry.register("canonical_merge_log", r["id"]),
                "household_id": DEMO_HOUSEHOLD_ID,
                "duplicate_canonical_id": dup_id,
                "survivor_canonical_id": surv_id,
                "evidence_json": {},
                "confidence": r["confidence"],
                "merged_by": DEMO_USER_ID,
                "merged_at": r["merged_at"],
                "reversed_at": r.get("reversed_at"),
            }
        )
    dataset["canonical_merge_log"] = demo_merge_logs

    # 5. item_aliases (deduplicate normalized_alias per demo household after scrubbing)
    demo_item_aliases: list[dict[str, Any]] = []
    seen_item_aliases: set[str] = set()
    for r in source_rows.get("item_aliases", []):
        if r.get("household_id") != source_household_id:
            continue
        cid = registry.resolve("canonical_items", r.get("canonical_item_id"))
        if cid is None:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
            continue
        clean_alias = _scrub_item_label(r.get("normalized_alias"), fallback="")
        if not clean_alias or clean_alias in seen_item_aliases:
            continue
        seen_item_aliases.add(clean_alias)
        demo_item_aliases.append(
            {
                "id": registry.register("item_aliases", r["id"]),
                "household_id": DEMO_HOUSEHOLD_ID,
                "canonical_item_id": cid,
                "normalized_alias": clean_alias,
                "source": r["source"],
                "alias_kind": r["alias_kind"],
                "created_at": r["created_at"],
            }
        )
    dataset["item_aliases"] = demo_item_aliases

    # 6. ingredient_aliases (household-scoped only; global rows untouched)
    demo_ing_aliases: list[dict[str, Any]] = []
    for r in source_rows.get("ingredient_aliases", []):
        if r.get("household_id") != source_household_id:
            continue
        ci_id = r.get("canonical_ingredient_id")
        if ci_id not in global_canonical_ingredient_ids:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
            continue
        demo_ing_aliases.append(
            {
                "id": registry.register("ingredient_aliases", r["id"]),
                "household_id": DEMO_HOUSEHOLD_ID,
                "canonical_ingredient_id": ci_id,
                "alias": _scrub_item_label(r.get("alias"), fallback="ingredient"),
                "normalized_alias": _scrub_item_label(
                    r.get("normalized_alias"), fallback="ingredient"
                ),
                "language": r["language"],
                "alias_type": r["alias_type"],
                "source": "demo_curated",
                "confidence": r["confidence"],
                "is_approved": bool(r["is_approved"]),
                "original_phrase": None,
                "target_family_id": r.get("target_family_id"),
                "target_variant_id": r.get("target_variant_id"),
                "confirmed_at": r.get("confirmed_at"),
                "confirmation_count": r["confirmation_count"],
                "provenance": "demo_curated",
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    dataset["ingredient_aliases"] = demo_ing_aliases

    # 7. ingredient_mappings
    demo_ing_mappings: list[dict[str, Any]] = []
    for r in source_rows.get("ingredient_mappings", []):
        if r.get("household_id") != source_household_id:
            continue
        cid = registry.resolve("canonical_items", r.get("canonical_item_id"))
        ci_id = r.get("canonical_ingredient_id")
        if cid is None or ci_id not in global_canonical_ingredient_ids:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
            continue
        demo_ing_mappings.append(
            {
                "id": registry.register("ingredient_mappings", r["id"]),
                "household_id": DEMO_HOUSEHOLD_ID,
                "canonical_item_id": cid,
                "canonical_ingredient_id": ci_id,
                "matching_confidence": r["matching_confidence"],
                "mapping_source": r["mapping_source"],
                "user_confirmed": bool(r["user_confirmed"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    dataset["ingredient_mappings"] = demo_ing_mappings

    # 8. household_budgets
    demo_budgets: list[dict[str, Any]] = []
    for r in source_rows.get("household_budgets", []):
        if r.get("household_id") != source_household_id:
            continue
        demo_budgets.append(
            {
                "id": registry.register("household_budgets", r["id"]),
                "household_id": DEMO_HOUSEHOLD_ID,
                "period_type": r["period_type"],
                "overall_limit_paise": r["overall_limit_paise"],
                "grocery_limit_paise": r.get("grocery_limit_paise"),
                "outside_food_limit_paise": r.get("outside_food_limit_paise"),
                "effective_from": r["effective_from"],
                "created_by": DEMO_USER_ID,
                "updated_by": DEMO_USER_ID,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    dataset["household_budgets"] = demo_budgets

    # 9. purchases (strict allowlist; raw_ocr_payload, attachments, receipt/order/payment stripped)
    demo_purchases: list[dict[str, Any]] = []
    for r in src_purchases:
        new_pid = registry.resolve("purchases", r["id"])
        assert new_pid is not None
        is_outside = str(r.get("receipt_category") or "") == "restaurant_food"
        if r.get("raw_ocr_payload"):
            sanitization_stats["raw_ocr_payloads_stripped"] += 1
        if r.get("attachments_json"):
            sanitization_stats["attachments_stripped"] += 1
        if r.get("receipt_number") or r.get("order_id"):
            sanitization_stats["receipt_and_order_ids_stripped"] += 1
        if r.get("payment_method"):
            sanitization_stats["payment_methods_stripped"] += 1
        sanitization_stats["merchants_anonymized"] += 1
        demo_purchases.append(
            {
                "id": new_pid,
                "household_id": DEMO_HOUSEHOLD_ID,
                "created_by": DEMO_USER_ID,
                "status": r["status"],
                "source_type": r["source_type"],
                "document_type": r["document_type"],
                "ocr_adapter": "demo_curated",
                "merchant_name": _clean_merchant_label(
                    r.get("merchant_name"), is_outside_food=is_outside
                ),
                "receipt_number": None,
                "order_id": None,
                "purchased_on": r["purchased_on"],
                "purchased_time": r.get("purchased_time"),
                "currency_code": r["currency_code"],
                "receipt_category": r["receipt_category"],
                "payment_method": None,
                "subtotal_paise": r.get("subtotal_paise"),
                "discount_paise": r["discount_paise"],
                "additional_charges_paise": r["additional_charges_paise"],
                "cgst_paise": r["cgst_paise"],
                "sgst_paise": r["sgst_paise"],
                "igst_paise": r["igst_paise"],
                "other_tax_paise": r["other_tax_paise"],
                "tax_paise": r["tax_paise"],
                "rounding_paise": r["rounding_paise"],
                "total_amount_paise": r["total_amount_paise"],
                "raw_ocr_payload": {},
                "missing_fields_json": [],
                "attachments_json": [],
                "confirmed_at": r.get("confirmed_at"),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    dataset["purchases"] = demo_purchases

    # 10. food_consumptions
    demo_food_consumptions: list[dict[str, Any]] = []
    for r in source_rows.get("food_consumptions", []):
        if r.get("household_id") != source_household_id:
            continue
        pid = registry.resolve("purchases", r.get("purchase_id"))
        if pid is None:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
            continue
        if r.get("notes"):
            sanitization_stats["free_text_notes_stripped"] += 1
        clean_summary = None
        if isinstance(r.get("items_summary"), list):
            clean_summary = [
                {
                    "name": _scrub_item_label(str(it.get("name") or ""), fallback="Meal Item"),
                    "qty": it.get("qty"),
                    "unit": it.get("unit"),
                    "line_total_paise": it.get("line_total_paise"),
                }
                for it in r["items_summary"]
                if isinstance(it, dict)
            ]
        demo_food_consumptions.append(
            {
                "id": registry.register("food_consumptions", r["id"]),
                "household_id": DEMO_HOUSEHOLD_ID,
                "purchase_id": pid,
                "consumed_on": r["consumed_on"],
                "meal_type": r["meal_type"],
                "dining_mode": r["dining_mode"],
                "merchant_name": _clean_merchant_label(
                    r.get("merchant_name"), is_outside_food=True
                ),
                "amount_paise": r["amount_paise"],
                "currency_code": r["currency_code"],
                "items_summary": clean_summary,
                "people_count": r.get("people_count"),
                "notes": None,
                "created_at": r["created_at"],
            }
        )
    dataset["food_consumptions"] = demo_food_consumptions

    # 11. purchase_items (raw_text replaced with clean display_name; metadata stripped)
    demo_purchase_items: list[dict[str, Any]] = []
    for r in src_purchase_items:
        new_pi_id = registry.resolve("purchase_items", r["id"])
        pid = registry.resolve("purchases", r["purchase_id"])
        assert new_pi_id is not None and pid is not None
        cid = registry.resolve("canonical_items", r.get("canonical_item_id"))
        if r.get("canonical_item_id") is not None and cid is None:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
        clean_display = _scrub_item_label(r.get("display_name"), fallback="Grocery Item")
        clean_norm = (
            _scrub_item_label(r.get("normalized_name"), fallback=clean_display.lower())
            if r.get("normalized_name")
            else None
        )
        demo_purchase_items.append(
            {
                "id": new_pi_id,
                "purchase_id": pid,
                "household_id": DEMO_HOUSEHOLD_ID,
                "line_index": r["line_index"],
                "raw_text": clean_display,
                "canonical_item_id": cid,
                "display_name": clean_display,
                "normalized_name": clean_norm,
                "brand": _scrub_item_label(r.get("brand"), fallback="") or None
                if r.get("brand")
                else None,
                "variant": r.get("variant"),
                "pack_size": r.get("pack_size"),
                "category": r["category"],
                "secondary_category": r.get("secondary_category"),
                "match_confidence": r["match_confidence"],
                "matching_confidence": r.get("matching_confidence"),
                "eligibility_reason": r.get("eligibility_reason"),
                "unit_price_provenance": r.get("unit_price_provenance"),
                "normalized_qty": r["normalized_qty"],
                "base_unit": r["base_unit"],
                "unit_price_paise": r["unit_price_paise"],
                "line_total_paise": r["line_total_paise"],
                "discount_paise": r["discount_paise"],
                "tax_paise": r["tax_paise"],
                "tax_rate_bps": r.get("tax_rate_bps"),
                "confidence": r.get("confidence"),
                "warnings_json": [],
                "track_inventory": bool(r["track_inventory"]),
                "food_state": r.get("food_state"),
                "budget_treatment": r.get("budget_treatment"),
                "classification_confidence": r.get("classification_confidence"),
                "matching_method": r.get("matching_method"),
                "resolution_method": r.get("resolution_method"),
                "resolution_score": r.get("resolution_score"),
                "resolution_status": r.get("resolution_status"),
                "resolution_metadata_json": {},
                "classification_source": r.get("classification_source"),
                "expires_on": r.get("expires_on"),
                "created_at": r["created_at"],
            }
        )
    dataset["purchase_items"] = demo_purchase_items

    # 12. inventory_lots
    demo_lots: list[dict[str, Any]] = []
    raw_inventory_payload: list[dict[str, Any]] = []
    for r in src_lots:
        new_lot_id = registry.resolve("inventory_lots", r["id"])
        cid = registry.resolve("canonical_items", r.get("canonical_item_id"))
        if new_lot_id is None or cid is None:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
            continue
        pi_id = registry.resolve("purchase_items", r.get("purchase_item_id"))
        if r.get("purchase_item_id") is not None and pi_id is None:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
        lot_row = {
            "id": new_lot_id,
            "household_id": DEMO_HOUSEHOLD_ID,
            "canonical_item_id": cid,
            "purchase_item_id": pi_id,
            "initial_qty": r["initial_qty"],
            "remaining_qty": r["remaining_qty"],
            "base_unit": r["base_unit"],
            "unit_cost_paise": r["unit_cost_paise"],
            "purchased_at": r["purchased_at"],
            "expires_on": r.get("expires_on"),
            "lot_status": r["lot_status"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        demo_lots.append(lot_row)
        if r.get("lot_status") == "active" and cid in canonical_name_by_demo_id:
            ci_meta = canonical_name_by_demo_id[cid]
            raw_inventory_payload.append(
                {
                    "canonical_item_id": str(cid),
                    "name": ci_meta["name"],
                    "quantity": float(r["remaining_qty"]),
                    "base_unit": r["base_unit"],
                }
            )
    dataset["inventory_lots"] = demo_lots

    # 13. stock_ledger (notes stripped; reference_id explicitly remapped by reference_type)
    ref_type_to_table = {
        "purchase": "purchases",
        "purchase_item": "purchase_items",
        "cooking_plan": "cooking_plans",
        "meal_history": "meal_history",
        "lot": "inventory_lots",
    }
    demo_ledger: list[dict[str, Any]] = []
    for r in source_rows.get("stock_ledger", []):
        if r.get("household_id") != source_household_id:
            continue
        lot_id = registry.resolve("inventory_lots", r.get("lot_id"))
        cid = registry.resolve("canonical_items", r.get("canonical_item_id"))
        if lot_id is None or cid is None:
            sanitization_stats["unresolved_fk_refs_dropped"] += 1
            continue
        if r.get("notes"):
            sanitization_stats["free_text_notes_stripped"] += 1
        ref_type = r.get("reference_type")
        ref_id = None
        if r.get("reference_id") is not None and ref_type in ref_type_to_table:
            ref_id = registry.resolve(ref_type_to_table[ref_type], r["reference_id"])
            if ref_id is None:
                sanitization_stats["unresolved_fk_refs_dropped"] += 1
        demo_ledger.append(
            {
                "id": registry.register("stock_ledger", r["id"]),
                "household_id": DEMO_HOUSEHOLD_ID,
                "lot_id": lot_id,
                "canonical_item_id": cid,
                "event_type": r["event_type"],
                "quantity_delta": r["quantity_delta"],
                "base_unit": r["base_unit"],
                "balance_after_lot": r["balance_after_lot"],
                "unit_cost_paise": r["unit_cost_paise"],
                "reference_type": ref_type if ref_id is not None else None,
                "reference_id": ref_id,
                "actor_user_id": DEMO_USER_ID,
                "notes": None,
                "created_at": r["created_at"],
            }
        )
    dataset["stock_ledger"] = demo_ledger

    # 14. cooking_plans
    demo_cooking_plans: list[dict[str, Any]] = []
    for r in src_cooking_plans:
        new_cp_id = registry.resolve("cooking_plans", r["id"])
        assert new_cp_id is not None
        demo_cooking_plans.append(
            {
                "id": new_cp_id,
                "household_id": DEMO_HOUSEHOLD_ID,
                "created_by": DEMO_USER_ID,
                "recipe_id": r.get("recipe_id"),
                "recipe_title": _scrub_item_label(r.get("recipe_title"), fallback="Home Recipe"),
                "status": r["status"],
                "planned_servings": r["planned_servings"],
                "ingredients": _sanitize_cooking_ingredients(r.get("ingredients"), registry),
                "deductions": _sanitize_deductions(r.get("deductions"), registry)
                if r.get("deductions") is not None
                else None,
                "meal_type": r.get("meal_type"),
                "cuisine": r.get("cuisine"),
                "prep_minutes": r.get("prep_minutes"),
                "cook_minutes": r.get("cook_minutes"),
                "recipe_snapshot": None,
                "started_at": r["started_at"],
                "completed_at": r.get("completed_at"),
                "cancelled_at": r.get("cancelled_at"),
            }
        )
    dataset["cooking_plans"] = demo_cooking_plans

    # 15. meal_history (feedback_comment free-text stripped)
    demo_meal_history: list[dict[str, Any]] = []
    for r in src_meal_history:
        new_mh_id = registry.resolve("meal_history", r["id"])
        cp_id = registry.resolve("cooking_plans", r["cooking_plan_id"])
        assert new_mh_id is not None and cp_id is not None
        if r.get("feedback_comment"):
            sanitization_stats["free_text_notes_stripped"] += 1
        demo_meal_history.append(
            {
                "id": new_mh_id,
                "household_id": DEMO_HOUSEHOLD_ID,
                "cooking_plan_id": cp_id,
                "cooked_by": DEMO_USER_ID,
                "recipe_id": r.get("recipe_id"),
                "recipe_title": _scrub_item_label(r.get("recipe_title"), fallback="Home Recipe"),
                "meal_type": r.get("meal_type"),
                "cuisine": r.get("cuisine"),
                "servings": r["servings"],
                "prep_minutes": r.get("prep_minutes"),
                "cook_minutes": r.get("cook_minutes"),
                "ingredients_used": _sanitize_cooking_ingredients(
                    r.get("ingredients_used"), registry
                ),
                "substitutions": [],
                "recipe_snapshot": None,
                "cooked_at": r["cooked_at"],
                "rating": r.get("rating"),
                "feedback_tags": list(r.get("feedback_tags") or []),
                "feedback_comment": None,
                "would_make_again": r.get("would_make_again"),
                "feedback_updated_at": r.get("feedback_updated_at"),
            }
        )
    dataset["meal_history"] = demo_meal_history

    # 16. recipe_feedback
    demo_recipe_fb: list[dict[str, Any]] = []
    for r in source_rows.get("recipe_feedback", []):
        if r.get("household_id") != source_household_id:
            continue
        demo_recipe_fb.append(
            {
                "id": registry.register("recipe_feedback", r["id"]),
                "household_id": DEMO_HOUSEHOLD_ID,
                "recipe_id": r["recipe_id"],
                "recipe_title": _scrub_item_label(r.get("recipe_title"), fallback="Home Recipe"),
                "action": r["action"],
                "created_at": r["created_at"],
            }
        )
    dataset["recipe_feedback"] = demo_recipe_fb

    # 17. Clean static ready 8-recipe daily meal plan (validated via validate_recipe_quality)
    plan_row, slot_rows = build_static_demo_daily_plan(
        demo_household_id=DEMO_HOUSEHOLD_ID,
        raw_inventory_payload=raw_inventory_payload,
        plan_date=plan_date,
        now_utc=ref_now,
    )
    dataset["daily_meal_plans"] = [plan_row]
    dataset["daily_meal_plan_recipes"] = slot_rows

    # 18. Supplementary regex PII assertion scan
    pii_violations = scan_dataset_for_pii(dataset)

    return DemoBuildReport(
        demo_user_id=DEMO_USER_ID,
        demo_household_id=DEMO_HOUSEHOLD_ID,
        source_household_id=source_household_id,
        dataset=dataset,
        table_counts={k: len(v) for k, v in dataset.items()},
        sanitization_summary=sanitization_stats,
        pii_violations=pii_violations,
    )
