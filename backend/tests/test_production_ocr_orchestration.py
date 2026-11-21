"""Phase 9: Comprehensive Tests for Unified OCR Orchestration (Experimental V10.6 Primary + Existing Fallback)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import ClerkClaims, get_current_claims
from app.main import create_app
from app.modules.experimental_ocr.orchestrator import orchestrate_receipt_extraction
from app.modules.experimental_ocr.provider import ExperimentalV10_6ExtractionProvider

SAMPLE_EX4 = Path(
    "/Users/skanakmegha/.gemini/antigravity/brain/b7627d1e-bf7b-404c-8d86-2bd55bc9429a/.user_uploaded/media_1795181605039_ca8eed79.jpg"
)


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "orch_test.db"
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(cfg, "head")
    return db_path


def _setup_app(db_path: Path, *, exp_enabled: bool, exp_primary: bool, fb_enabled: bool = True, api_key: str = "") -> tuple[Any, dict[str, str]]:
    settings = Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{db_path}",
        EXPERIMENTAL_OCR_ENABLED=exp_enabled,
        EXPERIMENTAL_OCR_PRIMARY=exp_primary,
        EXISTING_OCR_FALLBACK_ENABLED=fb_enabled,
        EXPERIMENTAL_OCR_API_KEY=api_key,
    )
    app = create_app(settings)
    active_user = {"sub": "orch_u1", "email": "u1@orch.test", "name": "U1"}

    async def _claims() -> ClerkClaims:
        return ClerkClaims(sub=active_user["sub"], email=active_user["email"], display_name=active_user["name"], azp=None, raw={})

    app.dependency_overrides[get_current_claims] = _claims
    return app, {"Authorization": "Bearer t"}, active_user  # type: ignore[return-value]


async def _mock_existing_ok(**kwargs: Any) -> dict[str, Any]:
    return {
        "adapter_used": "apple_vision_ocrmac",
        "merchant_name": "Fallback Store",
        "receipt_date": "2026-11-20",
        "subtotal_paise": 6000,
        "grand_total_paise": 6000,
        "items": [{"raw_text": "Milk 1L 60.00", "name": "Milk 1L", "quantity": 1.0, "unit": "l", "unit_price_paise": 6000, "total_paise": 6000}],
        "warnings": [],
    }


async def _mock_existing_fail(**kwargs: Any) -> dict[str, Any]:
    return {
        "adapter_used": "ocr_provider_unavailable",
        "items": [],
        "grand_total_paise": None,
        "warnings": ["OCR_PROVIDER_UNAVAILABLE"],
    }


def _mock_vlm_provider(response_or_exc: Any) -> ExperimentalV10_6ExtractionProvider:
    settings = Settings(EXPERIMENTAL_OCR_ENABLED=True, EXPERIMENTAL_OCR_PRIMARY=True, EXPERIMENTAL_OCR_API_KEY="k", EXPERIMENTAL_OCR_BASE_URL="https://mock.local/v1")
    prov = ExperimentalV10_6ExtractionProvider(settings)

    def _handler(req: httpx.Request) -> httpx.Response:
        if isinstance(response_or_exc, Exception):
            raise response_or_exc
        return response_or_exc

    orig_extract = prov.extract

    async def _wrapped(*, image_bytes: bytes, content_type: str, transport: Any = None) -> Any:
        return await orig_extract(image_bytes=image_bytes, content_type=content_type, transport=httpx.MockTransport(_handler))

    prov.extract = _wrapped  # type: ignore[method-assign]
    return prov


@pytest.mark.asyncio
async def test_orchestrator_unit_scenarios() -> None:
    s_on = Settings(EXPERIMENTAL_OCR_ENABLED=True, EXPERIMENTAL_OCR_PRIMARY=True, EXISTING_OCR_FALLBACK_ENABLED=True, EXPERIMENTAL_OCR_API_KEY="k")

    # 1. Experimental succeeds
    good_json = {
        "document_type": "GST_INVOICE_TABULAR",
        "merchant_evidence": {"observed_merchant": "Fresh Mart", "inferred_merchant": None, "confidence": 0.99},
        "transaction": {"purchase_date": "2026-11-20"},
        "items": [{"original_description": "Milk 1L", "quantity": 1.0, "unit": "l", "unit_price": 60.0, "line_total": 60.0, "gst": {}}],
        "fees": [],
        "financial_summary": {"subtotal_after_tax": 60.0, "grand_total": 60.0},
    }
    p_ok = _mock_vlm_provider(httpx.Response(200, json={"choices": [{"message": {"content": __import__("json").dumps(good_json)}}]}))
    res_ok = await orchestrate_receipt_extraction(settings=s_on, existing_extract_fn=_mock_existing_ok, file_bytes=b"img", filename="r.jpg", content_type="image/jpeg", experimental_provider=p_ok)
    assert res_ok["raw_payload"]["orchestration"]["extraction_status"] == "EXPERIMENTAL_SUCCESS"
    assert res_ok["raw_payload"]["orchestration"]["fallback_triggered"] is False

    # 2. Experimental partial (missing merchant/date) -> RETAINS experimental, NO fallback
    partial_json = {**good_json, "merchant_evidence": {"observed_merchant": None, "inferred_merchant": None, "confidence": 0.0}, "transaction": {"purchase_date": None}}
    p_part = _mock_vlm_provider(httpx.Response(200, json={"choices": [{"message": {"content": __import__("json").dumps(partial_json)}}]}))
    res_part = await orchestrate_receipt_extraction(settings=s_on, existing_extract_fn=_mock_existing_ok, file_bytes=b"img", filename="r.jpg", content_type="image/jpeg", experimental_provider=p_part)
    assert res_part["raw_payload"]["orchestration"]["extraction_status"] == "EXPERIMENTAL_PARTIAL"
    assert res_part["raw_payload"]["orchestration"]["fallback_triggered"] is False

    # 3-6. Timeout, 5xx, Malformed JSON, Zero usable items -> FALLBACK_SUCCESS
    for exc_or_resp in (
        httpx.ReadTimeout("timeout"),
        httpx.Response(503, text="unavailable"),
        httpx.Response(200, json={"choices": [{"message": {"content": "NOT_JSON"}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": '{"items": []}'}}]}),
    ):
        p_fail = _mock_vlm_provider(exc_or_resp)
        res_fb = await orchestrate_receipt_extraction(settings=s_on, existing_extract_fn=_mock_existing_ok, file_bytes=b"img", filename="r.jpg", content_type="image/jpeg", experimental_provider=p_fail)
        assert res_fb["raw_payload"]["orchestration"]["extraction_status"] == "FALLBACK_SUCCESS"
        assert res_fb["raw_payload"]["orchestration"]["fallback_triggered"] is True
        assert res_fb["merchant_name"] == "Fallback Store"

    # 7. Both engines fail -> explicit AppError 502 ALL_PROVIDERS_FAILED
    p_fail = _mock_vlm_provider(httpx.Response(503, text="down"))
    with pytest.raises(AppError) as ei:
        await orchestrate_receipt_extraction(settings=s_on, existing_extract_fn=_mock_existing_fail, file_bytes=b"img", filename="r.jpg", content_type="image/jpeg", experimental_provider=p_fail)
    assert ei.value.code == "ALL_PROVIDERS_FAILED"

    # 8. Experimental disabled -> uses existing OCR cleanly
    s_off = Settings(EXPERIMENTAL_OCR_ENABLED=False, EXPERIMENTAL_OCR_PRIMARY=True, EXISTING_OCR_FALLBACK_ENABLED=True)
    res_off = await orchestrate_receipt_extraction(settings=s_off, existing_extract_fn=_mock_existing_ok, file_bytes=b"img", filename="r.jpg", content_type="image/jpeg")
    assert res_off["raw_payload"]["orchestration"]["primary_status"] == "SKIPPED"
    assert res_off["merchant_name"] == "Fallback Store"


@pytest.mark.asyncio
async def test_live_main_purchase_ocr_orchestration_and_isolation(migrated_db: Path) -> None:
    hf_token = get_settings().HF_TOKEN or os.getenv("HF_TOKEN", "")
    if not hf_token or not SAMPLE_EX4.exists():
        pytest.skip("HF_TOKEN or sample receipt unavailable")

    app, h_a, active_user = _setup_app(migrated_db, exp_enabled=True, exp_primary=True, fb_enabled=True, api_key=hf_token)  # type: ignore[misc]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            me = await client.get("/api/v1/auth/me", headers=h_a)
            hh_a = me.json()["households"][0]["id"]
            h_a = {**h_a, "X-Household-Id": hh_a}

            img_bytes = SAMPLE_EX4.read_bytes()
            r1 = await client.post("/api/v1/purchases/ocr", headers=h_a, files={"file": ("ex4.jpg", img_bytes, "image/jpeg")})
            assert r1.status_code == 201
            d1 = r1.json()
            assert d1["status"] == "draft"
            assert d1["ocr_orchestration"]["selected_provider"] == "qwen3_vl_30b_v10_6"
            assert d1["ocr_orchestration"]["fallback_triggered"] is False
            assert len(d1["items"]) >= 3

            # Duplicate upload within 60s returns same single draft (no duplicate draft)
            r2 = await client.post("/api/v1/purchases/ocr", headers=h_a, files={"file": ("ex4.jpg", img_bytes, "image/jpeg")})
            assert r2.json()["id"] == d1["id"]

            # Inventory & learning remain empty until explicit confirmation
            inv_pre = await client.get("/api/v1/inventory/lots", headers=h_a)
            assert inv_pre.json()["items"] == []

            # Confirm draft -> creates lots once; re-confirm -> 409
            c1 = await client.post(f"/api/v1/purchases/drafts/{d1['id']}/confirm", headers={**h_a, "Idempotency-Key": "k1"}, json={})
            assert c1.status_code == 200
            inv_post = await client.get("/api/v1/inventory/lots", headers=h_a)
            lots_count = len(inv_post.json()["items"])
            assert lots_count >= 1
            c2 = await client.post(f"/api/v1/purchases/drafts/{d1['id']}/confirm", headers={**h_a, "Idempotency-Key": "k1"}, json={})
            assert c2.status_code == 409

            # Household isolation: User B cannot read User A's draft
            active_user.update({"sub": "orch_u2", "email": "u2@orch.test", "name": "U2"})
            me_b = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer t"})
            hh_b = me_b.json()["households"][0]["id"]
            r_iso = await client.get(f"/api/v1/purchases/{d1['id']}", headers={"Authorization": "Bearer t", "X-Household-Id": hh_b})
            assert r_iso.status_code == 404
