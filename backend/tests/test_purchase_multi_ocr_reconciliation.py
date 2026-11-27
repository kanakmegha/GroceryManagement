"""Regression tests for multi-file OCR purchase-total reconciliation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.core.errors import AppError
from app.modules.auth.models import Household, HouseholdMember, User
from app.modules.purchases.schemas import UpdatePurchaseRequest
from app.modules.purchases.service import (
    confirm_purchase,
    create_multi_ocr_draft,
    update_draft,
)


async def _make_session_and_seed(tmp_path: Path) -> tuple[AsyncSession, User, Household, object]:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'multi_ocr.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    db = session_maker()
    user = User(clerk_user_id=f"clk_multi_{uuid4().hex[:8]}", display_name="Tester")
    db.add(user)
    await db.flush()
    household = Household(name="Test Kitchen", created_by=user.id)
    db.add(household)
    await db.flush()
    db.add(HouseholdMember(household_id=household.id, user_id=user.id, role="owner"))
    await db.commit()
    return db, user, household, engine


def _item(name: str, total_paise: int) -> dict:
    return {
        "raw_text": f"{name} {total_paise / 100:.2f}",
        "display_name": name,
        "qty": 1,
        "unit": "pcs",
        "unit_price_paise": total_paise,
        "line_total_paise": total_paise,
        "confidence": 0.92,
    }


@pytest.mark.asyncio
async def test_multi_ocr_matching_explicit_grand_total(tmp_path: Path) -> None:
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    try:
        pages = [
            {"merchant_name": "Big Mart", "items": [_item("Milk", 30000)]},
            {"merchant_name": "Big Mart", "grand_total_paise": 50000, "items": [_item("Bread", 20000)]},
        ]
        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(side_effect=pages),
        ):
            draft = await create_multi_ocr_draft(
                db,
                household_id=household.id,
                user_id=user.id,
                files=[(b"p1", "p1.jpg", "image/jpeg"), (b"p2", "p2.jpg", "image/jpeg")],
            )
        assert draft.total_amount_paise == 50000
        assert len(draft.items) == 2
        assert "total_amount" not in draft.missing_fields
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_multi_ocr_discrepancy_within_and_exceeding_200_paise(tmp_path: Path) -> None:
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    try:
        # Exactly ₹2 (200 paise) discrepancy -> passes without review
        within = [
            {"merchant_name": "Mart A", "items": [_item("Milk", 30000)]},
            {"merchant_name": "Mart A", "grand_total_paise": 50200, "items": [_item("Eggs", 20000)]},
        ]
        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(side_effect=within),
        ):
            d1 = await create_multi_ocr_draft(
                db,
                household_id=household.id,
                user_id=user.id,
                files=[(b"a1", "a1.jpg", "image/jpeg"), (b"a2", "a2.jpg", "image/jpeg")],
            )
        assert d1.total_amount_paise == 50200
        assert "total_amount" not in d1.missing_fields

        # 201 paise discrepancy -> flags needs_review
        over = [
            {"merchant_name": "Mart B", "items": [_item("Rice", 30000)]},
            {"merchant_name": "Mart B", "grand_total_paise": 50201, "items": [_item("Dal", 20000)]},
        ]
        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(side_effect=over),
        ):
            d2 = await create_multi_ocr_draft(
                db,
                household_id=household.id,
                user_id=user.id,
                files=[(b"b1", "b1.jpg", "image/jpeg"), (b"b2", "b2.jpg", "image/jpeg")],
            )
        assert d2.total_amount_paise == 50201
        assert "total_amount" in d2.missing_fields
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_multi_ocr_missing_explicit_total_and_overlapping_duplicate_lines(
    tmp_path: Path,
) -> None:
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    try:
        # Page 2 repeats "Milk" from Page 1 — must be deduplicated, not double-counted
        pages = [
            {"merchant_name": "Fresh Store", "items": [_item("Milk", 10000)]},
            {
                "merchant_name": "Fresh Store",
                "items": [_item("Milk", 10000), _item("Curd", 15000)],
            },
        ]
        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(side_effect=pages),
        ):
            draft = await create_multi_ocr_draft(
                db,
                household_id=household.id,
                user_id=user.id,
                files=[(b"d1", "d1.jpg", "image/jpeg"), (b"d2", "d2.jpg", "image/jpeg")],
            )
        assert len(draft.items) == 2
        assert draft.total_amount_paise == 25000
        assert "total_amount" not in draft.missing_fields
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_multi_ocr_negative_calculated_total_blocks_confirm_until_fixed(
    tmp_path: Path,
) -> None:
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    household_id = household.id
    user_id = user.id
    try:
        pages = [
            {"merchant_name": "Discount Mart", "items": [_item("Milk", 10000)]},
            {
                "merchant_name": "Discount Mart",
                "discount_paise": 40000,
                "items": [_item("Bread", 5000)],
            },
        ]
        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(side_effect=pages),
        ):
            draft = await create_multi_ocr_draft(
                db,
                household_id=household_id,
                user_id=user_id,
                files=[(b"n1", "n1.jpg", "image/jpeg"), (b"n2", "n2.jpg", "image/jpeg")],
            )

        assert len(draft.items) == 2
        assert draft.total_amount_paise == 0
        assert "total_amount" in draft.missing_fields

        with pytest.raises(AppError) as err:
            await confirm_purchase(
                db, household_id=household_id, user_id=user_id, purchase_id=draft.id
            )
        assert err.value.status_code == 422
        assert err.value.code == "TOTAL_REVIEW_REQUIRED"

        fixed = await update_draft(
            db, household_id, draft.id, UpdatePurchaseRequest(total_amount_paise=15000)
        )
        assert fixed.total_amount_paise == 15000
        assert "total_amount" not in fixed.missing_fields

        confirmed = await confirm_purchase(
            db, household_id=household_id, user_id=user_id, purchase_id=draft.id
        )
        assert confirmed.status == "confirmed"
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_multi_ocr_explicit_negative_total_rejected_as_refund(tmp_path: Path) -> None:
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    try:
        pages = [
            {"merchant_name": "Refund Mart", "items": [_item("Milk", 10000)]},
            {"merchant_name": "Refund Mart", "grand_total_paise": -21801, "items": []},
        ]
        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(side_effect=pages),
        ):
            with pytest.raises(AppError) as err:
                await create_multi_ocr_draft(
                    db,
                    household_id=household.id,
                    user_id=user.id,
                    files=[(b"r1", "r1.jpg", "image/jpeg"), (b"r2", "r2.jpg", "image/jpeg")],
                )
        assert err.value.status_code == 422
        assert err.value.code == "REFUND_OR_CREDIT_RECEIPT"
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_multi_ocr_preserves_legitimate_same_page_duplicates_and_diff_qty_or_discount(
    tmp_path: Path,
) -> None:
    """Legitimate duplicate rows on the same page, or same product with different qty/discount,
    must NEVER be silently discarded during multi-page merge.
    """
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    try:
        milk_qty2 = {**_item("Milk", 20000), "qty": 2, "unit_price_paise": 10000}
        milk_disc = {**_item("Milk", 9000), "qty": 1, "unit_price_paise": 10000, "discount_paise": 1000}
        pages = [
            {"merchant_name": "Grocery Hub", "items": [_item("Milk", 10000)]},
            {
                "merchant_name": "Grocery Hub",
                "items": [
                    _item("Bread", 5000),
                    _item("Bread", 5000),  # Two legitimate identical Bread entries on Page 2
                    milk_qty2,  # Same product, different qty/total
                    milk_disc,  # Same product, different discount/total
                ],
            },
        ]
        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(side_effect=pages),
        ):
            draft = await create_multi_ocr_draft(
                db,
                household_id=household.id,
                user_id=user.id,
                files=[(b"x1", "x1.jpg", "image/jpeg"), (b"x2", "x2.jpg", "image/jpeg")],
            )
        assert len(draft.items) == 5
        assert draft.total_amount_paise == 49000
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_multi_ocr_preserves_genuine_merchant_subtotal_even_when_equal_to_page1_sum(
    tmp_path: Path,
) -> None:
    """A genuine merchant-provided subtotal_paise on Page 1 must NOT be overwritten when Page 2
    is merged, even if it happened to equal Page 1's item sum.
    """
    db, user, household, engine = await _make_session_and_seed(tmp_path)
    try:
        pages = [
            {
                "merchant_name": "Strict Mart",
                "subtotal_paise": 10000,  # Genuine merchant subtotal (equals Page 1 sum)
                "items": [_item("Milk", 10000)],
            },
            {
                "merchant_name": "Strict Mart",
                "items": [_item("Curd", 15000)],
            },
        ]
        with patch(
            "app.modules.purchases.service._invoke_extract_receipt",
            new=AsyncMock(side_effect=pages),
        ):
            draft = await create_multi_ocr_draft(
                db,
                household_id=household.id,
                user_id=user.id,
                files=[(b"s1", "s1.jpg", "image/jpeg"), (b"s2", "s2.jpg", "image/jpeg")],
            )
        assert draft.subtotal_paise == 10000  # Genuine merchant subtotal preserved!
        assert "total_amount" in draft.missing_fields  # Flags SUBTOTAL_LINE_MISMATCH for review
    finally:
        await db.close()
        await engine.dispose()  # type: ignore[attr-defined]
