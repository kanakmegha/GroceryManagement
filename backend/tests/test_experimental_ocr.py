"""Tests for the isolated Experimental V10.6 OCR service."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.core.security import ClerkClaims, get_current_claims
from app.main import create_app

SAMPLE_EX4 = Path(
    "/Users/skanakmegha/.gemini/antigravity/brain/b7627d1e-bf7b-404c-8d86-2bd55bc9429a/.user_uploaded/media_1795181605039_ca8eed79.jpg"
)


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "exp_test.db"
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(cfg, "head")
    return db_path


def _setup_app(
    db_path: Path,
    *,
    exp_enabled: bool = False,
    base_url: str = "https://router.huggingface.co/v1/chat/completions",
    api_key: str = "",
) -> tuple[Any, dict[str, str]]:
    settings = Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{db_path}",
        EXPERIMENTAL_OCR_ENABLED=exp_enabled,
        EXPERIMENTAL_OCR_BASE_URL=base_url,
        EXPERIMENTAL_OCR_API_KEY=api_key,
    )
    app = create_app(settings)

    async def _claims() -> ClerkClaims:
        return ClerkClaims(
            sub="exp_user_1", email="exp@test.local", display_name="ExpTester", azp=None, raw={}
        )

    app.dependency_overrides[get_current_claims] = _claims
    return app, {"Authorization": "Bearer test-token"}


@pytest.mark.asyncio
async def test_experimental_disabled_and_existing_ocr_unchanged(migrated_db: Path) -> None:
    app, headers = _setup_app(migrated_db, exp_enabled=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        me = await client.get("/api/v1/auth/me", headers=headers)
        hh = me.json()["households"][0]["id"]
        h = {**headers, "X-Household-Id": hh}

        health = await client.get("/api/experimental/receipts/health")
        assert health.status_code == 200
        hb = health.json()
        assert hb["enabled"] is False
        assert hb["isolation_guaranteed"] is True
        assert "api_key" not in str(hb).lower() or "api_key_configured" in hb

        # Experimental extract fails explicitly when disabled (no silent fallback!)
        r_exp = await client.post(
            "/api/experimental/receipts/extract",
            headers=h,
            files={"file": ("r.jpg", b"fake-bytes", "image/jpeg")},
            data={"mode": "experimental"},
        )
        assert r_exp.status_code == 201
        assert r_exp.json()["experimental_result"]["status"] == "failed"
        assert "EXPERIMENTAL_OCR_DISABLED" in r_exp.json()["experimental_result"]["error"]
        assert r_exp.json()["inventory_mutated"] is False

        # Existing OCR endpoint still works unchanged
        r_old = await client.post(
            "/api/v1/purchases/ocr",
            headers=h,
            json={"raw_text": "Milk 1L 60.00\nTotal 60.00"},
        )
        assert r_old.status_code == 201
        assert r_old.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_malformed_and_timeout_handling_and_auth(migrated_db: Path) -> None:
    from app.modules.experimental_ocr.provider import ExperimentalV10_6ExtractionProvider

    settings = Settings(
        EXPERIMENTAL_OCR_ENABLED=True,
        EXPERIMENTAL_OCR_API_KEY="k",
        EXPERIMENTAL_OCR_BASE_URL="https://mock.local/v1",
    )
    prov = ExperimentalV10_6ExtractionProvider(settings)

    # Malformed JSON
    bad_transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "NOT_JSON"}}]})
    )
    res_bad = await prov.extract(
        image_bytes=b"img", content_type="image/jpeg", transport=bad_transport
    )
    assert res_bad.status == "failed"
    assert "EXPERIMENTAL_MALFORMED_RESPONSE" in (res_bad.error or "")

    # Timeout
    def _timeout_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    res_to = await prov.extract(
        image_bytes=b"img",
        content_type="image/jpeg",
        transport=httpx.MockTransport(_timeout_handler),
    )
    assert res_to.status == "failed"
    assert "EXPERIMENTAL_PROVIDER_TIMEOUT" in (res_to.error or "")

    # Auth required on experimental endpoints
    app, _ = _setup_app(migrated_db, exp_enabled=False)
    app.dependency_overrides.clear()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        unauth = await client.post(
            "/api/experimental/receipts/extract", files={"file": ("r.jpg", b"x", "image/jpeg")}
        )
        assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_live_qwen3_vl_compare_both_and_zero_inventory_mutation(migrated_db: Path) -> None:
    hf_token = get_settings().HF_TOKEN or os.getenv("HF_TOKEN", "")
    if not hf_token or not SAMPLE_EX4.exists():
        pytest.skip("HF_TOKEN or sample receipt unavailable")

    app, headers = _setup_app(migrated_db, exp_enabled=True, api_key=hf_token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        me = await client.get("/api/v1/auth/me", headers=headers)
        hh = me.json()["households"][0]["id"]
        h = {**headers, "X-Household-Id": hh}

        r_cmp = await client.post(
            "/api/experimental/receipts/extract",
            headers=h,
            files={"file": ("ex4.jpg", SAMPLE_EX4.read_bytes(), "image/jpeg")},
            data={"mode": "compare"},
        )
        assert r_cmp.status_code == 201
        body = r_cmp.json()
        assert body["mode"] == "compare"
        assert body["inventory_mutated"] is False
        assert body["existing_result"]["status"] == "completed"
        exp = body["experimental_result"]
        assert exp["status"] == "completed", f"Live Qwen3-VL failed: {exp.get('error')}"
        assert len(exp["items"]) >= 3
        assert exp["merchant_evidence"]["unavailable_merchant"] is True

        # Verify job retrieval and ground truth attachment
        r_get = await client.get(f"/api/experimental/receipts/jobs/{body['job_id']}", headers=h)
        assert r_get.status_code == 200
        r_gt = await client.post(
            f"/api/experimental/receipts/jobs/{body['job_id']}/ground-truth",
            headers=h,
            json={"grand_total": 1158.79},
        )
        assert r_gt.status_code == 200
        assert r_gt.json()["ground_truth"]["grand_total"] == 1158.79

        # Verify zero inventory mutation
        inv = await client.get("/api/v1/inventory/lots", headers=h)
        assert inv.json()["items"] == []
