import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.core.money import decimal_inr_to_paise
from app.core.units import normalize_to_base_unit, resolve_purchase_measurement
from app.modules.inventory.models import CanonicalItem, InventoryLot, ItemAlias, StockLedger
from app.modules.purchases.models import FoodConsumption, Purchase, PurchaseItem
from app.modules.purchases.schemas import (
    ConfirmPurchaseRequest,
    ConfirmPurchaseResponse,
    CreatedLotSummary,
    CreatePurchaseRequest,
    FoodConsumptionOut,
    PurchaseIngredientMappingOut,
    PurchaseOut,
    UpdatePurchaseRequest,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "item"


def _normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9\s]+", " ", value.strip().lower()).strip()


from dataclasses import dataclass

DEFAULT_TOTAL_RECONCILIATION_TOLERANCE_PAISE = 200  # ₹2.00


@dataclass(frozen=True)
class OcrTotalReconciliationResult:
    """Result of reconciling OCR-extracted financial summary against line items."""

    total_amount_paise: int
    needs_review: bool
    review_reason: str | None
    explicit_total_paise: int | None
    calculated_total_paise: int
    discrepancy_paise: int | None
    issue_code: str | None = None
    issue_message: str | None = None


def reconcile_ocr_purchase_total(
    *,
    explicit_total_paise: int | None,
    subtotal_paise: int | None,
    line_items_sum_paise: int,
    discount_paise: int = 0,
    additional_charges_paise: int = 0,
    tax_paise: int = 0,
    rounding_paise: int = 0,
    has_incomplete_extraction: bool = False,
    tolerance_paise: int = DEFAULT_TOTAL_RECONCILIATION_TOLERANCE_PAISE,
) -> OcrTotalReconciliationResult:
    """Reconcile OCR financial fields without ever using abs() or silently clamping.

    Rules:
    1. Explicit negative grand total (refund/credit note) -> raises 422 REFUND_OR_CREDIT_RECEIPT.
    2. Explicit non-negative grand total -> preferred; flags review if discrepancy > tolerance.
    3. No explicit grand total + non-negative computed total -> used; flags review if subtotal
       disagrees with line_items_sum by > tolerance.
    4. No explicit grand total + negative computed total (missed items / misclassified discount) ->
       preserves draft with total_amount_paise=0 explicitly flagged as needs_review so the user
       keeps extracted items and must enter/confirm the printed total before confirmation.
    """
    calculated_total = (
        line_items_sum_paise
        - discount_paise
        + additional_charges_paise
        + tax_paise
        + rounding_paise
    )

    if explicit_total_paise is not None:
        explicit_val = int(explicit_total_paise)
        if explicit_val < 0:
            rupees = explicit_val / 100.0
            raise AppError(
                status_code=422,
                code="REFUND_OR_CREDIT_RECEIPT",
                message=(
                    f"Refund or credit-note receipt detected (printed total ₹{rupees:.2f}). "
                    "Negative refund totals cannot be saved as a purchase."
                ),
                details={"explicit_total_paise": explicit_val},
            )
        discrepancy = abs(explicit_val - calculated_total)
        if discrepancy > tolerance_paise:
            return OcrTotalReconciliationResult(
                total_amount_paise=explicit_val,
                needs_review=True,
                review_reason="reconciliation_mismatch",
                explicit_total_paise=explicit_val,
                calculated_total_paise=calculated_total,
                discrepancy_paise=discrepancy,
                issue_code="TOTAL_RECONCILIATION_MISMATCH",
                issue_message=(
                    f"Printed total (₹{explicit_val / 100:.2f}) disagrees with calculated "
                    f"items/discounts/taxes (₹{calculated_total / 100:.2f}) by "
                    f"₹{discrepancy / 100:.2f}. Please review before confirming."
                ),
            )
        return OcrTotalReconciliationResult(
            total_amount_paise=explicit_val,
            needs_review=False,
            review_reason=None,
            explicit_total_paise=explicit_val,
            calculated_total_paise=calculated_total,
            discrepancy_paise=discrepancy,
        )

    if has_incomplete_extraction:
        return OcrTotalReconciliationResult(
            total_amount_paise=0,
            needs_review=True,
            review_reason="incomplete_extraction",
            explicit_total_paise=None,
            calculated_total_paise=calculated_total,
            discrepancy_paise=None,
            issue_code="TOTAL_NEEDS_REVIEW",
            issue_message=(
                "Receipt extraction was incomplete. Please enter the printed purchase total "
                "before confirming."
            ),
        )

    if calculated_total < 0:
        return OcrTotalReconciliationResult(
            total_amount_paise=0,
            needs_review=True,
            review_reason="negative_computed_total",
            explicit_total_paise=None,
            calculated_total_paise=calculated_total,
            discrepancy_paise=None,
            issue_code="TOTAL_NEEDS_REVIEW",
            issue_message=(
                f"Calculated receipt total was negative (₹{calculated_total / 100:.2f}), "
                "likely due to missed line items or a misclassified discount. "
                "Extracted items were preserved — please review and enter the printed total "
                "before confirming."
            ),
        )

    if subtotal_paise is not None:
        sub_diff = abs(int(subtotal_paise) - line_items_sum_paise)
        if sub_diff > tolerance_paise:
            return OcrTotalReconciliationResult(
                total_amount_paise=calculated_total,
                needs_review=True,
                review_reason="subtotal_mismatch",
                explicit_total_paise=None,
                calculated_total_paise=calculated_total,
                discrepancy_paise=sub_diff,
                issue_code="SUBTOTAL_LINE_MISMATCH",
                issue_message=(
                    f"Receipt subtotal (₹{int(subtotal_paise) / 100:.2f}) disagrees with "
                    f"extracted line items (₹{line_items_sum_paise / 100:.2f}). "
                    "Please verify the total before confirming."
                ),
            )

    return OcrTotalReconciliationResult(
        total_amount_paise=calculated_total,
        needs_review=False,
        review_reason=None,
        explicit_total_paise=None,
        calculated_total_paise=calculated_total,
        discrepancy_paise=0,
    )


def _is_total_review_required(purchase: Purchase) -> bool:
    """Explicit persisted review state check (never relies solely on total_amount_paise == 0)."""
    payload = purchase.raw_ocr_payload if isinstance(purchase.raw_ocr_payload, dict) else {}
    total_review = payload.get("total_review")
    if isinstance(total_review, dict) and total_review.get("status") == "needs_review":
        return True
    missing = purchase.missing_fields_json if isinstance(purchase.missing_fields_json, list) else []
    return "total_amount" in missing


def _mark_total_review_verified(purchase: Purchase) -> None:
    """Clear persisted total review state only after explicit user total submission."""
    payload = dict(purchase.raw_ocr_payload) if isinstance(purchase.raw_ocr_payload, dict) else {}
    existing_review = payload.get("total_review")
    if isinstance(existing_review, dict):
        payload["total_review"] = {**existing_review, "status": "verified"}
    else:
        payload["total_review"] = {"status": "verified"}
    purchase.raw_ocr_payload = payload
    missing = [
        f
        for f in (purchase.missing_fields_json or [])
        if f != "total_amount"
    ]
    purchase.missing_fields_json = missing


def _validate_non_negative_total_paise(
    computed_total_paise: int,
    *,
    source: str = "receipt",
) -> int:
    """Validate that a purchase total is non-negative before database persistence.

    Never converts negative values via abs() and never silently clamps to zero.
    Raises an actionable 422 AppError so invalid/ambiguous OCR totals never trigger
    a PostgreSQL CheckViolationError (HTTP 500/502).
    """
    if computed_total_paise < 0:
        rupees = computed_total_paise / 100.0
        raise AppError(
            status_code=422,
            code="INVALID_PURCHASE_TOTAL",
            message=(
                f"Could not save {source}: calculated total is negative "
                f"(₹{rupees:.2f}). Please verify the receipt discount, tax, and line items "
                "or enter the purchase manually."
            ),
            details={"calculated_total_paise": computed_total_paise},
        )
    return computed_total_paise


def _get_extract_receipt() -> Any:
    from app.infra.ocr.pipeline import extract_receipt  # type: ignore[import-not-found]

    return extract_receipt


async def _invoke_extract_receipt(
    *,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    raw_text: str | None = None,
) -> dict[str, Any]:
    from app.core.config import get_settings
    from app.modules.experimental_ocr.orchestrator import orchestrate_receipt_extraction

    return await orchestrate_receipt_extraction(
        settings=get_settings(),
        existing_extract_fn=_get_extract_receipt(),
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
        raw_text=raw_text,
    )


_RESTAURANT_MERCHANT_HINTS = (
    "restaurant",
    "bhojanam",
    "dhaba",
    "cafe",
    "kitchen",
    "biryani",
    "bistro",
    "bakery",
    "pizzeria",
    "canteen",
    "tiffin",
    "meals",
    "eatery",
    "hotel",
)
_PREPARED_DISH_HINTS = (
    "phulka",
    "roti",
    "naan",
    "chapati",
    "paratha",
    "kulcha",
    "kadai paneer",
    "chole masala",
    "butter chicken",
    "biryani",
    "thali",
    "dosa",
    "idli",
    "vada",
    "fried rice",
    "manchurian",
    "noodles",
    "pizza",
    "burger",
    "sandwich",
    "curry",
)
_GROCERY_UNIT_HINTS = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kg|g|gm|gms|ml|l|ltr|ltrs|dozen|doz|pack|pkt)\b", re.IGNORECASE
)


