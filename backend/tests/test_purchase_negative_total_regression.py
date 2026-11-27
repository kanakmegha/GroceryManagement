"""Regression tests for OCR purchase-total reconciliation, review gating, and VLM triggers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.core.errors import AppError
from app.infra.ocr.pipeline import _needs_vlm_escalation
from app.infra.ocr.schemas import ExtractedReceipt, ExtractedReceiptLine
from app.modules.auth.models import Household, HouseholdMember, User
from app.modules.purchases.models import Purchase
from app.modules.purchases.schemas import (
    CreatePurchaseRequest,
    PurchaseItemInput,
    UpdatePurchaseRequest,
)
from app.modules.purchases.service import (
    DEFAULT_TOTAL_RECONCILIATION_TOLERANCE_PAISE,
    _validate_non_negative_total_paise,
    confirm_purchase,
    create_manual_draft,
    create_ocr_draft,
    merge_ocr_into_draft,
    reconcile_ocr_purchase_total,
    update_draft,
)


async def _make_session_and_seed(tmp_path: Path) -> tuple[AsyncSession, User, Household, object]:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'reconcile_total.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    db = session_maker()
    user = User(clerk_user_id=f"clk_rec_{uuid4().hex[:8]}", display_name="Tester")
    db.add(user)
    await db.flush()
    household = Household(name="Test Kitchen", created_by=user.id)
    db.add(household)
    await db.flush()
    db.add(HouseholdMember(household_id=household.id, user_id=user.id, role="owner"))
    await db.commit()
    return db, user, household, engine


def test_reconcile_tolerance_boundary_exact_200_paise() -> None:
    """Discrepancy == 200 paise (₹2.00) passes; 201 paise flags needs_review."""
    assert DEFAULT_TOTAL_RECONCILIATION_TOLERANCE_PAISE == 200

    at_boundary = reconcile_ocr_purchase_total(
        explicit_total_paise=50200,
        subtotal_paise=50000,
        line_items_sum_paise=50000,
        tolerance_paise=200,
    )
    assert at_boundary.total_amount_paise == 50200
    assert at_boundary.needs_review is False
    assert at_boundary.discrepancy_paise == 200

    over_boundary = reconcile_ocr_purchase_total(
        explicit_total_paise=50201,
        subtotal_paise=50000,
        line_items_sum_paise=50000,
        tolerance_paise=200,
    )
    assert over_boundary.total_amount_paise == 50201
    assert over_boundary.needs_review is True
    assert over_boundary.review_reason == "reconciliation_mismatch"
    assert over_boundary.issue_code == "TOTAL_RECONCILIATION_MISMATCH"
    assert over_boundary.discrepancy_paise == 201


def test_reconcile_refund_credit_note_distinct_from_invalid_total() -> None:
    with pytest.raises(AppError) as exc_info:
        reconcile_ocr_purchase_total(
            explicit_total_paise=-21801,
            subtotal_paise=None,
            line_items_sum_paise=10000,
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "REFUND_OR_CREDIT_RECEIPT"


def test_reconcile_tax_and_rounding_discrepancy_and_subtotal_mismatch() -> None:
    # Valid discount + tax + rounding matches explicit total
    matched = reconcile_ocr_purchase_total(
        explicit_total_paise=47505,
        subtotal_paise=50000,
        line_items_sum_paise=50000,
        discount_paise=5000,
        tax_paise=2500,
        rounding_paise=5,
    )
    assert matched.total_amount_paise == 47505
    assert matched.needs_review is False

    # No explicit grand total, subtotal disagrees with line_items_sum by > ₹2
    sub_mismatch = reconcile_ocr_purchase_total(
        explicit_total_paise=None,
        subtotal_paise=55000,
        line_items_sum_paise=50000,
        discount_paise=0,
        tax_paise=0,
    )
    assert sub_mismatch.total_amount_paise == 50000
    assert sub_mismatch.needs_review is True
    assert sub_mismatch.review_reason == "subtotal_mismatch"
    assert sub_mismatch.issue_code == "SUBTOTAL_LINE_MISMATCH"


def test_needs_vlm_escalation_triggers_on_missing_total_negative_net_and_low_conf() -> None:
    base_line = ExtractedReceiptLine(raw_text="Milk 100", name="Milk", total_paise=10000)

    missing_total = ExtractedReceipt(
        filename="r.jpg",
        content_type="image/jpeg",
        source_type="image",
        lines=[base_line],
        grand_total_paise=None,
        math_reconciled=True,
        confidence=0.9,
    )
    assert _needs_vlm_escalation(missing_total) is True

    negative_net = ExtractedReceipt(
        filename="r.jpg",
        content_type="image/jpeg",
        source_type="image",
        lines=[base_line],
        discount_paise=31801,
        grand_total_paise=10000,
        math_reconciled=True,
        confidence=0.9,
    )
    assert _needs_vlm_escalation(negative_net) is True

    low_conf = ExtractedReceipt(
        filename="r.jpg",
        content_type="image/jpeg",
        source_type="image",
        lines=[base_line],
        grand_total_paise=10000,
        math_reconciled=True,
        confidence=0.45,
    )
    assert _needs_vlm_escalation(low_conf) is True

    clean = ExtractedReceipt(
        filename="r.jpg",
        content_type="image/jpeg",
        source_type="image",
        lines=[base_line],
        grand_total_paise=10000,
        math_reconciled=True,
        confidence=0.88,
    )
    assert _needs_vlm_escalation(clean) is False


@pytest.mark.asyncio
async def test_negative_computed_total_preserves_draft_blocks_confirm_and_allows_after_fix(
    tmp_path: Path,
) -> None:
    """Reproduces Render req_db4be622fa86 (-21801 paise):
    1. Draft and extracted items are preserved (no HTTP 502 / CheckViolationError).
    2. Persisted total_review state is needs_review and 'total_amount' is in missing_fields.
    3. Unrelated draft edit preserves needs_review.
    4. Merge operation preserves unresolved needs_review.
    5. Confirmation is blocked with 422 TOTAL_REVIEW_REQUIRED.
    6. Explicit user total_amount_paise update clears review state and allows confirmation.
    """
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    household_id = household.id
    user_id = user.id
    try:
        malformed_ocr = {
            "merchant_name": "Fresh Mart",
            "discount_paise": 31801,
            "tax_paise": 0,
            "rounding_paise": 0,
            "items": [
                {
                    "raw_text": "Milk 1L",
                    "display_name": "Milk",
                    "qty": 1,
                    "unit": "l",
                    "unit_price_paise": 10000,
                    "line_total_paise": 10000,
                    "confidence": 0.9,
                }
            ],
        }

        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(return_value=malformed_ocr),
        ):
            draft = await create_ocr_draft(
                db,
                household_id=household_id,
                user_id=user_id,
                raw_text="Milk 100.00 @ 85 saved on this order",
            )

        assert draft.status == "draft"
        assert len(draft.items) == 1
        assert draft.items[0].display_name == "Milk"
        assert draft.total_amount_paise == 0
        assert "total_amount" in draft.missing_fields

        # Unrelated edit (merchant name) must preserve review state
        edited = await update_draft(
            db,
            household_id,
            draft.id,
            UpdatePurchaseRequest(merchant_name="Fresh Mart Express"),
        )
        assert "total_amount" in edited.missing_fields

        # Merge OCR must preserve unresolved review state
        merge_ocr = {
            "merchant_name": "Fresh Mart Express",
            "grand_total_paise": 50000,
            "items": [],
        }
        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(return_value=merge_ocr),
        ):
            merged = await merge_ocr_into_draft(
                db,
                household_id=household_id,
                purchase_id=draft.id,
                file_bytes=b"extra-page",
                filename="page2.jpg",
                content_type="image/jpeg",
            )
        assert "total_amount" in merged.missing_fields

        # Confirmation must be blocked before user explicitly reviews/submits total
        with pytest.raises(AppError) as confirm_err:
            await confirm_purchase(
                db,
                household_id=household_id,
                user_id=user_id,
                purchase_id=draft.id,
            )
        assert confirm_err.value.status_code == 422
        assert confirm_err.value.code == "TOTAL_REVIEW_REQUIRED"

        # User explicitly enters printed total -> clears review state
        fixed = await update_draft(
            db,
            household_id,
            draft.id,
            UpdatePurchaseRequest(total_amount_paise=42500),
        )
        assert fixed.total_amount_paise == 42500
        assert "total_amount" not in fixed.missing_fields

        # Confirmation now succeeds
        confirmed = await confirm_purchase(
            db,
            household_id=household_id,
            user_id=user_id,
            purchase_id=draft.id,
        )
        assert confirmed.purchase_id == draft.id
        assert confirmed.status == "confirmed"
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_ocr_draft_rejects_negative_explicit_grand_total_as_refund(tmp_path: Path) -> None:
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    household_id = household.id
    user_id = user.id
    try:
        refund_ocr = {
            "merchant_name": "Refund Store",
            "grand_total_paise": -21801,
            "items": [
                {
                    "raw_text": "Apples",
                    "display_name": "Apples",
                    "qty": 1,
                    "unit": "kg",
                    "unit_price_paise": 15000,
                    "line_total_paise": 15000,
                    "confidence": 0.9,
                }
            ],
        }

        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(return_value=refund_ocr),
        ):
            with pytest.raises(AppError) as exc_info:
                await create_ocr_draft(
                    db,
                    household_id=household_id,
                    user_id=user_id,
                    raw_text="Apples -218.01",
                )

        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "REFUND_OR_CREDIT_RECEIPT"
        rows = (
            await db.execute(select(Purchase).where(Purchase.household_id == household_id))
        ).scalars().all()
        assert rows == []
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_manual_draft_still_rejects_negative_computed_total(tmp_path: Path) -> None:
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    try:
        payload = CreatePurchaseRequest(
            merchant_name="Corner Shop",
            discount_paise=25000,
            items=[
                PurchaseItemInput(
                    display_name="Bread",
                    qty=1,
                    unit="pcs",
                    unit_price_paise=4000,
                    line_total_paise=4000,
                )
            ],
        )

        with pytest.raises(AppError) as exc_info:
            await create_manual_draft(
                db,
                household_id=household.id,
                user_id=user.id,
                payload=payload,
            )

        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "INVALID_PURCHASE_TOTAL"
        assert _validate_non_negative_total_paise(0) == 0
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]
