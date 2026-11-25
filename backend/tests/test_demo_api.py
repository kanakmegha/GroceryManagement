from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.routing import _DefaultLifespan

from app.core.config import Settings
from app.main import create_app
from app.modules.demo.dataset_builder import DEMO_HOUSEHOLD_ID
from app.modules.purchases.models import Purchase
from scripts.refresh_demo_dataset import execute_demo_refresh
from tests.conftest import EphemeralJWKSProvider
from tests.test_demo_dataset_refresh import _seed_temp_source_db

FORBIDDEN_KEYS = {
    "email",
    "clerk_user_id",
    "raw_ocr_payload",
    "attachments_json",
    "receipt_number",
    "order_id",
    "payment_method",
    "raw_text",
    "notes",
    "feedback_comment",
    "created_by",
    "actor_user_id",
    "cooked_by",
    "updated_by",
}

FORBIDDEN_PII_STRINGS = [
    "private.owner@example.com",
    "user_2abcREALCLERK99999",
    "Real Private Home",
    "Real Owner Name",
    "9876543210",
    "INV-998877",
    "ORD-554433",
    "UPI GPay",
    "Flat 402",
    "Sector 12",
    "Draft Secret Store",
    "/Users/private/receipt.jpg",
]


def _collect_all_keys(obj: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            keys.update(_collect_all_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            keys.update(_collect_all_keys(item))
    return keys


@asynccontextmanager
async def _make_demo_client(
    db_url: str,
    jwks_provider: EphemeralJWKSProvider,
    *,
    public_demo_enabled: bool = True,
    demo_household_id: str = str(DEMO_HOUSEHOLD_ID),
    rate_limit_requests: int = 120,
    rate_limit_window_seconds: int = 60,
    cache_max_age_seconds: int = 60,
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        DATABASE_URL=db_url,
        CLERK_ISSUER=jwks_provider.issuer,
        CLERK_JWKS_URL=jwks_provider.jwks_url,
        CLERK_AUTHORIZED_PARTIES=["https://app.example.test"],
        JWKS_CACHE_TTL_SECONDS=3600,
        JWKS_REFRESH_COOLDOWN_SECONDS=30,
        JWT_LEEWAY_SECONDS=10,
        PUBLIC_DEMO_ENABLED=public_demo_enabled,
        DEMO_HOUSEHOLD_ID=demo_household_id,
        DEMO_RATE_LIMIT_REQUESTS=rate_limit_requests,
        DEMO_RATE_LIMIT_WINDOW_SECONDS=rate_limit_window_seconds,
        DEMO_CACHE_MAX_AGE_SECONDS=cache_max_age_seconds,
    )
    jwks_http_client = httpx.AsyncClient(transport=jwks_provider.transport())
    app = create_app(settings_override=settings, jwks_http_client=jwks_http_client)
    lifespan_ctx = app.router.lifespan_context(app)
    assert not isinstance(lifespan_ctx, _DefaultLifespan)
    try:
        async with (
            lifespan_ctx,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as async_client,
        ):
            yield async_client
    finally:
        await jwks_http_client.aclose()


async def _seed_real_and_demo_db(tmp_path: Path) -> tuple[str, UUID, UUID, UUID]:
    """Seed a temp SQLite DB with both a real private household and a refreshed demo household.

    Returns ``(db_url, real_household_id, real_purchase_id, demo_purchase_id)``.
    """
    db_path = tmp_path / "demo_api_test.db"
    real_hh_id, _real_user_id, _global_ci_id = await _seed_temp_source_db(db_path)
    db_url = f"sqlite+aiosqlite:///{db_path}"
    _report, _counts, src_ok = await execute_demo_refresh(
        db_path,
        db_url,
        source_household_id=real_hh_id,
        plan_date=date(2026, 11, 25),
    )
    assert src_ok is True

    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        real_purchase = (
            await db.execute(
                select(Purchase).where(
                    Purchase.household_id == real_hh_id,
                    Purchase.status == "confirmed",
                )
            )
        ).scalar_one()
        demo_purchase = (
            await db.execute(
                select(Purchase).where(
                    Purchase.household_id == DEMO_HOUSEHOLD_ID,
                    Purchase.status == "confirmed",
                )
            )
        ).scalar_one()
        real_purchase_id = real_purchase.id
        demo_purchase_id = demo_purchase.id
    await engine.dispose()
    return db_url, real_hh_id, real_purchase_id, demo_purchase_id


@pytest.mark.asyncio
async def test_demo_endpoints_disabled_by_default_return_404(
    tmp_path: Path,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    db_url, _real_hh_id, _real_p_id, _demo_p_id = await _seed_real_and_demo_db(tmp_path)
    async with _make_demo_client(db_url, jwks_provider, public_demo_enabled=False) as client:
        for path in (
            "/api/v1/demo/dashboard",
            "/api/v1/demo/inventory",
            "/api/v1/demo/purchases",
            "/api/v1/demo/receipts",
            "/api/v1/demo/recipes",
            "/api/v1/demo/daily-plan",
            "/api/v1/demo/analytics",
        ):
            res = await client.get(path)
            assert res.status_code == 404, f"{path} returned {res.status_code}"
            assert res.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_demo_endpoints_anonymous_access_headers_and_pii_exclusion(
    tmp_path: Path,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    db_url, _real_hh_id, _real_p_id, demo_p_id = await _seed_real_and_demo_db(tmp_path)
    async with _make_demo_client(
        db_url,
        jwks_provider,
        public_demo_enabled=True,
        cache_max_age_seconds=90,
    ) as client:
        endpoints = [
            "/api/v1/demo/dashboard",
            "/api/v1/demo/inventory",
            "/api/v1/demo/purchases",
            f"/api/v1/demo/purchases/{demo_p_id}",
            "/api/v1/demo/receipts",
            f"/api/v1/demo/receipts/{demo_p_id}",
            "/api/v1/demo/recipes",
            "/api/v1/demo/daily-plan",
            "/api/v1/demo/analytics",
        ]
        payloads: dict[str, Any] = {}
        for path in endpoints:
            res = await client.get(path)
            assert res.status_code == 200, f"{path} failed: {res.status_code} {res.text}"
            assert res.headers.get("x-demo-mode") == "true"
            assert res.headers.get("x-robots-tag") == "noindex"
            assert (
                res.headers.get("cache-control")
                == "public, max-age=90, stale-while-revalidate=30"
            )

            body = res.json()
            payloads[path] = body

            # Explicit DTO allowlist verification: zero forbidden keys
            present_forbidden = _collect_all_keys(body) & FORBIDDEN_KEYS
            assert not present_forbidden, f"{path} leaked forbidden keys: {present_forbidden}"

            # Zero real-household PII strings anywhere in response
            raw_json = json.dumps(body)
            for pii in FORBIDDEN_PII_STRINGS:
                assert pii not in raw_json, f"{path} leaked PII string: {pii}"

        # Structural verification across the 7 endpoints
        dash = payloads["/api/v1/demo/dashboard"]
        assert dash["demo_mode"] is True
        assert dash["household"]["name"] == "Demo Kitchen"
        assert dash["household"]["is_demo_mode"] is True
        assert dash["household"]["read_only"] is True
        assert dash["inventory_item_count"] >= 1
        assert dash["active_lot_count"] >= 1
        assert dash["daily_plan"]["status"] == "ready"
        assert len(dash["daily_plan"]["recipes"]) == 8
        assert dash["analytics"]["purchase_count"] == 1

        inv = payloads["/api/v1/demo/inventory"]
        assert inv["total_items"] == 1
        assert inv["total_lots"] == 1
        assert inv["summary"][0]["name"] == "Moong Dal"

        purchases = payloads["/api/v1/demo/purchases"]
        assert purchases["total"] == 1
        assert len(purchases["items"]) == 1
        assert purchases["items"][0]["id"] == str(demo_p_id)
        assert purchases["items"][0]["item_count"] == 1

        receipts = payloads["/api/v1/demo/receipts"]
        assert receipts["total"] == 1
        assert receipts["items"][0]["id"] == str(demo_p_id)

        recipes = payloads["/api/v1/demo/recipes"]
        assert len(recipes["daily_recipes"]) == 8
        assert recipes["total_cooking_plans"] == 1
        assert recipes["total_meal_history"] == 1

        daily = payloads["/api/v1/demo/daily-plan"]
        assert daily["status"] == "ready"
        assert len(daily["recipes"]) == 8
        assert set(daily["categories"].keys()) == {"breakfast", "lunch", "snacks", "dinner"}

        # Category filter on daily-plan
        bfast = await client.get("/api/v1/demo/daily-plan", params={"category": "breakfast"})
        assert bfast.status_code == 200
        assert len(bfast.json()["recipes"]) == 2
        assert all(r["meal_category"] == "breakfast" for r in bfast.json()["recipes"])

        analytics = payloads["/api/v1/demo/analytics"]
        assert analytics["purchase_count"] == 1
        assert analytics["total_spend_paise"] == 18000
        assert len(analytics["budgets"]) == 1


@pytest.mark.asyncio
async def test_normal_authenticated_endpoints_still_require_clerk_auth(
    tmp_path: Path,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    db_url, _real_hh_id, _real_p_id, _demo_p_id = await _seed_real_and_demo_db(tmp_path)
    async with _make_demo_client(db_url, jwks_provider, public_demo_enabled=True) as client:
        for path in (
            "/api/v1/auth/me",
            "/api/v1/households",
            "/api/v1/inventory/summary",
            "/api/v1/purchases",
            "/api/v1/discover/daily-plan",
            "/api/v1/insights/summary",
        ):
            res = await client.get(path)
            assert res.status_code == 401, f"{path} expected 401, got {res.status_code}"


@pytest.mark.asyncio
async def test_demo_prevents_cross_household_idor_and_header_manipulation(
    tmp_path: Path,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    db_url, real_hh_id, real_p_id, demo_p_id = await _seed_real_and_demo_db(tmp_path)
    async with _make_demo_client(db_url, jwks_provider, public_demo_enabled=True) as client:
        attack_headers = {"X-Household-Id": str(real_hh_id)}
        attack_params = {"household_id": str(real_hh_id)}

        # Manipulated header + query param still resolve ONLY Demo Kitchen
        dash = await client.get(
            "/api/v1/demo/dashboard", headers=attack_headers, params=attack_params
        )
        assert dash.status_code == 200
        assert dash.json()["household"]["name"] == "Demo Kitchen"
        assert str(real_hh_id) not in dash.text
        assert str(real_p_id) not in dash.text

        # Detail IDOR against real household purchase/receipt MUST return 404
        res_p = await client.get(
            f"/api/v1/demo/purchases/{real_p_id}", headers=attack_headers
        )
        assert res_p.status_code == 404
        assert res_p.json()["error"]["code"] == "NOT_FOUND"

        res_r = await client.get(
            f"/api/v1/demo/receipts/{real_p_id}", headers=attack_headers
        )
        assert res_r.status_code == 404
        assert res_r.json()["error"]["code"] == "NOT_FOUND"

        # Pagination boundaries never spill into real household rows
        p_page = await client.get(
            "/api/v1/demo/purchases",
            headers=attack_headers,
            params={"limit": 100, "offset": 0},
        )
        assert p_page.status_code == 200
        assert p_page.json()["total"] == 1
        assert [it["id"] for it in p_page.json()["items"]] == [str(demo_p_id)]

        p_empty_page = await client.get(
            "/api/v1/demo/purchases",
            headers=attack_headers,
            params={"limit": 20, "offset": 10},
        )
        assert p_empty_page.status_code == 200
        assert p_empty_page.json()["items"] == []
        assert p_empty_page.json()["total"] == 1


@pytest.mark.asyncio
async def test_demo_rejects_all_mutations(
    tmp_path: Path,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    db_url, _real_hh_id, _real_p_id, demo_p_id = await _seed_real_and_demo_db(tmp_path)
    async with _make_demo_client(db_url, jwks_provider, public_demo_enabled=True) as client:
        targets = [
            "/api/v1/demo/dashboard",
            "/api/v1/demo/inventory",
            "/api/v1/demo/purchases",
            f"/api/v1/demo/purchases/{demo_p_id}",
            "/api/v1/demo/receipts",
            f"/api/v1/demo/receipts/{demo_p_id}",
            "/api/v1/demo/recipes",
            "/api/v1/demo/daily-plan",
            "/api/v1/demo/analytics",
        ]
        for path in targets:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                res = await client.request(method, path, json={"mutation": True})
                assert res.status_code in {404, 405}, (
                    f"{method} {path} unexpectedly returned {res.status_code}"
                )


@pytest.mark.asyncio
async def test_demo_empty_dataset_returns_graceful_empty_dtos(
    temp_db_url: str,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    async with _make_demo_client(temp_db_url, jwks_provider, public_demo_enabled=True) as client:
        dash = await client.get("/api/v1/demo/dashboard")
        assert dash.status_code == 200
        assert dash.json()["inventory_item_count"] == 0
        assert dash.json()["daily_plan"]["status"] == "empty"

        inv = await client.get("/api/v1/demo/inventory")
        assert inv.status_code == 200
        assert inv.json()["total_items"] == 0

        purchases = await client.get("/api/v1/demo/purchases")
        assert purchases.status_code == 200
        assert purchases.json()["total"] == 0

        receipts = await client.get("/api/v1/demo/receipts")
        assert receipts.status_code == 200
        assert receipts.json()["total"] == 0

        recipes = await client.get("/api/v1/demo/recipes")
        assert recipes.status_code == 200
        assert recipes.json()["daily_recipes"] == []

        daily = await client.get("/api/v1/demo/daily-plan")
        assert daily.status_code == 200
        assert daily.json()["status"] == "empty"

        analytics = await client.get("/api/v1/demo/analytics")
        assert analytics.status_code == 200
        assert analytics.json()["total_spend_paise"] == 0

        missing_detail = await client.get(f"/api/v1/demo/purchases/{uuid4()}")
        assert missing_detail.status_code == 404


@pytest.mark.asyncio
async def test_demo_rate_limiting_enforced_per_ip(
    tmp_path: Path,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    db_url, _real_hh_id, _real_p_id, _demo_p_id = await _seed_real_and_demo_db(tmp_path)
    async with _make_demo_client(
        db_url,
        jwks_provider,
        public_demo_enabled=True,
        rate_limit_requests=3,
        rate_limit_window_seconds=60,
    ) as client:
        ip_a = {"X-Forwarded-For": "203.0.113.10"}
        ip_b = {"X-Forwarded-For": "203.0.113.20"}

        for _ in range(3):
            ok = await client.get("/api/v1/demo/dashboard", headers=ip_a)
            assert ok.status_code == 200

        limited = await client.get("/api/v1/demo/dashboard", headers=ip_a)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert "retry-after" in {k.lower() for k in limited.headers}
        assert int(limited.headers["Retry-After"]) >= 1

        # Distinct IP is unaffected
        other_ip = await client.get("/api/v1/demo/dashboard", headers=ip_b)
        assert other_ip.status_code == 200