def _classify_receipt_and_inventory_eligibility(
    merchant_name: str | None,
    item_names: list[str],
    raw_text: str,
) -> tuple[str, list[bool], list[str]]:
    """Classify receipt category ('restaurant' vs 'grocery') and per-item inventory tracking
    eligibility.
    """
    m_lower = (merchant_name or "").lower()
    raw_lower = (raw_text or "").lower()
    is_grocery_merchant = any(
        h in m_lower
        for h in (
            "mart",
            "fresh",
            "kirana",
            "grocery",
            "supermarket",
            "basket",
            "dairy",
            "vegetable",
            "fruit",
            "bazaar",
            "retail",
            "store",
        )
    )
    is_restaurant = not is_grocery_merchant and (
        any(h in m_lower for h in _RESTAURANT_MERCHANT_HINTS)
        or "table no" in raw_lower
        or "cashier:" in raw_lower
        or "dine in" in raw_lower
        or "pick up" in raw_lower
    )
    dish_hits = sum(
        1 for name in item_names if any(dh in name.lower() for dh in _PREPARED_DISH_HINTS)
    )
    if is_grocery_merchant:
        receipt_category = "grocery"
    elif dish_hits >= 1 and (is_restaurant or (item_names and dish_hits == len(item_names))):
        receipt_category = "restaurant"
    elif "pharmacy" in m_lower or "medical" in m_lower:
        receipt_category = "pharmacy"
    else:
        receipt_category = "grocery"

    eligibility: list[bool] = []
    cls_warnings: list[str] = []
    for name in item_names:
        n_lower = name.lower()
        is_prepared_dish = any(dh in n_lower for dh in _PREPARED_DISH_HINTS)
        has_grocery_pack = bool(_GROCERY_UNIT_HINTS.search(name))
        if receipt_category == "restaurant" and not has_grocery_pack:
            eligibility.append(False)
            if not is_restaurant:
                cls_warnings.append(
                    f"'{name}' was marked budget-only; enable 'Add to Kitchen Inventory' "
                    "if it is a pantry item."
                )
        elif is_prepared_dish and not has_grocery_pack:
            eligibility.append(False)
            cls_warnings.append(
                f"'{name}' was marked budget-only; enable 'Add to Kitchen Inventory' if "
                "it is a pantry item."
            )
        else:
            eligibility.append(True)

    # Derive 3-way receipt classification ('grocery' | 'restaurant' | 'mixed') from items
    if eligibility:
        if all(eligibility) and receipt_category != "pharmacy":
            receipt_category = "grocery"
        elif not any(eligibility):
            receipt_category = "restaurant"
        elif any(eligibility) and not all(eligibility):
            receipt_category = "mixed"
    return receipt_category, eligibility, cls_warnings


def _extract_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


async def _resolve_canonical_item_id(
    db: AsyncSession,
    household_id: UUID,
    raw_name: str,
    *,
    unit_price_paise: int | None = None,
    line_total_paise: int | None = None,
    quantity: float | None = None,
    matched_canonical_name: str | None = None,
) -> tuple[UUID | None, str]:
    from app.modules.catalog.canonical_resolver import clean_canonical_candidate_name

    candidates: list[str] = [raw_name]
    cleaned = clean_canonical_candidate_name(
        raw_name,
        unit_price_paise=unit_price_paise,
        line_total_paise=line_total_paise,
        quantity=quantity,
    )
    if cleaned and cleaned.lower() != (raw_name or "").strip().lower():
        candidates.append(cleaned)
    if matched_canonical_name and matched_canonical_name not in candidates:
        candidates.append(matched_canonical_name)

    for cand in candidates:
        norm = _normalize_alias(cand)
        if not norm:
            continue

        alias_stmt = (
            select(ItemAlias)
            .join(CanonicalItem, ItemAlias.canonical_item_id == CanonicalItem.id)
            .where(
                ItemAlias.normalized_alias == norm,
                CanonicalItem.is_active.is_(True),
                or_(ItemAlias.household_id == household_id, ItemAlias.household_id.is_(None)),
            )
            .order_by(ItemAlias.household_id.is_(None).asc())
        )
        alias = (await db.execute(alias_stmt)).scalars().first()
        if alias is not None:
            return alias.canonical_item_id, "exact_alias"

        slug = _slugify(cand)
        item_stmt = (
            select(CanonicalItem)
            .where(
                CanonicalItem.slug == slug,
                CanonicalItem.is_active.is_(True),
                or_(
                    CanonicalItem.household_id == household_id, CanonicalItem.household_id.is_(None)
                ),
            )
            .order_by(CanonicalItem.household_id.is_(None).asc())
        )
        item = (await db.execute(item_stmt)).scalars().first()
        if item is not None:
            return item.id, "exact_alias"

    return None, "unmatched"


async def create_ocr_draft(
    db: AsyncSession,
    *,
    household_id: UUID,
    user_id: UUID,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    raw_text: str | None = None,
    merchant_name_override: str | None = None,
    purchased_on_override: date | None = None,
) -> PurchaseOut:
    """Run OCR extraction and persist a draft purchase WITHOUT mutating inventory or ledger."""
    from app.infra.ocr.schemas import parse_flexible_date

    file_sha256 = hashlib.sha256(file_bytes).hexdigest() if file_bytes else None
    if file_sha256:
        cutoff = _utcnow() - timedelta(seconds=60)
        recent_stmt = (
            select(Purchase)
            .options(selectinload(Purchase.items))
            .where(
                Purchase.household_id == household_id,
                Purchase.status == "draft",
                Purchase.source_type == "ocr",
                Purchase.created_at >= cutoff,
            )
            .order_by(Purchase.created_at.desc())
            .limit(5)
        )
        recent_drafts = (await db.execute(recent_stmt)).scalars().all()
        for existing in recent_drafts:
            payload = existing.raw_ocr_payload or {}
            if isinstance(payload, dict) and payload.get("file_sha256") == file_sha256:
                return _serialize_purchase(existing)

    ocr_result = await _invoke_extract_receipt(
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
        raw_text=raw_text,
    )

    merchant_name = merchant_name_override or ocr_result.get("merchant_name")
    raw_purchased_on = (
        purchased_on_override or ocr_result.get("purchased_on") or ocr_result.get("receipt_date")
    )
    if isinstance(raw_purchased_on, str):
        purchased_on = parse_flexible_date(raw_purchased_on) or _utcnow().date()
    elif isinstance(raw_purchased_on, date):
        purchased_on = raw_purchased_on
    else:
        purchased_on = _utcnow().date()

    raw_items = ocr_result.get("items") or ocr_result.get("lines") or []
    raw_payload = dict(ocr_result.get("raw_payload") or ocr_result)
    if file_sha256:
        raw_payload["file_sha256"] = file_sha256

    tax_breakdown = ocr_result.get("tax_breakdown") or {}
    cgst_paise = int(_extract_attr(tax_breakdown, "cgst_paise") or 0)
    sgst_paise = int(_extract_attr(tax_breakdown, "sgst_paise") or 0)
    igst_paise = int(_extract_attr(tax_breakdown, "igst_paise") or 0)
    cess_paise = int(ocr_result.get("cess_paise") or 0)
    other_tax_paise = int(_extract_attr(tax_breakdown, "other_tax_paise") or 0) + cess_paise
    explicit_tax_paise = ocr_result.get("tax_paise")
    tax_paise = (
        int(explicit_tax_paise) + cess_paise
        if explicit_tax_paise is not None
        else (cgst_paise + sgst_paise + igst_paise + other_tax_paise)
    )
    subtotal_paise = ocr_result.get("subtotal_paise")
    explicit_discount = ocr_result.get("discount_paise")
    if explicit_discount is not None:
        discount_paise = int(explicit_discount)
    else:
        discount_paise = int(ocr_result.get("loyalty_discount_paise") or 0)
    explicit_charges = ocr_result.get("additional_charges_paise")
    if explicit_charges is not None:
        additional_charges_paise = int(explicit_charges)
    else:
        additional_charges_paise = int(ocr_result.get("handling_charges_paise") or 0) + int(
            ocr_result.get("delivery_charges_paise") or 0
        )
    rounding_paise = int(ocr_result.get("rounding_paise") or 0) + int(
        ocr_result.get("goodwill_paise") or 0
    )
    document_type = str(ocr_result.get("document_type") or "unknown")[:32]
    order_id = str(ocr_result["order_id"])[:64] if ocr_result.get("order_id") else None
    missing_fields = [str(f) for f in (ocr_result.get("missing_fields") or []) if f]
    attachments = (
        [
            {
                "filename": filename,
                "content_type": content_type,
                "document_type": document_type,
                "sha256": file_sha256,
            }
        ]
        if filename
        else []
    )

    item_names_for_cls = [
        str(
            _extract_attr(ri, "display_name")
            or _extract_attr(ri, "name")
            or _extract_attr(ri, "raw_text")
            or ""
        )
        for ri in raw_items
    ]
    receipt_category, item_track_flags, cls_warnings = _classify_receipt_and_inventory_eligibility(
        merchant_name, item_names_for_cls, str(ocr_result.get("raw_text") or "")
    )
    if cls_warnings:
        existing_warnings = list(raw_payload.get("warnings") or [])
        for w in cls_warnings:
            if w not in existing_warnings:
                existing_warnings.append(w)
        raw_payload["warnings"] = existing_warnings

    purchase = Purchase(
        household_id=household_id,
        created_by=user_id,
        status="draft",
        source_type="ocr",
        document_type=document_type,
        ocr_adapter=str(
            ocr_result.get("adapter_used")
            or ocr_result.get("adapter")
            or ocr_result.get("ocr_adapter")
            or "ocr"
        ),
        merchant_name=merchant_name,
        receipt_number=(
            str(ocr_result["receipt_number"])[:64] if ocr_result.get("receipt_number") else None
        ),
        order_id=order_id,
        purchased_on=purchased_on,
        purchased_time=(
            str(ocr_result["receipt_time"])[:32] if ocr_result.get("receipt_time") else None
        ),
        currency_code=str(ocr_result.get("currency") or "INR")[:3].upper(),
        receipt_category=receipt_category,
        payment_method=(
            str(ocr_result["payment_method"])[:32] if ocr_result.get("payment_method") else None
        ),
        subtotal_paise=int(subtotal_paise) if subtotal_paise is not None else None,
        discount_paise=discount_paise,
        additional_charges_paise=additional_charges_paise,
        cgst_paise=cgst_paise,
        sgst_paise=sgst_paise,
        igst_paise=igst_paise,
        other_tax_paise=other_tax_paise,
        tax_paise=tax_paise,
        rounding_paise=rounding_paise,
        total_amount_paise=0,
        raw_ocr_payload=raw_payload,
        missing_fields_json=missing_fields,
        attachments_json=attachments,
    )
    db.add(purchase)
    await db.flush()

    created_items = await _build_ocr_purchase_items(
        db,
        purchase=purchase,
        household_id=household_id,
        raw_items=raw_items,
        item_track_flags=item_track_flags,
        start_index=0,
    )
    total_paise = sum(item.line_total_paise for item in created_items)
    has_incomplete_extraction = any(
        isinstance(issue, dict) and issue.get("code") == "INCOMPLETE_EXTRACTION"
        for issue in (ocr_result.get("validation_issues") or [])
    )

    subtotal_is_synthetic = False
    if purchase.subtotal_paise is None and total_paise > 0 and not has_incomplete_extraction:
        purchase.subtotal_paise = total_paise
        subtotal_is_synthetic = True

    explicit_total = (
        ocr_result.get("total_amount_paise")
        if ocr_result.get("total_amount_paise") is not None
        else ocr_result.get("grand_total_paise")
    )
    try:
        reconciliation = reconcile_ocr_purchase_total(
            explicit_total_paise=int(explicit_total) if explicit_total is not None else None,
            subtotal_paise=purchase.subtotal_paise,
            line_items_sum_paise=total_paise,
            discount_paise=discount_paise,
            additional_charges_paise=additional_charges_paise,
            tax_paise=tax_paise,
            rounding_paise=rounding_paise,
            has_incomplete_extraction=has_incomplete_extraction,
        )
    except AppError:
        await db.rollback()
        raise

    purchase.total_amount_paise = _validate_non_negative_total_paise(
        reconciliation.total_amount_paise, source="receipt"
    )

    updated_payload = (
        dict(purchase.raw_ocr_payload) if isinstance(purchase.raw_ocr_payload, dict) else {}
    )
    updated_payload["total_review"] = {
        "status": "needs_review" if reconciliation.needs_review else "verified",
        "reason": reconciliation.review_reason,
        "explicit_total_paise": reconciliation.explicit_total_paise,
        "calculated_total_paise": reconciliation.calculated_total_paise,
        "discrepancy_paise": reconciliation.discrepancy_paise,
        "subtotal_is_synthetic": subtotal_is_synthetic,
    }
    if reconciliation.issue_code and reconciliation.issue_message:
        existing_issues = list(updated_payload.get("validation_issues") or [])
        existing_issues.append(
            {
                "code": reconciliation.issue_code,
                "severity": "warning",
                "message": reconciliation.issue_message,
                "field": "total_amount",
                "calculated_total_paise": reconciliation.calculated_total_paise,
                "explicit_total_paise": reconciliation.explicit_total_paise,
            }
        )
        updated_payload["validation_issues"] = existing_issues
    purchase.raw_ocr_payload = updated_payload

    if reconciliation.needs_review:
        cur_missing = list(purchase.missing_fields_json or [])
        if "total_amount" not in cur_missing:
            cur_missing.append("total_amount")
        purchase.missing_fields_json = cur_missing

    await db.commit()
    return await get_purchase(db, household_id, purchase.id)


