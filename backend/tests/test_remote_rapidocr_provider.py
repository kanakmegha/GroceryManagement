"""Mocked unit tests for RemoteRapidOcrProvider (Phase A)."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.core.config import get_settings
from app.infra.ocr.pipeline import _select_local_ocr_provider
from app.infra.ocr.providers import RawOcrOutput, RemoteRapidOcrProvider, TesseractOcrProvider


@pytest.fixture(autouse=True)
def _remote_ocr_env(monkeypatch):
    monkeypatch.setenv("OCR_PROVIDER", "remote_rapidocr")
    monkeypatch.setenv("OCR_REMOTE_BASE_URL", "https://meg89-grocery-ocr-experiment.hf.space")
    monkeypatch.setenv("OCR_API_TOKEN", "super-secret-poc-token-12345")
    monkeypatch.setenv("OCR_REMOTE_CONNECT_TIMEOUT_SECONDS", "5.0")
    monkeypatch.setenv("OCR_REMOTE_READ_TIMEOUT_SECONDS", "60.0")
    monkeypatch.setenv("OCR_REMOTE_MAX_RETRIES", "2")
    monkeypatch.setenv("OCR_REMOTE_FALLBACK_TO_TESSERACT", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_successful_ocr_response_multipart_auth_and_mapping(caplog):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "adapter_name": "rapidocr_ppocrv6_tiny_det_small_rec",
                "raw_text": "FRESH MART\nMILK 60.00",
                "boxes": [
                    {
                        "text": "FRESH MART",
                        "confidence": 0.97,
                        "x": 0.1,
                        "y": 0.05,
                        "w": 0.5,
                        "h": 0.04,
                        "page": 99,  # must be overridden by requested page
                        "reading_order": 0,
                        "language": "en",
                    },
                    {
                        "text": "MILK 60.00",
                        "confidence": 0.91,
                        "x": 0.1,
                        "y": 0.2,
                        "w": 0.6,
                        "h": 0.04,
                        "reading_order": 1,
                    },
                ],
                "warnings": [],
            },
        )

    provider = RemoteRapidOcrProvider(http_client=_make_client(handler))
    with caplog.at_level(logging.DEBUG):
        out = provider.recognize_image(b"\x89PNG\r\n\x1a\nfake-bytes", page=2)

    assert captured["url"] == "https://meg89-grocery-ocr-experiment.hf.space/ocr"
    assert captured["auth"] == "Bearer super-secret-poc-token-12345"
    assert "multipart/form-data" in captured["content_type"]
    assert b"fake-bytes" in captured["body"]

    assert out.adapter_name == "remote_rapidocr_hf"
    assert out.raw_text == "FRESH MART\nMILK 60.00"
    assert len(out.boxes) == 2
    assert out.boxes[0].page == 2
    assert out.boxes[1].page == 2
    assert out.boxes[0].confidence == pytest.approx(0.97)
    assert out.boxes[0].x == pytest.approx(0.1)

    # Verify zero token or receipt content leakage in logs
    log_text = caplog.text
    assert "super-secret-poc-token-12345" not in log_text
    assert "FRESH MART" not in log_text
    assert "MILK 60.00" not in log_text


def test_missing_token_raises_and_is_unavailable(monkeypatch):
    monkeypatch.setenv("OCR_API_TOKEN", "")
    get_settings.cache_clear()
    provider = RemoteRapidOcrProvider()
    assert provider.is_available() is False
    with pytest.raises(RuntimeError, match="not configured"):
        provider.recognize_image(b"png")


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_errors_fail_fast_without_retry_or_token_leak(status_code):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json={"error": {"code": "UNAUTHORIZED"}})

    provider = RemoteRapidOcrProvider(http_client=_make_client(handler))
    with pytest.raises(RuntimeError) as exc_info:
        provider.recognize_image(b"png")

    assert calls == 1
    assert "super-secret-poc-token-12345" not in str(exc_info.value)


@pytest.mark.parametrize("status_code", [400, 413, 415])
def test_client_validation_errors_do_not_retry(status_code):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json={"error": {"code": "BAD_REQUEST"}})

    provider = RemoteRapidOcrProvider(http_client=_make_client(handler))
    out = provider.recognize_image(b"png")

    assert calls == 1
    assert out.boxes == []
    assert any("OCR_ENGINE_ERROR" in w for w in out.warnings)


@pytest.mark.parametrize("status_code", [502, 503, 504])
def test_transient_5xx_retries_with_backoff_and_recovers(status_code):
    bodies: list[bytes] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        if len(bodies) < 3:
            return httpx.Response(status_code, text="waking up")
        return httpx.Response(
            200,
            json={
                "raw_text": "OK",
                "boxes": [
                    {"text": "OK", "confidence": 0.9, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.05}
                ],
                "warnings": [],
            },
        )

    provider = RemoteRapidOcrProvider(
        http_client=_make_client(handler),
        sleep_fn=sleeps.append,
        backoff_base_seconds=1.0,
    )
    out = provider.recognize_image(b"identical-png-bytes", page=1)

    assert len(bodies) == 3
    assert all(b"identical-png-bytes" in b for b in bodies)
    assert sleeps == [1.0, 2.0]
    assert len(out.boxes) == 1


@pytest.mark.parametrize("exc_cls", [httpx.ConnectTimeout, httpx.ReadTimeout])
def test_timeouts_retry_bounded_and_return_ocr_timeout(exc_cls):
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise exc_cls("timed out", request=request)

    provider = RemoteRapidOcrProvider(
        http_client=_make_client(handler),
        sleep_fn=sleeps.append,
        backoff_base_seconds=0.5,
    )
    out = provider.recognize_image(b"png", page=3)

    assert calls == 3  # 1 initial + 2 retries
    assert sleeps == [0.5, 1.0]
    assert out.boxes == []
    assert any("OCR_TIMEOUT" in w and "page 3" in w for w in out.warnings)


def test_malformed_json_and_missing_fields_do_not_retry():
    calls = 0

    def bad_json(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"{not-json")

    provider = RemoteRapidOcrProvider(http_client=_make_client(bad_json))
    out = provider.recognize_image(b"png")
    assert calls == 1
    assert any("OCR_ENGINE_ERROR" in w for w in out.warnings)

    calls = 0

    def missing_fields(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"unexpected": True})

    provider2 = RemoteRapidOcrProvider(http_client=_make_client(missing_fields))
    out2 = provider2.recognize_image(b"png")
    assert calls == 1
    assert any("OCR_ENGINE_ERROR" in w for w in out2.warnings)


def test_empty_result_and_invalid_coordinates_handled_safely():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "raw_text": "",
                "boxes": [
                    {"text": "BAD_NEG", "x": -0.5, "y": 0.1, "w": 0.2, "h": 0.1},
                    {"text": "BAD_ZERO", "x": 0.1, "y": 0.1, "w": 0.0, "h": 0.1},
                    {"text": "BAD_NAN", "x": "not-a-num", "y": 0.1, "w": 0.2, "h": 0.1},
                ],
                "warnings": [],
            },
        )

    provider = RemoteRapidOcrProvider(http_client=_make_client(handler))
    out = provider.recognize_image(b"png")
    assert out.boxes == []
    assert any("Skipped 3 invalid bounding box" in w for w in out.warnings)
    assert any("no text regions" in w for w in out.warnings)


def test_explicit_tesseract_fallback_only_when_enabled(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down", request=request)

    fake_tess_out = RawOcrOutput(adapter_name="tesseract_tsv", raw_text="FALLBACK", boxes=[])
    monkeypatch.setenv("OCR_REMOTE_MAX_RETRIES", "0")
    monkeypatch.setenv("OCR_REMOTE_FALLBACK_TO_TESSERACT", "true")
    get_settings.cache_clear()

    provider = RemoteRapidOcrProvider(http_client=_make_client(handler))
    with (
        patch.object(TesseractOcrProvider, "is_available", return_value=True),
        patch.object(
            TesseractOcrProvider, "recognize_image", return_value=fake_tess_out
        ) as mock_tess,
    ):
        out = provider.recognize_image(b"png", page=1)

    mock_tess.assert_called_once()
    assert out.adapter_name == "tesseract_tsv"


def test_provider_selection_respects_ocr_provider_setting(monkeypatch):
    monkeypatch.setenv("OCR_PROVIDER", "remote_rapidocr")
    get_settings.cache_clear()
    selected = _select_local_ocr_provider()
    assert isinstance(selected, RemoteRapidOcrProvider)


def test_default_timeout_and_retry_budget_within_proxy_limit(monkeypatch):
    for key in (
        "OCR_REMOTE_CONNECT_TIMEOUT_SECONDS",
        "OCR_REMOTE_READ_TIMEOUT_SECONDS",
        "OCR_REMOTE_MAX_RETRIES",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.OCR_REMOTE_CONNECT_TIMEOUT_SECONDS == 8.0
    assert s.OCR_REMOTE_READ_TIMEOUT_SECONDS == 25.0
    assert s.OCR_REMOTE_MAX_RETRIES == 1
    attempts = 1 + s.OCR_REMOTE_MAX_RETRIES
    backoff_total = sum(1.0 * (2**i) for i in range(s.OCR_REMOTE_MAX_RETRIES))
    max_duration_s = attempts * (
        s.OCR_REMOTE_CONNECT_TIMEOUT_SECONDS + s.OCR_REMOTE_READ_TIMEOUT_SECONDS
    ) + backoff_total
    assert max_duration_s == pytest.approx(67.0)
    assert max_duration_s < 100.0


@pytest.mark.asyncio
async def test_multipage_partial_failure_persists_warning_and_blocks_confirm(
    client,
    jwks_provider,
    monkeypatch,
) -> None:
    import io

    from PIL import Image, ImageDraw

    from app.infra.ocr.providers import RapidOcrProvider
    from app.infra.ocr.schemas import OcrTokenBox

    monkeypatch.setenv("OCR_PROVIDER", "remote_rapidocr")
    monkeypatch.setenv("OCR_REMOTE_BASE_URL", "https://meg89-grocery-ocr-experiment.hf.space")
    monkeypatch.setenv("OCR_API_TOKEN", "test-token-phase-c")
    monkeypatch.setenv("OCR_REMOTE_FALLBACK_TO_TESSERACT", "false")
    monkeypatch.setenv("EXPERIMENTAL_OCR_ENABLED", "false")
    get_settings.cache_clear()
    RapidOcrProvider.reset_cache()

    token = jwks_provider.make_token(sub="clk_phase_c_pf", email="pf@example.com", name="PF User")
    headers = {"Authorization": f"Bearer {token}"}
    me_res = await client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    headers["X-Household-Id"] = me_res.json()["active_household"]["id"]

    def _make_png(label: str) -> bytes:
        img = Image.new("RGB", (600, 900), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        for y in range(40, 800, 40):
            draw.text((40, y), f"{label} RECEIPT LINE {y} 60.00", fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    calls = 0

    def _mock_recognize(self, png_bytes: bytes, *, page: int = 1) -> RawOcrOutput:
        nonlocal calls
        calls += 1
        if calls == 1:
            return RawOcrOutput(
                adapter_name="remote_rapidocr_hf",
                raw_text="FRESH MART\nWhole Milk 1L 1 60.00\nGRAND TOTAL 60.00",
                boxes=[
                    OcrTokenBox(
                        text="FRESH MART", confidence=0.98, x=0.1, y=0.05, w=0.5, h=0.04, page=1
                    ),
                    OcrTokenBox(
                        text="Whole Milk 1L", confidence=0.95, x=0.1, y=0.20, w=0.4, h=0.04, page=1
                    ),
                    OcrTokenBox(
                        text="60.00", confidence=0.95, x=0.7, y=0.20, w=0.2, h=0.04, page=1
                    ),
                    OcrTokenBox(
                        text="GRAND TOTAL", confidence=0.96, x=0.1, y=0.40, w=0.4, h=0.04, page=1
                    ),
                    OcrTokenBox(
                        text="60.00", confidence=0.96, x=0.7, y=0.40, w=0.2, h=0.04, page=1
                    ),
                ],
                warnings=["LOW_CONTRAST_NOTE"],
            )
        return RawOcrOutput(
            adapter_name="remote_rapidocr_hf",
            raw_text="",
            boxes=[],
            warnings=[
                "LOW_CONTRAST_NOTE",
                "OCR_TIMEOUT: Remote RapidOCR timed out (ReadTimeout) on page 1; please retry.",
            ],
        )

    with patch.object(RemoteRapidOcrProvider, "recognize_image", _mock_recognize):
        res = await client.post(
            "/api/v1/purchases/ocr",
            headers=headers,
            files=[
                ("files", ("page1.png", _make_png("P1"), "image/png")),
                ("files", ("page2.png", _make_png("P2"), "image/png")),
            ],
        )

    assert res.status_code == 201
    draft = res.json()
    assert calls == 2
    assert draft["merchant_name"] == "FRESH MART"
    assert draft["total_amount_paise"] == 6000
    assert len(draft["items"]) == 1
    assert "ocr_partial_failure" in draft["missing_fields"]
    assert any("OCR_TIMEOUT" in w for w in draft["ocr_warnings"])
    assert draft["ocr_warnings"].count("LOW_CONTRAST_NOTE") == 1
    assert RapidOcrProvider._cached_engine is None

    confirm_res = await client.post(f"/api/v1/purchases/{draft['id']}/confirm", headers=headers)
    assert confirm_res.status_code == 422
    assert confirm_res.json()["error"]["code"] == "OCR_PARTIAL_FAILURE_REVIEW_REQUIRED"
