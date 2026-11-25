"""Dedicated public read-only API routes for the isolated Demo Kitchen household.

Only explicit GET routes are registered here — zero mutations, zero LLM/OCR calls,
and zero reliance on client-supplied household IDs or authentication headers.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_db
from app.core.errors import AppError
from app.modules.auth.models import Household
from app.modules.demo.schemas import (
    DemoAnalyticsResponse,
    DemoDailyMealPlanResponse,
    DemoDashboardResponse,
    DemoInventoryResponse,
    DemoPurchaseOut,
    DemoPurchasesResponse,
    DemoReceiptOut,
    DemoReceiptsResponse,
    DemoRecipesResponse,
)
from app.modules.demo.service import (
    DemoRateLimiter,
    get_demo_analytics,
    get_demo_daily_plan,
    get_demo_dashboard,
    get_demo_inventory,
    get_demo_purchase_detail,
    get_demo_purchases,
    get_demo_receipt_detail,
    get_demo_receipts,
    get_demo_recipes,
    resolve_demo_household,
)
from app.modules.discovery.daily_schemas import DailyMealCategory

router = APIRouter(prefix="/demo", tags=["demo"])


def _get_demo_rate_limiter(request: Request, settings: Settings) -> DemoRateLimiter:
    limiter: DemoRateLimiter | None = getattr(request.app.state, "demo_rate_limiter", None)
    if limiter is None:
        limiter = DemoRateLimiter(
            max_requests=settings.DEMO_RATE_LIMIT_REQUESTS,
            window_seconds=settings.DEMO_RATE_LIMIT_WINDOW_SECONDS,
        )
        request.app.state.demo_rate_limiter = limiter
    return limiter


def _apply_demo_headers(response: Response, settings: Settings) -> None:
    max_age = max(0, int(settings.DEMO_CACHE_MAX_AGE_SECONDS))
    response.headers["Cache-Control"] = f"public, max-age={max_age}, stale-while-revalidate=30"
    response.headers["X-Demo-Mode"] = "true"
    response.headers["X-Robots-Tag"] = "noindex"


async def _demo_guard(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> tuple[Household | None, UUID, AsyncSession]:
    settings: Settings = request.app.state.settings
    if not settings.PUBLIC_DEMO_ENABLED:
        raise AppError(
            status_code=404,
            code="NOT_FOUND",
            message="Public demo mode is not enabled.",
        )

    client_ip = (
        (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    limiter = _get_demo_rate_limiter(request, settings)
    allowed, retry_after = limiter.check(client_ip)
    if not allowed:
        raise AppError(
            status_code=429,
            code="RATE_LIMITED",
            message="Too many demo requests. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after)},
        )

    household, demo_hh_id = await resolve_demo_household(db, settings)
    _apply_demo_headers(response, settings)
    return household, demo_hh_id, db


@router.get("/dashboard", response_model=DemoDashboardResponse)
async def demo_dashboard(
    ctx: tuple[Household | None, UUID, AsyncSession] = Depends(_demo_guard),
) -> DemoDashboardResponse:
    household, demo_hh_id, db = ctx
    return await get_demo_dashboard(db, household, demo_hh_id)


@router.get("/inventory", response_model=DemoInventoryResponse)
async def demo_inventory(
    category: str | None = Query(default=None, max_length=64),
    status: str = Query(default="active", pattern="^(active|consumed|expired|all)$"),
    expiring_within_days: int | None = Query(default=None, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=5000),
    ctx: tuple[Household | None, UUID, AsyncSession] = Depends(_demo_guard),
) -> DemoInventoryResponse:
    _household, demo_hh_id, db = ctx
    return await get_demo_inventory(
        db,
        demo_hh_id,
        category=category,
        lot_status=status,
        expiring_within_days=expiring_within_days,
        limit=limit,
        offset=offset,
    )


@router.get("/purchases", response_model=DemoPurchasesResponse)
async def demo_purchases(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=5000),
    include_items: bool = Query(default=True),
    ctx: tuple[Household | None, UUID, AsyncSession] = Depends(_demo_guard),
) -> DemoPurchasesResponse:
    _household, demo_hh_id, db = ctx
    return await get_demo_purchases(
        db, demo_hh_id, limit=limit, offset=offset, include_items=include_items
    )


@router.get("/purchases/{purchase_id}", response_model=DemoPurchaseOut)
async def demo_purchase_detail(
    purchase_id: UUID,
    ctx: tuple[Household | None, UUID, AsyncSession] = Depends(_demo_guard),
) -> DemoPurchaseOut:
    _household, demo_hh_id, db = ctx
    return await get_demo_purchase_detail(db, demo_hh_id, purchase_id)


@router.get("/receipts", response_model=DemoReceiptsResponse)
async def demo_receipts(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=5000),
    ctx: tuple[Household | None, UUID, AsyncSession] = Depends(_demo_guard),
) -> DemoReceiptsResponse:
    _household, demo_hh_id, db = ctx
    return await get_demo_receipts(db, demo_hh_id, limit=limit, offset=offset)


@router.get("/receipts/{receipt_id}", response_model=DemoReceiptOut)
async def demo_receipt_detail(
    receipt_id: UUID,
    ctx: tuple[Household | None, UUID, AsyncSession] = Depends(_demo_guard),
) -> DemoReceiptOut:
    _household, demo_hh_id, db = ctx
    return await get_demo_receipt_detail(db, demo_hh_id, receipt_id)


@router.get("/recipes", response_model=DemoRecipesResponse)
async def demo_recipes(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=5000),
    ctx: tuple[Household | None, UUID, AsyncSession] = Depends(_demo_guard),
) -> DemoRecipesResponse:
    _household, demo_hh_id, db = ctx
    return await get_demo_recipes(db, demo_hh_id, limit=limit, offset=offset)


@router.get("/daily-plan", response_model=DemoDailyMealPlanResponse)
async def demo_daily_plan(
    category: DailyMealCategory | None = Query(default=None),
    ctx: tuple[Household | None, UUID, AsyncSession] = Depends(_demo_guard),
) -> DemoDailyMealPlanResponse:
    _household, demo_hh_id, db = ctx
    return await get_demo_daily_plan(db, demo_hh_id, category=category)


@router.get("/analytics", response_model=DemoAnalyticsResponse)
async def demo_analytics(
    year: int | None = Query(default=None, ge=2020, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    ctx: tuple[Household | None, UUID, AsyncSession] = Depends(_demo_guard),
) -> DemoAnalyticsResponse:
    household, demo_hh_id, db = ctx
    currency = household.currency_code if household is not None else "INR"
    return await get_demo_analytics(
        db, demo_hh_id, currency_code=currency, year=year, month=month
    )