async def _build_ocr_purchase_items(
    db: AsyncSession,
    *,
    purchase: Purchase,
    household_id: UUID,
    raw_items: list,
    item_track_flags: list[bool],
    start_index: int = 0,
) -> list[PurchaseItem]:
    from app.modules.catalog.classifier import classify_with_household_db

    created: list[PurchaseItem] = []
    for idx, raw_item in enumerate(raw_items):
        display_name = str(
            _extract_attr(raw_item, "display_name")
            or _extract_attr(raw_item, "name")
            or _extract_attr(raw_item, "raw_text")
            or f"Item {start_index + idx + 1}"
        ).strip()
        item_raw_text = str(_extract_attr(raw_item, "raw_text") or display_name)
        cls_res = await classify_with_household_db(
            db,
            household_id=household_id,
            raw_name=display_name,
            receipt_category=purchase.receipt_category,
            merchant_name=purchase.merchant_name,
        )
        raw_track_attr = _extract_attr(raw_item, "track_inventory")
        track_flag = (
            False
            if (idx < len(item_track_flags) and not item_track_flags[idx])
            or cls_res.needs_review
            or not cls_res.pantry_eligible
            else (bool(raw_track_attr) if raw_track_attr is not None else True)
        )
        explicit_cat = _extract_attr(raw_item, "category")
        if cls_res.classification_status == "non_food":
            category = "non_food"
        elif cls_res.classification_status == "prepared_meal":
            category = str(explicit_cat or "prepared_food").strip().lower()
        else:
            category = str(explicit_cat or cls_res.category).strip().lower()

        norm_qty_attr = _extract_attr(raw_item, "normalized_qty")
        base_unit_attr = _extract_attr(raw_item, "base_unit")
        pack_size_attr = _extract_attr(raw_item, "pack_size")
        if norm_qty_attr is not None and base_unit_attr in {"g", "ml", "pcs"}:
            raw_qty = norm_qty_attr
            raw_unit = str(base_unit_attr)
            try:
                norm_qty, base_unit = resolve_purchase_measurement(
                    raw_qty,
                    raw_unit,
                    str(pack_size_attr) if pack_size_attr else None,
                )
            except ValueError:
                norm_qty, base_unit = Decimal("1.000"), "pcs"
        else:
            raw_qty = _extract_attr(raw_item, "qty") or _extract_attr(raw_item, "quantity") or 1
            raw_unit = str(_extract_attr(raw_item, "unit") or base_unit_attr or "pcs")
            try:
                norm_qty, base_unit = resolve_purchase_measurement(
                    raw_qty,
                    raw_unit,
                    str(pack_size_attr) if pack_size_attr else None,
                )
            except ValueError:
                norm_qty, base_unit = Decimal("1.000"), "pcs"

        if norm_qty <= Decimal("0"):
            norm_qty = Decimal("1.000")

        line_total_paise = _extract_attr(raw_item, "line_total_paise")
        if line_total_paise is None:
            line_total_paise = _extract_attr(raw_item, "total_paise")
        if line_total_paise is None:
            line_total_inr = _extract_attr(raw_item, "line_total_inr") or _extract_attr(
                raw_item, "price"
            )
            line_total_paise = (
                decimal_inr_to_paise(line_total_inr) if line_total_inr is not None else 0
            )
        line_total_paise = max(0, int(line_total_paise))

        explicit_unit_price_paise = _extract_attr(raw_item, "unit_price_paise")
        unit_price_paise = explicit_unit_price_paise
        if unit_price_paise is None:
            qty_float = float(norm_qty)
            unit_price_paise = (
                round(line_total_paise / qty_float) if qty_float > 0 else line_total_paise
            )

        raw_exp = _extract_attr(raw_item, "expires_on")
        expires_on = date.fromisoformat(raw_exp) if isinstance(raw_exp, str) else raw_exp

        high_conf_canonical = (
            cls_res.matched_canonical_name
            if cls_res.matched_canonical_name
            and cls_res.confidence >= 0.85
            and cls_res.matching_method
            in {"user_alias", "exact_canonical", "exact_alias", "normalized_token"}
            else None
        )
        canonical_id, confidence = await _resolve_canonical_item_id(
            db,
            household_id,
            display_name,
            unit_price_paise=unit_price_paise,
            line_total_paise=line_total_paise,
            quantity=float(norm_qty),
            matched_canonical_name=high_conf_canonical,
        )
        item_conf = _extract_attr(raw_item, "confidence")
        item_warnings = _extract_attr(raw_item, "warnings")

        line_tax_paise = _extract_attr(raw_item, "tax_paise")
        if line_tax_paise is None:
            line_tax_paise = sum(
                int(_extract_attr(raw_item, k) or 0)
                for k in ("cgst_paise", "sgst_paise", "igst_paise", "cess_paise")
            )

        from app.modules.catalog.measurement_service import validate_receipt_quantity_against_price

        qty_price_unit_paise = (
            max(0, int(explicit_unit_price_paise))
            if explicit_unit_price_paise is not None
            else 0
        )
        qty_price_check = validate_receipt_quantity_against_price(
            normalized_qty=norm_qty,
            base_unit=base_unit,
            raw_unit=raw_unit,
            unit_price_paise=qty_price_unit_paise,
            line_total_paise=line_total_paise,
        )

        item_warnings = _extract_attr(raw_item, "warnings")
        merged_warnings = list(item_warnings) if isinstance(item_warnings, list) else []
        if cls_res.review_reason and cls_res.review_reason not in merged_warnings:
            merged_warnings.append(cls_res.review_reason)
        if not qty_price_check.is_valid and qty_price_check.message:
            merged_warnings.append(f"quantity_price_mismatch: {qty_price_check.message}")
            track_flag = False
        item_conf = _extract_attr(raw_item, "confidence")

        from app.modules.purchases.draft_resolution import evaluate_draft_item_resolution

        draft_res = await evaluate_draft_item_resolution(
            db,
            household_id=household_id,
            raw_text=item_raw_text,
            display_name=display_name,
            canonical_item_id=canonical_id,
            normalized_qty=norm_qty,
            base_unit=base_unit,
            raw_unit=raw_unit,
            raw_qty=Decimal(str(raw_qty)) if raw_qty is not None else None,
            unit_price_paise=qty_price_unit_paise,
            line_total_paise=line_total_paise,
            discount_paise=max(0, int(_extract_attr(raw_item, "discount_paise") or 0)),
            tax_paise=max(0, int(line_tax_paise or 0)),
            track_inventory=track_flag
            or (
                cls_res.classification_status not in {"non_food", "prepared_meal"}
                and not (idx < len(item_track_flags) and not item_track_flags[idx])
            ),
            classification_status=cls_res.classification_status,
        )
        effective_display_name = display_name
        if (
            draft_res.ingredient_confidence_tier == "HIGH"
            and qty_price_check.is_valid
            and cls_res.classification_status not in {"non_food", "prepared_meal"}
            and not (idx < len(item_track_flags) and not item_track_flags[idx])
        ):
            track_flag = True

        p_item = PurchaseItem(
            purchase_id=purchase.id,
            household_id=household_id,
            line_index=start_index + idx,
            raw_text=item_raw_text,
            canonical_item_id=canonical_id,
            display_name=effective_display_name,
            normalized_name=_extract_attr(raw_item, "normalized_name")
            or cls_res.normalized_name
            or effective_display_name.lower(),
            brand=_extract_attr(raw_item, "brand") or cls_res.extracted_brand,
            variant=_extract_attr(raw_item, "variant") or draft_res.standardized_name,
            pack_size=_extract_attr(raw_item, "pack_size") or cls_res.extracted_pack_size,
            category=category,
            secondary_category=_extract_attr(raw_item, "secondary_category")
            or cls_res.secondary_category,
            match_confidence=confidence,
            matching_confidence=Decimal(str(round(cls_res.matching_confidence, 3))),
            eligibility_reason=cls_res.eligibility_reason,
            unit_price_provenance=_extract_attr(raw_item, "unit_price_provenance"),
            normalized_qty=norm_qty,
            base_unit=base_unit,
            unit_price_paise=max(0, int(unit_price_paise)),
            line_total_paise=line_total_paise,
            discount_paise=max(0, int(_extract_attr(raw_item, "discount_paise") or 0)),
            tax_paise=max(0, int(line_tax_paise or 0)),
            tax_rate_bps=_extract_attr(raw_item, "tax_rate_bps"),
            confidence=Decimal(str(round(float(item_conf), 2))) if item_conf is not None else None,
            warnings_json=merged_warnings or None,
            track_inventory=track_flag,
            food_state=cls_res.food_state,
            budget_treatment=(
                "pantry_inventory"
                if track_flag and cls_res.budget_treatment == "requires_user_review"
                else cls_res.budget_treatment
            ),
            classification_confidence=Decimal(str(round(cls_res.confidence, 3))),
            matching_method=cls_res.matching_method,
            resolution_score=draft_res.raw_confidence_decimal,
            resolution_metadata_json=draft_res.to_metadata_dict(),
            classification_source=cls_res.source,
            expires_on=expires_on,
        )
        db.add(p_item)
        created.append(p_item)
    return created


async def merge_ocr_into_draft(
    db: AsyncSession,
    *,
    household_id: UUID,
    purchase_id: UUID,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    raw_text: str | None = None,
) -> PurchaseOut:
    """Attach another receipt/invoice document to an existing draft and merge without duplicating
    items or totals.
    """
    stmt = (
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(Purchase.id == purchase_id, Purchase.household_id == household_id)
    )
    purchase = (await db.execute(stmt)).scalar_one_or_none()
    if purchase is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Purchase draft not found.")
    if purchase.status != "draft":
        raise AppError(
            status_code=409,
            code="INVALID_STATE",
            message="Only draft purchases can receive additional attachments.",
        )

    file_sha256 = hashlib.sha256(file_bytes).hexdigest() if file_bytes else None
    existing_attachments = list(purchase.attachments_json or [])
    if file_sha256 and any(
        att.get("sha256") == file_sha256 for att in existing_attachments if isinstance(att, dict)
    ):
        return await get_purchase(db, household_id, purchase.id)

    ocr_result = await _invoke_extract_receipt(
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
        raw_text=raw_text,
    )
    doc_type = str(ocr_result.get("document_type") or "unknown")[:32]
    if filename:
        existing_attachments.append(
            {
                "filename": filename,
                "content_type": content_type,
                "document_type": doc_type,
                "sha256": file_sha256,
            }
        )
        purchase.attachments_json = existing_attachments

    if not purchase.merchant_name and ocr_result.get("merchant_name"):
        purchase.merchant_name = str(ocr_result["merchant_name"])
    if not purchase.receipt_number and ocr_result.get("receipt_number"):
        purchase.receipt_number = str(ocr_result["receipt_number"])[:64]
    if not purchase.order_id and ocr_result.get("order_id"):
        purchase.order_id = str(ocr_result["order_id"])[:64]
    if not purchase.payment_method and ocr_result.get("payment_method"):
        purchase.payment_method = str(ocr_result["payment_method"])[:32]

    raw_items = ocr_result.get("items") or ocr_result.get("lines") or []
    if raw_items:
        from collections import Counter

        existing_sig_counts: Counter[tuple[str, str, int, int, int]] = Counter(
            (
                (it.normalized_name or it.display_name).strip().lower(),
                f"{float(getattr(it, 'quantity', getattr(it, 'raw_qty', 1)) or 1):.3f}",
                int(it.unit_price_paise or 0),
                int(it.discount_paise or 0),
                int(it.line_total_paise or 0),
            )
            for it in purchase.items
        )
        deduped_items = []
        for ri in raw_items:
            d_name = str(
                _extract_attr(ri, "display_name")
                or _extract_attr(ri, "name")
                or _extract_attr(ri, "raw_text")
                or ""
            ).strip()
            r_qty = f"{float(_extract_attr(ri, 'qty') or _extract_attr(ri, 'quantity') or 1):.3f}"
            u_price = int(_extract_attr(ri, "unit_price_paise") or 0)
            i_disc = int(_extract_attr(ri, "discount_paise") or 0)
            l_tot = int(
                _extract_attr(ri, "line_total_paise") or _extract_attr(ri, "total_paise") or 0
            )
            sig = (d_name.lower(), r_qty, u_price, i_disc, l_tot)
            if existing_sig_counts[sig] > 0:
                # Consume at most 1-for-1 cross-page overlap; never collapse same-page duplicates
                existing_sig_counts[sig] -= 1
                continue
            deduped_items.append(ri)

        if deduped_items:
            names = [
                str(_extract_attr(ri, "display_name") or _extract_attr(ri, "name") or "")
                for ri in deduped_items
            ]
            rec_cat, track_flags, _ = _classify_receipt_and_inventory_eligibility(
                purchase.merchant_name, names, str(ocr_result.get("raw_text") or "")
            )
            if not purchase.items:
                purchase.receipt_category = rec_cat
                purchase.document_type = doc_type
            added_items = await _build_ocr_purchase_items(
                db,
                purchase=purchase,
                household_id=household_id,
                raw_items=deduped_items,
                item_track_flags=track_flags,
                start_index=len(purchase.items),
            )
            missing = [f for f in (purchase.missing_fields_json or []) if f != "line_items"]
            purchase.missing_fields_json = missing
        else:
            added_items = []
    else:
        added_items = []

    prev_items_sum = sum(it.line_total_paise for it in purchase.items)
    combined_items_sum = prev_items_sum + sum(it.line_total_paise for it in added_items)

    existing_payload = (
        dict(purchase.raw_ocr_payload) if isinstance(purchase.raw_ocr_payload, dict) else {}
    )
    incoming_warnings = [str(w) for w in (ocr_result.get("warnings") or []) if w]
    if incoming_warnings:
        merged_warnings = list(
            dict.fromkeys([*(existing_payload.get("warnings") or []), *incoming_warnings])
        )
        existing_payload["warnings"] = merged_warnings
    has_ocr_page_failure = any(
        ("OCR_TIMEOUT" in w or "OCR_ENGINE_ERROR" in w) for w in incoming_warnings
    )
    if has_ocr_page_failure:
        existing_payload["ocr_partial_failure"] = True
        cur_missing = list(purchase.missing_fields_json or [])
        if "ocr_partial_failure" not in cur_missing:
            cur_missing.append("ocr_partial_failure")
        purchase.missing_fields_json = cur_missing
    existing_review = existing_payload.get("total_review")
    subtotal_is_synthetic = bool(
        isinstance(existing_review, dict) and existing_review.get("subtotal_is_synthetic", False)
    )

    # Fill missing financial summary fields without overwriting genuine merchant subtotals
    if ocr_result.get("subtotal_paise") is not None:
        if purchase.subtotal_paise is None or subtotal_is_synthetic:
            purchase.subtotal_paise = int(ocr_result["subtotal_paise"])
            subtotal_is_synthetic = False
    elif added_items and subtotal_is_synthetic:
        purchase.subtotal_paise = combined_items_sum

    if purchase.discount_paise == 0 and ocr_result.get("discount_paise"):
        purchase.discount_paise = int(ocr_result["discount_paise"])
    if purchase.tax_paise == 0 and ocr_result.get("tax_paise"):
        purchase.tax_paise = int(ocr_result["tax_paise"])
    if purchase.additional_charges_paise == 0 and ocr_result.get("additional_charges_paise"):
        purchase.additional_charges_paise = int(ocr_result["additional_charges_paise"])
    if purchase.rounding_paise == 0 and ocr_result.get("rounding_paise"):
        purchase.rounding_paise = int(ocr_result["rounding_paise"])

    was_review_required = _is_total_review_required(purchase)
    existing_explicit = (
        existing_review.get("explicit_total_paise")
        if isinstance(existing_review, dict)
        else None
    )
    incoming_explicit_raw = (
        ocr_result.get("total_amount_paise")
        if ocr_result.get("total_amount_paise") is not None
        else ocr_result.get("grand_total_paise")
    )
    incoming_explicit = int(incoming_explicit_raw) if incoming_explicit_raw is not None else None
    if incoming_explicit is not None and incoming_explicit < 0:
        effective_explicit: int | None = incoming_explicit
    elif existing_explicit is not None:
        effective_explicit = int(existing_explicit)
    else:
        effective_explicit = incoming_explicit

    try:
        reconciliation = reconcile_ocr_purchase_total(
            explicit_total_paise=effective_explicit,
            subtotal_paise=purchase.subtotal_paise,
            line_items_sum_paise=combined_items_sum,
            discount_paise=purchase.discount_paise,
            additional_charges_paise=purchase.additional_charges_paise,
            tax_paise=purchase.tax_paise,
            rounding_paise=purchase.rounding_paise,
        )
    except AppError:
        await db.rollback()
        raise

    purchase.total_amount_paise = _validate_non_negative_total_paise(
        reconciliation.total_amount_paise, source="receipt"
    )
    effective_needs_review = was_review_required or reconciliation.needs_review
    effective_reason = reconciliation.review_reason or (
        existing_review.get("reason") if isinstance(existing_review, dict) else None
    )
    existing_payload["total_review"] = {
        "status": "needs_review" if effective_needs_review else "verified",
        "reason": effective_reason,
        "explicit_total_paise": reconciliation.explicit_total_paise,
        "calculated_total_paise": reconciliation.calculated_total_paise,
        "discrepancy_paise": reconciliation.discrepancy_paise,
        "subtotal_is_synthetic": subtotal_is_synthetic,
    }
    if reconciliation.issue_code and reconciliation.issue_message:
        existing_issues = list(existing_payload.get("validation_issues") or [])
        existing_issues.append(
            {
                "code": reconciliation.issue_code,
                "severity": "warning",
                "message": reconciliation.issue_message,
                "field": "total_amount",
                "calculated_total_paise": reconciliation.calculated_total_paise,
                "explicit_total_paise": reconciliation.explicit_total_paise,
            }
        )
        existing_payload["validation_issues"] = existing_issues
    purchase.raw_ocr_payload = existing_payload

    if effective_needs_review:
        cur_missing = list(purchase.missing_fields_json or [])
        if "total_amount" not in cur_missing:
            cur_missing.append("total_amount")
        purchase.missing_fields_json = cur_missing

    await db.commit()
    return await get_purchase(db, household_id, purchase.id)


async def create_multi_ocr_draft(
    db: AsyncSession,
    *,
    household_id: UUID,
    user_id: UUID,
    files: list[tuple[bytes, str | None, str | None]],
    merchant_name_override: str | None = None,
    purchased_on_override: date | None = None,
) -> PurchaseOut:
    """Create a single draft from multiple uploaded receipt/invoice files."""
    if not files:
        raise AppError(
            status_code=422, code="VALIDATION_ERROR", message="At least one file is required."
        )
    first_bytes, first_name, first_ct = files[0]
    draft = await create_ocr_draft(
        db,
        household_id=household_id,
        user_id=user_id,
        file_bytes=first_bytes,
        filename=first_name,
        content_type=first_ct,
        merchant_name_override=merchant_name_override,
        purchased_on_override=purchased_on_override,
    )
    for extra_bytes, extra_name, extra_ct in files[1:]:
        draft = await merge_ocr_into_draft(
            db,
            household_id=household_id,
            purchase_id=draft.id,
            file_bytes=extra_bytes,
            filename=extra_name,
            content_type=extra_ct,
        )
    return draft


async def create_manual_draft(
    db: AsyncSession,
    *,
    household_id: UUID,
    user_id: UUID,
    payload: CreatePurchaseRequest,
) -> PurchaseOut:
    purchased_on = payload.purchased_on or _utcnow().date()
    tax_paise = (
        payload.tax_paise
        if payload.tax_paise is not None
        else (
            payload.cgst_paise + payload.sgst_paise + payload.igst_paise + payload.other_tax_paise
        )
    )
    purchase = Purchase(
        household_id=household_id,
        created_by=user_id,
        status="draft",
        source_type="manual",
        merchant_name=payload.merchant_name,
        receipt_number=payload.receipt_number,
        purchased_on=purchased_on,
        purchased_time=payload.purchased_time,
        currency_code=payload.currency_code.upper(),
        receipt_category=payload.receipt_category,
        payment_method=payload.payment_method,
        subtotal_paise=payload.subtotal_paise,
        discount_paise=payload.discount_paise,
        additional_charges_paise=payload.additional_charges_paise,
        cgst_paise=payload.cgst_paise,
        sgst_paise=payload.sgst_paise,
        igst_paise=payload.igst_paise,
        other_tax_paise=payload.other_tax_paise,
        tax_paise=tax_paise,
        rounding_paise=payload.rounding_paise,
        total_amount_paise=0,
    )
    db.add(purchase)
    await db.flush()

    total_paise = 0
    for idx, item_in in enumerate(payload.items):
        raw_qty = item_in.normalized_qty if item_in.normalized_qty is not None else item_in.qty
        raw_unit = item_in.base_unit or item_in.unit
        if raw_qty is None or raw_unit is None:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Each purchase item must include qty/normalized_qty and unit/base_unit.",
            )
        try:
            norm_qty, base_unit = resolve_purchase_measurement(
                raw_qty,
                raw_unit,
                item_in.pack_size if item_in.normalized_qty is None else None,
            )
        except ValueError as exc:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message=str(exc),
            ) from exc

        from app.modules.catalog.canonical_resolver import clean_canonical_candidate_name
        from app.modules.catalog.classifier import classify_with_household_db

        cleaned_for_cls = (
            clean_canonical_candidate_name(
                item_in.display_name,
                unit_price_paise=item_in.unit_price_paise,
                line_total_paise=item_in.line_total_paise,
                quantity=float(raw_qty),
            )
            or item_in.display_name
        )

        cls_res = await classify_with_household_db(
            db,
            household_id=household_id,
            raw_name=cleaned_for_cls,
            receipt_category=purchase.receipt_category,
            merchant_name=purchase.merchant_name,
        )
        high_conf_canonical = (
            cls_res.matched_canonical_name
            if cls_res.matched_canonical_name
            and cls_res.confidence >= 0.85
            and cls_res.matching_method
            in {"user_alias", "exact_canonical", "exact_alias", "normalized_token"}
            else None
        )
        canonical_id = item_in.canonical_item_id
        confidence = "user_selected" if canonical_id is not None else "unmatched"
        if canonical_id is None:
            canonical_id, confidence = await _resolve_canonical_item_id(
                db,
                household_id,
                item_in.display_name,
                unit_price_paise=item_in.unit_price_paise,
                line_total_paise=item_in.line_total_paise,
                quantity=float(raw_qty),
                matched_canonical_name=high_conf_canonical,
            )
        from app.modules.catalog.measurement_service import validate_receipt_quantity_against_price

        qty_price_check = validate_receipt_quantity_against_price(
            normalized_qty=norm_qty,
            base_unit=base_unit,
            raw_unit=str(item_in.unit or raw_unit),
            unit_price_paise=item_in.unit_price_paise,
            line_total_paise=item_in.line_total_paise,
        )
        safe_track = (
            bool(item_in.track_inventory)
            and cls_res.pantry_eligible
            and not cls_res.needs_review
            and (qty_price_check.is_valid or item_in.classification_source == "user_override")
        )
        safe_cat = (
            "non_food"
            if cls_res.classification_status == "non_food"
            else item_in.category.strip().lower()
        )
        line_total = item_in.line_total_paise or item_in.unit_price_paise
        from app.modules.purchases.draft_resolution import evaluate_draft_item_resolution

        draft_res = await evaluate_draft_item_resolution(
            db,
            household_id=household_id,
            raw_text=item_in.raw_text or item_in.display_name,
            display_name=item_in.display_name.strip(),
            canonical_item_id=canonical_id,
            normalized_qty=norm_qty,
            base_unit=base_unit,
            raw_unit=str(item_in.unit or raw_unit),
            raw_qty=Decimal(str(raw_qty)),
            unit_price_paise=item_in.unit_price_paise,
            line_total_paise=line_total,
            discount_paise=item_in.discount_paise,
            tax_paise=item_in.tax_paise,
            track_inventory=bool(item_in.track_inventory)
            and cls_res.classification_status not in {"non_food", "prepared_meal"},
            classification_status=cls_res.classification_status,
            explicit_canonical_ingredient_id=item_in.canonical_ingredient_id,
            explicit_variant_id=item_in.ingredient_variant_id,
            user_approved_ingredient=item_in.user_approved_ingredient,
        )
        if (
            draft_res.ingredient_confidence_tier == "HIGH"
            and (qty_price_check.is_valid or item_in.classification_source == "user_override")
            and bool(item_in.track_inventory)
            and cls_res.classification_status not in {"non_food", "prepared_meal"}
        ):
            safe_track = True
        p_item = PurchaseItem(
            purchase_id=purchase.id,
            household_id=household_id,
            line_index=idx,
            raw_text=item_in.raw_text or item_in.display_name,
            canonical_item_id=canonical_id,
            display_name=item_in.display_name.strip(),
            normalized_name=item_in.normalized_name
            or cls_res.normalized_name
            or item_in.display_name.strip().lower(),
            brand=item_in.brand,
            variant=item_in.variant or draft_res.standardized_name,
            pack_size=item_in.pack_size,
            category=safe_cat,
            match_confidence=confidence,
            normalized_qty=norm_qty,
            base_unit=base_unit,
            unit_price_paise=item_in.unit_price_paise,
            line_total_paise=line_total,
            discount_paise=item_in.discount_paise,
            tax_paise=item_in.tax_paise,
            tax_rate_bps=item_in.tax_rate_bps,
            track_inventory=safe_track,
            food_state=cls_res.food_state,
            budget_treatment=cls_res.budget_treatment,
            classification_confidence=Decimal(str(round(cls_res.confidence, 3))),
            matching_method=cls_res.matching_method,
            resolution_score=draft_res.raw_confidence_decimal,
            resolution_metadata_json=draft_res.to_metadata_dict(),
            classification_source=cls_res.source,
            expires_on=item_in.expires_on,
        )
        db.add(p_item)
        total_paise += line_total

    if purchase.subtotal_paise is None:
        purchase.subtotal_paise = total_paise
    candidate_total = (
        payload.total_amount_paise
        if payload.total_amount_paise is not None
        else (
            total_paise
            - payload.discount_paise
            + payload.additional_charges_paise
            + tax_paise
            + payload.rounding_paise
        )
    )
    if candidate_total < 0:
        await db.rollback()
    purchase.total_amount_paise = _validate_non_negative_total_paise(
        candidate_total, source="purchase"
    )
    await db.commit()
    return await get_purchase(db, household_id, purchase.id)


def _serialize_purchase(purchase: Purchase) -> PurchaseOut:
    out = PurchaseOut.model_validate(purchase)
    out.document_type = purchase.document_type or "unknown"
    out.order_id = purchase.order_id
    out.missing_fields = [str(f) for f in (purchase.missing_fields_json or []) if f]
    out.attachments = [dict(a) for a in (purchase.attachments_json or []) if isinstance(a, dict)]
    for item_out, item_db in zip(out.items, purchase.items, strict=False):
        if item_db.warnings_json and isinstance(item_db.warnings_json, list):
            item_out.warnings = [str(w) for w in item_db.warnings_json if w]
        if item_db.confidence is not None:
            item_out.confidence = float(item_db.confidence)
        meta = item_db.resolution_metadata_json
        if isinstance(meta, dict):
            item_out.standardized_name = meta.get("standardized_name")
            item_out.ingredient_family_id = meta.get("ingredient_family_id")
            item_out.ingredient_family_name = meta.get("ingredient_family_name")
            item_out.ingredient_variant_id = meta.get("ingredient_variant_id")
            raw_cid = meta.get("canonical_ingredient_id")
            if raw_cid:
                try:
                    item_out.canonical_ingredient_id = UUID(str(raw_cid))
                except (ValueError, TypeError):
                    item_out.canonical_ingredient_id = None
            item_out.ingredient_category = meta.get("ingredient_category")
            item_out.is_unspecified_variant = bool(meta.get("is_unspecified_variant", False))
            item_out.ingredient_confidence_tier = meta.get("ingredient_confidence_tier")
            item_out.ingredient_confidence_score = meta.get("ingredient_confidence_score")
            item_out.ingredient_match_source = meta.get("ingredient_match_source")
            item_out.ingredient_review_reason = meta.get("ingredient_review_reason")
            item_out.user_approved_ingredient = bool(meta.get("user_approved_ingredient", False))
            sugs = meta.get("ingredient_suggestions")
            if isinstance(sugs, list):
                item_out.ingredient_suggestions = list(sugs)
            item_out.quantity_confidence_tier = meta.get("quantity_confidence_tier")
            item_out.quantity_review_reason = meta.get("quantity_review_reason")
            item_out.financial_mismatch = bool(meta.get("financial_mismatch", False))
            item_out.financial_review_reason = meta.get("financial_review_reason")
            sup_units = meta.get("supported_units")
            if isinstance(sup_units, list):
                item_out.supported_units = [str(u) for u in sup_units]
    payload = purchase.raw_ocr_payload
    if isinstance(payload, dict):
        warnings = payload.get("warnings")
        if isinstance(warnings, list):
            out.ocr_warnings = [str(w) for w in warnings if w]
        if "math_reconciled" in payload and payload["math_reconciled"] is not None:
            out.math_reconciled = bool(payload["math_reconciled"])
        if "confidence" in payload and payload["confidence"] is not None:
            out.ocr_confidence = float(payload["confidence"])
        if isinstance(payload.get("orchestration"), dict):
            out.ocr_orchestration = dict(payload["orchestration"])
    return out


async def list_purchases(
    db: AsyncSession,
    household_id: UUID,
    *,
    status_filter: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[PurchaseOut], int]:
    base_where = [Purchase.household_id == household_id]
    if status_filter:
        base_where.append(Purchase.status == status_filter)
    else:
        base_where.append(Purchase.status != "discarded")

    total_stmt = select(func.count()).select_from(Purchase).where(*base_where)
    total = int((await db.execute(total_stmt)).scalar_one())

    stmt = (
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(*base_where)
        .order_by(Purchase.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize_purchase(row) for row in rows], total


async def get_purchase(db: AsyncSession, household_id: UUID, purchase_id: UUID) -> PurchaseOut:
    stmt = (
        select(Purchase)
        .options(selectinload(Purchase.items))
        .execution_options(populate_existing=True)
        .where(Purchase.id == purchase_id, Purchase.household_id == household_id)
    )
    purchase = (await db.execute(stmt)).scalar_one_or_none()
    if purchase is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Purchase not found.")
    out = _serialize_purchase(purchase)
    fc_stmt = select(FoodConsumption).where(
        FoodConsumption.household_id == household_id,
        FoodConsumption.purchase_id == purchase.id,
    )
    fc = (await db.execute(fc_stmt)).scalar_one_or_none()
    if fc is not None:
        out.food_consumption = FoodConsumptionOut.model_validate(fc)
    return out


async def update_draft(
    db: AsyncSession,
    household_id: UUID,
    purchase_id: UUID,
    payload: UpdatePurchaseRequest,
) -> PurchaseOut:
    stmt = (
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(Purchase.id == purchase_id, Purchase.household_id == household_id)
    )
    purchase = (await db.execute(stmt)).scalar_one_or_none()
    if purchase is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Purchase not found.")
    if purchase.status != "draft":
        raise AppError(
            status_code=409,
            code="DRAFT_ALREADY_CONFIRMED",
            message="Only draft purchases can be edited.",
        )

    if payload.merchant_name is not None:
        purchase.merchant_name = payload.merchant_name
    if payload.receipt_number is not None:
        purchase.receipt_number = payload.receipt_number
    if payload.purchased_on is not None:
        purchase.purchased_on = payload.purchased_on
    if payload.purchased_time is not None:
        purchase.purchased_time = payload.purchased_time
    if payload.receipt_category is not None:
        purchase.receipt_category = payload.receipt_category
    if payload.payment_method is not None:
        purchase.payment_method = payload.payment_method
    if payload.subtotal_paise is not None:
        purchase.subtotal_paise = payload.subtotal_paise
    if payload.discount_paise is not None:
        purchase.discount_paise = payload.discount_paise
    if payload.additional_charges_paise is not None:
        purchase.additional_charges_paise = payload.additional_charges_paise
    if payload.cgst_paise is not None:
        purchase.cgst_paise = payload.cgst_paise
    if payload.sgst_paise is not None:
        purchase.sgst_paise = payload.sgst_paise
    if payload.igst_paise is not None:
        purchase.igst_paise = payload.igst_paise
    if payload.other_tax_paise is not None:
        purchase.other_tax_paise = payload.other_tax_paise
    if payload.tax_paise is not None:
        purchase.tax_paise = payload.tax_paise
    elif any(
        v is not None
        for v in (
            payload.cgst_paise,
            payload.sgst_paise,
            payload.igst_paise,
            payload.other_tax_paise,
        )
    ):
        purchase.tax_paise = (
            purchase.cgst_paise
            + purchase.sgst_paise
            + purchase.igst_paise
            + purchase.other_tax_paise
        )
    if payload.rounding_paise is not None:
        purchase.rounding_paise = payload.rounding_paise

    if payload.items is not None:
        existing_by_id = {item.id: item for item in purchase.items}
        for existing_item in list(purchase.items):
            await db.delete(existing_item)
        await db.flush()

        total_paise = 0
        for idx, item_in in enumerate(payload.items):
            raw_qty = item_in.normalized_qty if item_in.normalized_qty is not None else item_in.qty
            raw_unit = item_in.base_unit or item_in.unit
            if raw_qty is None or raw_unit is None:
                raise AppError(
                    status_code=422,
                    code="VALIDATION_ERROR",
                    message=(
                        "Each purchase item must include qty/normalized_qty and unit/base_unit."
                    ),
                )
            try:
                norm_qty, base_unit = normalize_to_base_unit(raw_qty, raw_unit)
            except ValueError as exc:
                raise AppError(
                    status_code=422,
                    code="VALIDATION_ERROR",
                    message=str(exc),
                ) from exc

            prev_item = existing_by_id.get(item_in.id) if item_in.id else None
            preserved_raw = (
                item_in.raw_text
                if item_in.raw_text
                else (prev_item.raw_text if prev_item else item_in.display_name)
            )
            resolved_canonical_id = item_in.canonical_item_id
            if resolved_canonical_id is None:
                resolved_canonical_id, _ = await _resolve_canonical_item_id(
                    db, household_id, item_in.display_name
                )
            if (
                prev_item is not None
                and resolved_canonical_id is not None
                and item_in.display_name.strip().lower() != prev_item.display_name.strip().lower()
            ):
                from app.modules.catalog.classifier import record_user_classification_correction

                await record_user_classification_correction(
                    db,
                    household_id=household_id,
                    raw_name=preserved_raw or prev_item.display_name,
                    canonical_item_id=resolved_canonical_id,
                )
                if prev_item.display_name and prev_item.display_name != preserved_raw:
                    await record_user_classification_correction(
                        db,
                        household_id=household_id,
                        raw_name=prev_item.display_name,
                        canonical_item_id=resolved_canonical_id,
                    )
            from app.modules.catalog.classifier import classify_with_household_db

            cls_res = await classify_with_household_db(
                db,
                household_id=household_id,
                raw_name=item_in.display_name,
                receipt_category=purchase.receipt_category,
                merchant_name=purchase.merchant_name,
            )
            requested_track = (
                item_in.track_inventory
                if item_in.track_inventory is not None
                else (prev_item.track_inventory if prev_item else True)
            )
            # Never allow non-food items to be tracked as pantry inventory.
            # For ambiguous or prepared items, honour explicit user-approved override
            # (item_in.track_inventory is True)
            # or a pantry-eligible canonical item selection.
            canonical_allows_pantry = False
            if resolved_canonical_id is not None:
                c_obj = await db.get(CanonicalItem, resolved_canonical_id)
                if c_obj is not None and c_obj.pantry_eligible and c_obj.category != "non_food":
                    canonical_allows_pantry = True

            user_explicit_override = item_in.track_inventory is True
            if cls_res.classification_status == "non_food":
                safe_track = False
            elif canonical_allows_pantry or (
                user_explicit_override
                and cls_res.classification_status in {"ambiguous_review", "prepared_meal"}
            ):
                safe_track = bool(requested_track)
            elif cls_res.needs_review:
                safe_track = False
            else:
                safe_track = bool(requested_track) and cls_res.pantry_eligible

            safe_cat = (
                "non_food"
                if cls_res.classification_status == "non_food"
                else item_in.category.strip().lower()
            )
            safe_budget = "pantry_inventory" if safe_track else cls_res.budget_treatment
            safe_source = (
                "user_correction"
                if canonical_allows_pantry
                else "user_override"
                if (user_explicit_override and safe_track and not cls_res.pantry_eligible)
                else cls_res.source
            )
            updated_warnings: list[str] | None = None
            if (
                safe_budget == "requires_user_review"
                and cls_res.review_reason
                or cls_res.needs_review
                and not safe_track
                and cls_res.review_reason
            ):
                updated_warnings = [cls_res.review_reason]

            line_total = item_in.line_total_paise or item_in.unit_price_paise
            from app.modules.purchases.draft_resolution import evaluate_draft_item_resolution

            prev_meta = (
                prev_item.resolution_metadata_json
                if prev_item and isinstance(prev_item.resolution_metadata_json, dict)
                else {}
            )
            name_unchanged = (
                prev_item is not None
                and item_in.display_name.strip().lower() == prev_item.display_name.strip().lower()
            )
            effective_user_approved = bool(
                item_in.user_approved_ingredient
                or item_in.canonical_ingredient_id is not None
                or item_in.ingredient_variant_id is not None
                or (name_unchanged and prev_meta.get("user_approved_ingredient"))
            )
            draft_res = await evaluate_draft_item_resolution(
                db,
                household_id=household_id,
                raw_text=preserved_raw,
                display_name=item_in.display_name.strip(),
                canonical_item_id=resolved_canonical_id,
                normalized_qty=norm_qty,
                base_unit=base_unit,
                raw_unit=str(raw_unit),
                raw_qty=Decimal(str(raw_qty)),
                unit_price_paise=item_in.unit_price_paise,
                line_total_paise=line_total,
                discount_paise=item_in.discount_paise,
                tax_paise=item_in.tax_paise,
                track_inventory=safe_track,
                classification_status=cls_res.classification_status,
                explicit_canonical_ingredient_id=item_in.canonical_ingredient_id,
                explicit_variant_id=item_in.ingredient_variant_id,
                user_approved_ingredient=effective_user_approved,
            )
            if (
                (
                    item_in.user_approved_ingredient
                    or item_in.canonical_ingredient_id is not None
                    or item_in.ingredient_variant_id is not None
                )
                and draft_res.canonical_ingredient_id is not None
                and safe_track
            ):
                from app.modules.catalog.ingredient_catalogue_sync import (
                    record_household_learned_alias,
                )
                from app.modules.inventory.models import CanonicalIngredient

                ing_for_learn = await db.get(CanonicalIngredient, draft_res.canonical_ingredient_id)
                if ing_for_learn is not None and (preserved_raw or item_in.display_name):
                    await record_household_learned_alias(
                        db,
                        household_id=household_id,
                        phrase=preserved_raw or item_in.display_name,
                        canonical_ingredient=ing_for_learn,
                        source="user_correction",
                        provenance=f"purchase:{purchase.id}:draft_review",
                    )

            p_item = PurchaseItem(
                purchase_id=purchase.id,
                household_id=household_id,
                line_index=idx,
                raw_text=preserved_raw,
                canonical_item_id=resolved_canonical_id,
                display_name=item_in.display_name.strip(),
                normalized_name=(
                    item_in.normalized_name
                    or (prev_item.normalized_name if prev_item else None)
                    or cls_res.normalized_name
                    or item_in.display_name.strip().lower()
                ),
                brand=item_in.brand
                if item_in.brand is not None
                else (prev_item.brand if prev_item else None),
                variant=item_in.variant
                if item_in.variant is not None
                else (draft_res.standardized_name or (prev_item.variant if prev_item else None)),
                pack_size=item_in.pack_size
                if item_in.pack_size is not None
                else (prev_item.pack_size if prev_item else None),
                category=safe_cat,
                match_confidence=(
                    "user_selected" if item_in.canonical_item_id is not None else "unmatched"
                ),
                normalized_qty=norm_qty,
                base_unit=base_unit,
                unit_price_paise=item_in.unit_price_paise,
                line_total_paise=line_total,
                discount_paise=item_in.discount_paise,
                tax_paise=item_in.tax_paise,
                tax_rate_bps=item_in.tax_rate_bps,
                confidence=prev_item.confidence if prev_item else None,
                warnings_json=updated_warnings,
                track_inventory=safe_track,
                food_state=cls_res.food_state,
                budget_treatment=safe_budget,
                classification_confidence=Decimal(str(round(cls_res.confidence, 3))),
                matching_method=cls_res.matching_method,
                resolution_score=draft_res.raw_confidence_decimal,
                resolution_metadata_json=draft_res.to_metadata_dict(),
                classification_source=safe_source,
                expires_on=item_in.expires_on,
            )
            db.add(p_item)
            total_paise += line_total
        if payload.subtotal_paise is None:
            purchase.subtotal_paise = total_paise
        if payload.total_amount_paise is not None:
            candidate_total = payload.total_amount_paise
        else:
            candidate_total = (
                (purchase.subtotal_paise if purchase.subtotal_paise is not None else total_paise)
                - purchase.discount_paise
                + purchase.additional_charges_paise
                + purchase.tax_paise
                + purchase.rounding_paise
            )
        if candidate_total < 0:
            await db.rollback()
        purchase.total_amount_paise = _validate_non_negative_total_paise(
            candidate_total, source="purchase"
        )
        if payload.total_amount_paise is not None:
            _mark_total_review_verified(purchase)
    elif payload.total_amount_paise is not None:
        if payload.total_amount_paise < 0:
            await db.rollback()
        purchase.total_amount_paise = _validate_non_negative_total_paise(
            payload.total_amount_paise, source="purchase"
        )
        _mark_total_review_verified(purchase)

    await db.commit()
    return await get_purchase(db, household_id, purchase.id)


async def discard_draft(db: AsyncSession, household_id: UUID, purchase_id: UUID) -> None:
    stmt = select(Purchase).where(Purchase.id == purchase_id, Purchase.household_id == household_id)
    purchase = (await db.execute(stmt)).scalar_one_or_none()
    if purchase is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Purchase not found.")
    if purchase.status != "draft":
        raise AppError(
            status_code=409,
            code="DRAFT_ALREADY_CONFIRMED",
            message="Confirmed purchases cannot be discarded.",
        )
    purchase.status = "discarded"
    await db.commit()


async def _upsert_canonical_and_alias(
    db: AsyncSession,
    *,
    household_id: UUID,
    item: PurchaseItem,
) -> UUID:
    from app.modules.catalog.canonical_resolver import clean_canonical_candidate_name
    from app.modules.catalog.classifier import FoodClassificationService
    from app.modules.catalog.product_identity_service import (
        get_or_create_canonical_item,
        learn_confirmed_product_alias,
        resolve_product_identity,
    )

    canonical_id = item.canonical_item_id
    if canonical_id is None:
        prod_res = await resolve_product_identity(
            db,
            household_id=household_id,
            raw_description=item.display_name or item.raw_text,
            brand=item.brand,
            variant=item.variant,
            pack_size=item.pack_size,
            base_unit=item.base_unit,
        )
        if prod_res.normalized_description:
            item.normalized_name = prod_res.normalized_description
        item.resolution_method = prod_res.resolution_method
        item.resolution_score = prod_res.resolution_score

        if prod_res.outcome == "match_existing" and prod_res.resolved_canonical_item_id is not None:
            canonical_id = prod_res.resolved_canonical_item_id
            item.canonical_item_id = canonical_id
            item.resolution_status = "auto_matched"
        else:
            item.resolution_status = (
                "pending_review" if prod_res.outcome == "review_required" else "new_product"
            )
            cleaned_name = (
                clean_canonical_candidate_name(
                    item.display_name,
                    unit_price_paise=item.unit_price_paise,
                    line_total_paise=item.line_total_paise,
                    quantity=float(item.normalized_qty)
                    if item.normalized_qty is not None
                    else None,
                )
                or item.display_name
            )
            from app.modules.catalog.ingredient_resolver import IngredientResolver

            ing_res = IngredientResolver().resolve_text(cleaned_name)
            if ing_res.variant_id and not ing_res.requires_review:
                canonical_name = cleaned_name
            else:
                cls_res = FoodClassificationService.classify_item(cleaned_name)
                canonical_name = (
                    cls_res.matched_canonical_name
                    if cls_res.matched_canonical_name
                    and cls_res.confidence >= 0.85
                    and cls_res.matching_method
                    in {"exact_canonical", "exact_alias", "normalized_token"}
                    else cleaned_name
                )
            slug = _slugify(canonical_name)
            canonical, _res2, _created = await get_or_create_canonical_item(
                db,
                household_id=household_id,
                raw_name=canonical_name,
                category=item.category or "general",
                secondary_category=item.secondary_category,
                default_base_unit=item.base_unit,
                brand=item.brand,
                variant=item.variant,
                pack_size=item.pack_size,
                slug_override=slug,
                canonical_name_override=None,
            )
            canonical_id = canonical.id
            item.canonical_item_id = canonical_id
    else:
        item.resolution_status = "user_confirmed"
        item.resolution_method = item.resolution_method or "user_selected"
        item.resolution_score = item.resolution_score or Decimal("1.000")

    await learn_confirmed_product_alias(
        db,
        household_id=household_id,
        canonical_item_id=canonical_id,
        raw_description=item.display_name or item.raw_text,
    )

    norm_alias = _normalize_alias(item.raw_text or item.display_name)
    if norm_alias:
        alias_stmt = select(ItemAlias).where(
            ItemAlias.household_id == household_id,
            ItemAlias.normalized_alias == norm_alias,
        )
        existing_alias = (await db.execute(alias_stmt)).scalar_one_or_none()
        if existing_alias is None:
            db.add(
                ItemAlias(
                    household_id=household_id,
                    canonical_item_id=canonical_id,
                    normalized_alias=norm_alias,
                    source="user_confirmed",
                )
            )
            await db.flush()

    return canonical_id


async def confirm_purchase(
    db: AsyncSession,
    *,
    household_id: UUID,
    user_id: UUID,
    purchase_id: UUID,
    meal_payload: ConfirmPurchaseRequest | None = None,
) -> ConfirmPurchaseResponse:
    stmt = (
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(Purchase.id == purchase_id, Purchase.household_id == household_id)
    )
    purchase = (await db.execute(stmt)).scalar_one_or_none()
    if purchase is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Purchase not found.")
    if purchase.status != "draft":
        raise AppError(
            status_code=409,
            code="DRAFT_ALREADY_CONFIRMED",
            message=f"Purchase is already {purchase.status}.",
        )
    if not purchase.items:
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Cannot confirm a purchase with zero items.",
        )

    if _is_total_review_required(purchase):
        raise AppError(
            status_code=422,
            code="TOTAL_REVIEW_REQUIRED",
            message=(
                "This receipt's purchase total needs review before confirmation. "
                "Please verify and save the printed total."
            ),
        )
    if "ocr_partial_failure" in (purchase.missing_fields_json or []):
        raise AppError(
            status_code=422,
            code="OCR_PARTIAL_FAILURE_REVIEW_REQUIRED",
            message=(
                "One or more receipt pages failed OCR processing. "
                "Please review or retry the failed page before confirming."
            ),
        )

    if meal_payload and meal_payload.enforce_ingredient_review:
        unresolved_items: list[str] = []
        for it in purchase.items:
            if (
                it.category == "non_food"
                or it.budget_treatment in {"household_non_food", "outside_food_expenditure"}
                or (not it.track_inventory and it.budget_treatment != "requires_user_review")
            ):
                continue
            meta = (
                it.resolution_metadata_json if isinstance(it.resolution_metadata_json, dict) else {}
            )
            tier = meta.get("ingredient_confidence_tier") or it.resolution_status
            approved = bool(meta.get("user_approved_ingredient", False))
            if tier in {"LOW", "UNMATCHED"} and not approved:
                unresolved_items.append(it.display_name or it.raw_text or "Item")
        if unresolved_items:
            raise AppError(
                status_code=422,
                code="INGREDIENT_REVIEW_REQUIRED",
                message=(
                    "Approve, edit, or mark as non-pantry before confirming: "
                    + ", ".join(unresolved_items[:5])
                ),
            )

    now = _utcnow()
    purchased_at = datetime.combine(purchase.purchased_on, now.timetz())
    created_lots: list[CreatedLotSummary] = []
    ingredient_mapping_outs: list[PurchaseIngredientMappingOut] = []
    confirmed_ingredient_inputs = (
        {m.canonical_item_id: m for m in meal_payload.ingredient_mappings}
        if meal_payload and meal_payload.ingredient_mappings
        else {}
    )

    from app.modules.catalog.classifier import classify_with_household_db
    from app.modules.catalog.ingredient_mapping_service import (
        confirm_ingredient_mapping,
        resolve_or_suggest_mapping,
    )
    from app.modules.inventory.models import CanonicalIngredient

    for item in purchase.items:
        user_approved_override = (
            item.classification_source in {"user_override", "user_correction"}
            and item.budget_treatment == "pantry_inventory"
            and item.track_inventory
        )
        if (
            not item.track_inventory
            or item.category == "non_food"
            or (item.food_state == "cooked" and not user_approved_override)
            or item.budget_treatment
            in {
                "outside_food_expenditure",
                "household_non_food",
                "requires_user_review",
            }
        ):
            item.track_inventory = False
            continue

        # Independently re-validate pantry eligibility at confirmation time.
        canonical_allows_pantry = False
        if item.canonical_item_id is not None:
            c_obj = await db.get(CanonicalItem, item.canonical_item_id)
            if c_obj is not None:
                if (
                    not c_obj.pantry_eligible
                    or c_obj.category == "non_food"
                    or c_obj.is_prepared_food
                ):
                    item.track_inventory = False
                    continue
                canonical_allows_pantry = True

        recheck = await classify_with_household_db(
            db,
            household_id=household_id,
            raw_name=item.display_name,
            receipt_category=purchase.receipt_category,
            merchant_name=purchase.merchant_name,
            use_llm_fallback=False,
        )
        hf_llm_approved = (
            item.classification_source == "hf_llm"
            and item.budget_treatment == "pantry_inventory"
            and item.track_inventory
        )
        if recheck.classification_status == "non_food":
            item.track_inventory = False
            item.budget_treatment = "household_non_food"
            continue
        if recheck.classification_status == "prepared_meal" and not (
            canonical_allows_pantry or user_approved_override or hf_llm_approved
        ):
            item.track_inventory = False
            item.budget_treatment = recheck.budget_treatment
            continue
        if recheck.needs_review and not (
            canonical_allows_pantry or user_approved_override or hf_llm_approved
        ):
            item.track_inventory = False
            item.budget_treatment = "requires_user_review"
            continue
        canonical_id = await _upsert_canonical_and_alias(db, household_id=household_id, item=item)
        c_item_for_map = await db.get(CanonicalItem, canonical_id)
        if c_item_for_map is not None:
            if canonical_id in confirmed_ingredient_inputs:
                conf_in = confirmed_ingredient_inputs[canonical_id]
                saved_map = await confirm_ingredient_mapping(
                    db,
                    household_id=household_id,
                    canonical_item_id=canonical_id,
                    canonical_ingredient_id=conf_in.canonical_ingredient_id,
                    allow_override=conf_in.allow_override,
                )
                ing_obj = await db.get(CanonicalIngredient, saved_map.canonical_ingredient_id)
                ingredient_mapping_outs.append(
                    PurchaseIngredientMappingOut(
                        canonical_item_id=canonical_id,
                        status="user_confirmed",
                        tier="high",
                        canonical_ingredient_id=saved_map.canonical_ingredient_id,
                        canonical_ingredient_name=ing_obj.name if ing_obj else None,
                        matching_confidence=saved_map.matching_confidence,
                        mapping_source=saved_map.mapping_source,
                        user_confirmed=True,
                    )
                )
            else:
                meta = (
                    item.resolution_metadata_json
                    if isinstance(item.resolution_metadata_json, dict)
                    else {}
                )
                approved_ing_id_raw = (
                    meta.get("canonical_ingredient_id")
                    if meta.get("user_approved_ingredient")
                    else None
                )
                if approved_ing_id_raw:
                    approved_ing_uuid = UUID(str(approved_ing_id_raw))
                    saved_map = await confirm_ingredient_mapping(
                        db,
                        household_id=household_id,
                        canonical_item_id=canonical_id,
                        canonical_ingredient_id=approved_ing_uuid,
                        allow_override=True,
                    )
                    ing_obj = await db.get(CanonicalIngredient, saved_map.canonical_ingredient_id)
                    if ing_obj is not None and (item.raw_text or item.display_name):
                        from app.modules.catalog.ingredient_catalogue_sync import (
                            record_household_learned_alias,
                        )

                        await record_household_learned_alias(
                            db,
                            household_id=household_id,
                            phrase=item.raw_text or item.display_name,
                            canonical_ingredient=ing_obj,
                            source="user_correction",
                            provenance=f"purchase:{purchase.id}:purchase_confirm",
                        )
                    ingredient_mapping_outs.append(
                        PurchaseIngredientMappingOut(
                            canonical_item_id=canonical_id,
                            status="user_confirmed",
                            tier="high",
                            canonical_ingredient_id=saved_map.canonical_ingredient_id,
                            canonical_ingredient_name=ing_obj.name if ing_obj else None,
                            matching_confidence=saved_map.matching_confidence,
                            mapping_source=saved_map.mapping_source,
                            user_confirmed=True,
                        )
                    )
                else:
                    res = await resolve_or_suggest_mapping(
                        db,
                        household_id=household_id,
                        canonical_item=c_item_for_map,
                        auto_map_high_confidence=True,
                    )
                    ingredient_mapping_outs.append(
                        PurchaseIngredientMappingOut(
                            canonical_item_id=canonical_id,
                            status=res.status,
                            tier=res.tier,
                            canonical_ingredient_id=(
                                res.suggested_ingredient.id if res.suggested_ingredient else None
                            ),
                            canonical_ingredient_name=(
                                res.suggested_ingredient.name if res.suggested_ingredient else None
                            ),
                            matching_confidence=res.matching_confidence,
                            mapping_source=res.mapping_source,
                            user_confirmed=res.user_confirmed,
                            detected_variant=res.detected_variant,
                        )
                    )
        unit_cost = item.unit_price_paise or item.line_total_paise

        lot = InventoryLot(
            household_id=household_id,
            canonical_item_id=canonical_id,
            purchase_item_id=item.id,
            initial_qty=item.normalized_qty,
            remaining_qty=item.normalized_qty,
            base_unit=item.base_unit,
            unit_cost_paise=unit_cost,
            purchased_at=purchased_at,
            expires_on=item.expires_on,
            lot_status="active",
        )
        db.add(lot)
        await db.flush()

        ledger = StockLedger(
            household_id=household_id,
            lot_id=lot.id,
            canonical_item_id=canonical_id,
            event_type="PURCHASE_IN",
            quantity_delta=item.normalized_qty,
            base_unit=item.base_unit,
            balance_after_lot=lot.remaining_qty,
            unit_cost_paise=unit_cost,
            reference_type="purchase_item",
            reference_id=item.id,
            actor_user_id=user_id,
            notes=f"Confirmed purchase {purchase.id}",
        )
        db.add(ledger)
        created_lots.append(
            CreatedLotSummary(
                lot_id=lot.id,
                canonical_item_id=canonical_id,
                remaining_qty=lot.remaining_qty,
                base_unit=lot.base_unit,
                expires_on=lot.expires_on,
            )
        )

    # Create outside-food consumption record only for prepared meals / restaurant items
    outside_food_items = [
        it
        for it in purchase.items
        if (
            it.budget_treatment == "outside_food_expenditure"
            or it.food_state == "cooked"
            or (
                purchase.receipt_category == "restaurant"
                and not it.track_inventory
                and it.budget_treatment not in {"household_non_food", "requires_user_review"}
                and it.category != "non_food"
            )
        )
    ]
    food_consumption_out: FoodConsumptionOut | None = None
    if outside_food_items or purchase.receipt_category == "restaurant":
        inv_items = [it for it in purchase.items if it.track_inventory]
        if inv_items and outside_food_items:
            purchase.receipt_category = "mixed"
            food_amount_paise = sum(int(it.line_total_paise) for it in outside_food_items)
        else:
            if not inv_items and outside_food_items:
                purchase.receipt_category = "restaurant"
            food_amount_paise = int(purchase.total_amount_paise)

        fc_items_summary = [
            {
                "name": it.display_name,
                "qty": float(it.normalized_qty),
                "unit": it.base_unit,
                "line_total_paise": int(it.line_total_paise),
            }
            for it in (outside_food_items or purchase.items)
        ]
        fc = FoodConsumption(
            household_id=household_id,
            purchase_id=purchase.id,
            consumed_on=(
                meal_payload.consumed_on
                if meal_payload and meal_payload.consumed_on
                else purchase.purchased_on
            ),
            meal_type=(
                meal_payload.meal_type if meal_payload and meal_payload.meal_type else "other"
            ),
            dining_mode=(
                meal_payload.dining_mode
                if meal_payload and meal_payload.dining_mode
                else "restaurant"
            ),
            merchant_name=purchase.merchant_name,
            amount_paise=food_amount_paise,
            currency_code=purchase.currency_code,
            items_summary=fc_items_summary,
            people_count=meal_payload.people_count if meal_payload else None,
            notes=meal_payload.notes if meal_payload else None,
        )
        db.add(fc)
        await db.flush()
        food_consumption_out = FoodConsumptionOut.model_validate(fc)

    purchase.status = "confirmed"
    purchase.confirmed_at = now
    await db.commit()

    return ConfirmPurchaseResponse(
        purchase_id=purchase.id,
        draft_id=purchase.id,
        status=purchase.status,
        total_amount_paise=purchase.total_amount_paise,
        created_lots=created_lots,
        food_consumption=food_consumption_out,
        ingredient_mappings=ingredient_mapping_outs,
    )


async def update_meal_classification(
    db: AsyncSession,
    *,
    household_id: UUID,
    purchase_id: UUID,
    payload: ConfirmPurchaseRequest,
) -> FoodConsumptionOut:
    """Update meal/dining classification on a confirmed outside-food/mixed purchase."""
    stmt = select(FoodConsumption).where(
        FoodConsumption.household_id == household_id,
        FoodConsumption.purchase_id == purchase_id,
    )
    fc = (await db.execute(stmt)).scalar_one_or_none()
    if fc is None:
        raise AppError(
            status_code=404,
            code="NOT_FOUND",
            message="Outside-food record not found for this purchase.",
        )
    if payload.meal_type is not None:
        fc.meal_type = payload.meal_type
    if payload.dining_mode is not None:
        fc.dining_mode = payload.dining_mode
    if payload.people_count is not None:
        fc.people_count = payload.people_count
    if payload.consumed_on is not None:
        fc.consumed_on = payload.consumed_on
    if payload.notes is not None:
        fc.notes = payload.notes
    await db.commit()
    await db.refresh(fc)
    return FoodConsumptionOut.model_validate(fc)


async def backfill_missing_restaurant_consumptions(
    db: AsyncSession,
    *,
    household_id: UUID | None = None,
) -> list[FoodConsumption]:
    """Idempotently backfill FoodConsumption rows for confirmed restaurant purchases lacking one.

    Uses purchase_id uniqueness so repeated runs never create duplicates, and keeps
    meal_type/dining_mode as 'unknown' without modifying purchases, items, or ledger.
    """
    stmt = (
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(
            Purchase.status == "confirmed",
            Purchase.receipt_category == "restaurant",
        )
    )
    if household_id is not None:
        stmt = stmt.where(Purchase.household_id == household_id)

    restaurant_purchases = (await db.execute(stmt)).scalars().all()
    if not restaurant_purchases:
        return []

    pids = [p.id for p in restaurant_purchases]
    existing_pids = set(
        (
            await db.execute(
                select(FoodConsumption.purchase_id).where(FoodConsumption.purchase_id.in_(pids))
            )
        )
        .scalars()
        .all()
    )

    created: list[FoodConsumption] = []
    for p in restaurant_purchases:
        if p.id in existing_pids:
            continue
        fc = FoodConsumption(
            household_id=p.household_id,
            purchase_id=p.id,
            consumed_on=p.purchased_on,
            meal_type="unknown",
            dining_mode="unknown",
            merchant_name=p.merchant_name,
            amount_paise=int(p.total_amount_paise),
            currency_code=p.currency_code,
            items_summary=[
                {
                    "name": it.display_name,
                    "qty": float(it.normalized_qty),
                    "unit": it.base_unit,
                    "line_total_paise": int(it.line_total_paise),
                }
                for it in p.items
            ],
            notes="Backfilled from confirmed historical restaurant purchase",
        )
        db.add(fc)
        created.append(fc)

    if created:
        await db.commit()
        for fc in created:
            await db.refresh(fc)
    return created
