"""Pluggable OCR and Vision-Language receipt extraction providers."""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.infra.ocr.schemas import OcrTokenBox

logger = logging.getLogger(__name__)


@dataclass
class RawOcrOutput:
    """Raw text + bounding boxes returned by a text-OCR provider."""

    adapter_name: str
    boxes: list[OcrTokenBox] = field(default_factory=list)
    raw_text: str = ""
    warnings: list[str] = field(default_factory=list)


class TextOcrProvider(Protocol):
    """Interface for local or cloud text+bounding-box OCR engines."""

    name: str

    def is_available(self) -> bool: ...

    def recognize_image(self, png_bytes: bytes, *, page: int = 1) -> RawOcrOutput: ...


class RapidOcrProvider:
    """Primary CPU-only OCR provider using RapidOCR 3.x (PP-OCRv6 small + ONNX Runtime).

    - Loads one cached engine per worker (never reloads weights per request).
    - Limits ONNX Runtime intra-op and inter-op threads to 1 for 0.5-vCPU Render Starter.
    - Serializes inference with a worker lock to prevent concurrent memory spikes.
    - Converts polygon boxes into normalized [0..1] OcrTokenBox records for spatial_parser.
    """

    name = "rapidocr_ppocrv6_tiny_det_small_rec"

    _init_lock = threading.Lock()
    _infer_lock = threading.Lock()
    _cached_engine: Any = None

    @classmethod
    def _build_params(cls) -> dict[str, Any]:
        from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion

        return {
            "Global.use_cls": False,
            "EngineConfig.onnxruntime.intra_op_num_threads": 1,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.enable_cpu_mem_arena": False,
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": LangDet.EN,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Det.model_type": ModelType.TINY,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": LangRec.EN,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
            "Rec.model_type": ModelType.SMALL,
        }

    @classmethod
    def _get_engine(cls) -> Any:
        if cls._cached_engine is not None:
            return cls._cached_engine
        with cls._init_lock:
            if cls._cached_engine is None:
                from rapidocr import RapidOCR

                cls._cached_engine = RapidOCR(params=cls._build_params())
            return cls._cached_engine

    @classmethod
    def reset_cache(cls) -> None:
        with cls._init_lock:
            cls._cached_engine = None

    def is_available(self) -> bool:
        try:
            import onnxruntime  # noqa: F401
            import rapidocr  # noqa: F401

            return True
        except ImportError:
            return False

    def recognize_image(self, png_bytes: bytes, *, page: int = 1) -> RawOcrOutput:
        import numpy as np
        from PIL import Image

        try:
            with Image.open(io.BytesIO(png_bytes)) as img:
                rgb_img = img.convert("RGB")
                w, h = rgb_img.size
                img_np = np.array(rgb_img)
            w = max(w, 1)
            h = max(h, 1)

            engine = self._get_engine()
            with self._infer_lock:
                result = engine(img_np)
            del img_np
        except Exception as exc:
            logger.exception("rapidocr_inference_failed page=%d: %s", page, type(exc).__name__)
            return RawOcrOutput(
                adapter_name=self.name,
                boxes=[],
                raw_text="",
                warnings=[
                    f"OCR_ENGINE_ERROR: Receipt text recognition failed ({type(exc).__name__}); "
                    "please review or enter items manually."
                ],
            )

        raw_boxes = getattr(result, "boxes", None)
        raw_txts = getattr(result, "txts", None)
        raw_scores = getattr(result, "scores", None)

        boxes: list[OcrTokenBox] = []
        if raw_boxes is not None and raw_txts is not None:
            scores_list = list(raw_scores) if raw_scores is not None else [0.85] * len(raw_txts)
            for poly, txt_raw, score_raw in zip(raw_boxes, raw_txts, scores_list, strict=False):
                txt = str(txt_raw or "").strip()
                if not txt:
                    continue
                try:
                    xs = [float(pt[0]) for pt in poly]
                    ys = [float(pt[1]) for pt in poly]
                except Exception:
                    continue
                if not xs or not ys:
                    continue
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                conf = float(score_raw) if score_raw is not None else 0.85
                boxes.append(
                    OcrTokenBox(
                        text=txt,
                        confidence=max(0.0, min(1.0, conf)),
                        x=max(0.0, min(1.0, min_x / w)),
                        y=max(0.0, min(1.0, min_y / h)),
                        w=max(0.001, min(1.0, (max_x - min_x) / w)),
                        h=max(0.001, min(1.0, (max_y - min_y) / h)),
                        page=page,
                    )
                )

        warnings: list[str] = []
        if not boxes:
            warnings.append("RapidOCR returned no text regions.")

        boxes.sort(key=lambda b: (b.y_center, b.x))
        for idx, b in enumerate(boxes):
            b.reading_order = idx
            b.language = b.language or "en"

        grouped_lines: list[list[OcrTokenBox]] = []
        for b in boxes:
            if (
                grouped_lines
                and abs(b.y_center - grouped_lines[-1][0].y_center) <= max(b.h, 0.012) * 0.65
            ):
                grouped_lines[-1].append(b)
            else:
                grouped_lines.append([b])
        raw_text = "\n".join(
            " ".join(tok.text for tok in sorted(line_boxes, key=lambda t: t.x))
            for line_boxes in grouped_lines
        )
        return RawOcrOutput(
            adapter_name=self.name,
            boxes=boxes,
            raw_text=raw_text,
            warnings=warnings,
        )


class RemoteRapidOcrProvider:
    """External Hugging Face RapidOCR microservice provider over HTTPS.

    - Sends multipart image bytes to ``POST {OCR_REMOTE_BASE_URL}/ocr``.
    - Authenticates with ``Authorization: Bearer <OCR_API_TOKEN>``.
    - Never logs tokens, headers, or extracted receipt text.
    - Applies bounded retries with exponential backoff only on transient network/502/503/504 errors.
    - Maps normalized [0..1] boxes directly into ``RawOcrOutput`` / ``OcrTokenBox``.
    """

    name = "remote_rapidocr_hf"
    TRANSIENT_STATUS_CODES = frozenset({502, 503, 504})
    NON_RETRYABLE_CLIENT_CODES = frozenset({400, 401, 403, 413, 415})

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        backoff_base_seconds: float = 1.0,
    ) -> None:
        self._http_client = http_client
        self._sleep_fn = sleep_fn
        self._backoff_base_seconds = backoff_base_seconds

    def is_available(self) -> bool:
        settings = get_settings()
        return bool(settings.OCR_REMOTE_BASE_URL.strip() and settings.OCR_API_TOKEN.strip())

    def _maybe_fallback_tesseract(
        self,
        png_bytes: bytes,
        *,
        page: int,
        reason: str,
        fallback_warning: str,
    ) -> RawOcrOutput:
        settings = get_settings()
        if settings.OCR_REMOTE_FALLBACK_TO_TESSERACT:
            tess = TesseractOcrProvider()
            if tess.is_available():
                logger.warning(
                    "remote_rapidocr_fallback_tesseract page=%d reason=%s",
                    page,
                    reason,
                )
                return tess.recognize_image(png_bytes, page=page)
        return RawOcrOutput(
            adapter_name=self.name,
            boxes=[],
            raw_text="",
            warnings=[fallback_warning],
        )

    @staticmethod
    def _parse_response_payload(
        data: Any,
        *,
        page: int,
        adapter_name: str,
    ) -> RawOcrOutput:
        if not isinstance(data, dict) or "boxes" not in data or "raw_text" not in data:
            raise ValueError(
                "Missing required 'boxes' or 'raw_text' fields in remote OCR response."
            )
        raw_boxes = data.get("boxes")
        raw_text_val = data.get("raw_text")
        if not isinstance(raw_boxes, list) or not isinstance(raw_text_val, str):
            raise ValueError("Invalid types for 'boxes' or 'raw_text' in remote OCR response.")

        boxes: list[OcrTokenBox] = []
        skipped_invalid = 0
        for idx, item in enumerate(raw_boxes):
            if not isinstance(item, dict):
                skipped_invalid += 1
                continue
            txt = str(item.get("text") or "").strip()
            if not txt:
                continue
            try:
                x = float(item["x"])
                y = float(item["y"])
                w = float(item["w"])
                h = float(item["h"])
                conf = float(item.get("confidence", 0.85))
            except (KeyError, TypeError, ValueError):
                skipped_invalid += 1
                continue
            if not all(math.isfinite(v) for v in (x, y, w, h, conf)):
                skipped_invalid += 1
                continue
            if x < 0.0 or y < 0.0 or w <= 0.0 or h <= 0.0 or x > 1.0 or y > 1.0:
                skipped_invalid += 1
                continue
            boxes.append(
                OcrTokenBox(
                    text=txt,
                    confidence=max(0.0, min(1.0, conf)),
                    x=max(0.0, min(1.0, x)),
                    y=max(0.0, min(1.0, y)),
                    w=max(0.001, min(1.0, w)),
                    h=max(0.001, min(1.0, h)),
                    page=page,
                    reading_order=int(item.get("reading_order", idx)),
                    language=str(item.get("language") or "en"),
                )
            )

        warnings: list[str] = [str(w) for w in (data.get("warnings") or []) if w]
        if skipped_invalid:
            warnings.append(f"Skipped {skipped_invalid} invalid bounding box(es) from remote OCR.")
        if not boxes:
            warnings.append("Remote RapidOCR returned no text regions.")

        return RawOcrOutput(
            adapter_name=adapter_name,
            boxes=boxes,
            raw_text=raw_text_val,
            warnings=warnings,
        )

    def recognize_image(self, png_bytes: bytes, *, page: int = 1) -> RawOcrOutput:
        settings = get_settings()
        base_url = settings.OCR_REMOTE_BASE_URL.strip().rstrip("/")
        token = settings.OCR_API_TOKEN.strip()
        if not base_url or not token:
            raise RuntimeError("Remote RapidOCR is not configured (missing URL or token).")

        url = f"{base_url}/ocr"
        headers = {"Authorization": f"Bearer {token}"}
        timeout = httpx.Timeout(
            connect=settings.OCR_REMOTE_CONNECT_TIMEOUT_SECONDS,
            read=settings.OCR_REMOTE_READ_TIMEOUT_SECONDS,
            write=settings.OCR_REMOTE_CONNECT_TIMEOUT_SECONDS,
            pool=settings.OCR_REMOTE_CONNECT_TIMEOUT_SECONDS,
        )
        max_attempts = 1 + max(0, settings.OCR_REMOTE_MAX_RETRIES)

        last_timeout = False
        last_reason = "unknown"

        client = self._http_client or httpx.Client(timeout=timeout)
        owns_client = self._http_client is None
        try:
            for attempt in range(max_attempts):
                try:
                    resp = client.post(
                        url,
                        headers=headers,
                        files={"file": (f"receipt_p{page}.png", png_bytes, "image/png")},
                        data={"page": str(page)},
                        timeout=timeout,
                    )
                except (httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                    last_timeout = True
                    last_reason = type(exc).__name__
                    logger.warning(
                        "remote_rapidocr_timeout page=%d attempt=%d/%d error=%s",
                        page,
                        attempt + 1,
                        max_attempts,
                        last_reason,
                    )
                    if attempt + 1 < max_attempts:
                        self._sleep_fn(self._backoff_base_seconds * (2**attempt))
                        continue
                    break
                except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                    last_timeout = False
                    last_reason = type(exc).__name__
                    logger.warning(
                        "remote_rapidocr_connection_error page=%d attempt=%d/%d error=%s",
                        page,
                        attempt + 1,
                        max_attempts,
                        last_reason,
                    )
                    if attempt + 1 < max_attempts:
                        self._sleep_fn(self._backoff_base_seconds * (2**attempt))
                        continue
                    break

                if resp.status_code in (401, 403):
                    logger.error(
                        "remote_rapidocr_auth_failed page=%d status=%d",
                        page,
                        resp.status_code,
                    )
                    raise RuntimeError(
                        f"Remote RapidOCR authentication failed (HTTP {resp.status_code})."
                    )

                if resp.status_code in self.NON_RETRYABLE_CLIENT_CODES:
                    logger.warning(
                        "remote_rapidocr_client_rejected page=%d status=%d",
                        page,
                        resp.status_code,
                    )
                    return RawOcrOutput(
                        adapter_name=self.name,
                        boxes=[],
                        raw_text="",
                        warnings=[
                            f"OCR_ENGINE_ERROR: Remote OCR rejected image "
                            f"(HTTP {resp.status_code}); please retake or review manually."
                        ],
                    )

                if resp.status_code in self.TRANSIENT_STATUS_CODES:
                    last_timeout = False
                    last_reason = f"HTTP_{resp.status_code}"
                    logger.warning(
                        "remote_rapidocr_transient_http page=%d status=%d attempt=%d/%d",
                        page,
                        resp.status_code,
                        attempt + 1,
                        max_attempts,
                    )
                    if attempt + 1 < max_attempts:
                        self._sleep_fn(self._backoff_base_seconds * (2**attempt))
                        continue
                    break

                if resp.status_code != 200:
                    last_timeout = False
                    last_reason = f"HTTP_{resp.status_code}"
                    logger.warning(
                        "remote_rapidocr_http_error page=%d status=%d",
                        page,
                        resp.status_code,
                    )
                    break

                try:
                    payload = resp.json()
                    parsed = self._parse_response_payload(
                        payload,
                        page=page,
                        adapter_name=self.name,
                    )
                except (ValueError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "remote_rapidocr_malformed_response page=%d error=%s",
                        page,
                        type(exc).__name__,
                    )
                    return self._maybe_fallback_tesseract(
                        png_bytes,
                        page=page,
                        reason=type(exc).__name__,
                        fallback_warning=(
                            f"OCR_ENGINE_ERROR: Remote OCR returned malformed response "
                            f"({type(exc).__name__}); please review or enter items manually."
                        ),
                    )

                logger.info(
                    "remote_rapidocr_success page=%d boxes=%d",
                    page,
                    len(parsed.boxes),
                )
                return parsed
        finally:
            if owns_client:
                client.close()

        if last_timeout:
            return self._maybe_fallback_tesseract(
                png_bytes,
                page=page,
                reason=last_reason,
                fallback_warning=(
                    f"OCR_TIMEOUT: Remote RapidOCR timed out ({last_reason}) on page {page}; "
                    "please retry or enter items manually."
                ),
            )
        return self._maybe_fallback_tesseract(
            png_bytes,
            page=page,
            reason=last_reason,
            fallback_warning=(
                f"OCR_ENGINE_ERROR: Remote RapidOCR failed ({last_reason}) on page {page}; "
                "please retry or enter items manually."
            ),
        )


class LocalVisionOcrProvider:
    """On-device macOS Apple Vision OCR via ``ocrmac`` (zero cost, offline, with bounding boxes)."""

    name = "apple_vision_ocrmac"

    def is_available(self) -> bool:
        try:
            import ocrmac  # noqa: F401

            return True
        except ImportError:
            return False

    def recognize_image(self, png_bytes: bytes, *, page: int = 1) -> RawOcrOutput:
        warnings: list[str] = []
        try:
            from ocrmac import ocrmac
            from PIL import Image
        except ImportError:
            return RawOcrOutput(
                adapter_name=self.name,
                warnings=["ocrmac is not installed in this environment."],
            )

        try:
            with Image.open(io.BytesIO(png_bytes)) as img:
                rgb_img = img.convert("RGB")
                annotations = ocrmac.OCR(
                    rgb_img,
                    recognition_level="accurate",
                    language_preference=["en-US"],
                ).recognize()
        except Exception as exc:
            return RawOcrOutput(
                adapter_name=self.name,
                warnings=[f"Local Vision OCR failed ({type(exc).__name__})."],
            )

        boxes: list[OcrTokenBox] = []
        for item in annotations or []:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            text = str(item[0] or "").strip()
            conf = float(item[1]) if item[1] is not None else 0.85
            bbox = item[2]
            if not text or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            bx, by_bottom, bw, bh = (
                float(bbox[0]),
                float(bbox[1]),
                float(bbox[2]),
                float(bbox[3]),
            )
            # Apple Vision uses bottom-left origin; convert to top-left [0..1]
            top_y = max(0.0, min(1.0, 1.0 - (by_bottom + bh)))
            boxes.append(
                OcrTokenBox(
                    text=text,
                    confidence=max(0.0, min(1.0, conf)),
                    x=max(0.0, min(1.0, bx)),
                    y=top_y,
                    w=max(0.001, min(1.0, bw)),
                    h=max(0.001, min(1.0, bh)),
                    page=page,
                )
            )

        if not boxes:
            warnings.append("Local Vision OCR returned no text spans.")

        boxes.sort(key=lambda b: (b.y_center, b.x))
        for idx, b in enumerate(boxes):
            b.reading_order = idx
            b.language = b.language or "en"

        raw_text = "\n".join(b.text for b in boxes)
        return RawOcrOutput(
            adapter_name=self.name,
            boxes=boxes,
            raw_text=raw_text,
            warnings=warnings,
        )
class TesseractOcrProvider:
    """Fallback local OCR using Tesseract TSV output for bounding boxes."""

    name = "tesseract_tsv"

    def is_available(self) -> bool:
        return shutil.which("tesseract") is not None

    def recognize_image(self, png_bytes: bytes, *, page: int = 1) -> RawOcrOutput:
        tesseract_bin = shutil.which("tesseract")
        if not tesseract_bin:
            return RawOcrOutput(
                adapter_name=self.name,
                warnings=["tesseract CLI is not installed on PATH."],
            )

        from PIL import Image

        with Image.open(io.BytesIO(png_bytes)) as img:
            w, h = img.size
        w = max(w, 1)
        h = max(h, 1)

        tess_env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            tmp.write(png_bytes)
            tmp.flush()
            try:
                proc = subprocess.run(
                    [tesseract_bin, tmp.name, "stdout", "--psm", "6", "tsv"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    env=tess_env,
                )
            except subprocess.TimeoutExpired:
                logger.warning("tesseract_timeout timeout_s=30 page=%d", page)
                return RawOcrOutput(
                    adapter_name=self.name,
                    boxes=[],
                    raw_text="",
                    warnings=[
                        "OCR_TIMEOUT: Receipt text recognition timed out after 30s; "
                        "please review or enter items manually."
                    ],
                )
        if proc.returncode != 0:
            return RawOcrOutput(
                adapter_name=self.name,
                warnings=[f"tesseract exited with code {proc.returncode}."],
            )

        boxes: list[OcrTokenBox] = []
        reader = csv.DictReader(io.StringIO(proc.stdout), delimiter="\t")
        for row in reader:
            txt = (row.get("text") or "").strip()
            if not txt:
                continue
            try:
                conf = float(row.get("conf") or -1)
                left = float(row.get("left") or 0)
                top = float(row.get("top") or 0)
                bw = float(row.get("width") or 1)
                bh = float(row.get("height") or 1)
            except ValueError:
                continue
            if conf < 0:
                continue
            boxes.append(
                OcrTokenBox(
                    text=txt,
                    confidence=max(0.0, min(1.0, conf / 100.0)),
                    x=max(0.0, min(1.0, left / w)),
                    y=max(0.0, min(1.0, top / h)),
                    w=max(0.001, min(1.0, bw / w)),
                    h=max(0.001, min(1.0, bh / h)),
                    page=page,
                )
            )

        boxes.sort(key=lambda b: (b.y_center, b.x))
        for idx, b in enumerate(boxes):
            b.reading_order = idx
            b.language = b.language or "en"

        # Group into visual lines for raw_text instead of flattening onto one line
        grouped_lines: list[list[OcrTokenBox]] = []
        for b in boxes:
            if (
                grouped_lines
                and abs(b.y_center - grouped_lines[-1][0].y_center) <= max(b.h, 0.012) * 0.65
            ):
                grouped_lines[-1].append(b)
            else:
                grouped_lines.append([b])
        raw_text = "\n".join(
            " ".join(tok.text for tok in sorted(line_boxes, key=lambda t: t.x))
            for line_boxes in grouped_lines
        )
        return RawOcrOutput(adapter_name=self.name, boxes=boxes, raw_text=raw_text)


class HuggingFaceVlmProvider:
    """Selective VLM receipt extractor using Qwen2.5-VL via HuggingFace Inference Router.

    Gated by ``RECEIPT_ENABLE_CLOUD_VLM=true`` and ``HF_TOKEN`` so no paid/cloud calls occur
    unless explicitly enabled.
    """

    name = "hf_qwen2_5_vl"
    DEFAULT_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"
    DEFAULT_VLM_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"

    def __init__(self, *, token: str | None = None, model_id: str | None = None) -> None:
        settings = get_settings()
        self._token = (token if token is not None else settings.HF_TOKEN).strip()
        self._model_id = (
            model_id or os.getenv("RECEIPT_VLM_MODEL_ID", "").strip() or self.DEFAULT_VLM_MODEL
        )
        self._enabled = os.getenv("RECEIPT_ENABLE_CLOUD_VLM", "false").lower() in {
            "1",
            "true",
            "yes",
        }

    def is_available(self) -> bool:
        return self._enabled and bool(self._token)

    async def extract_structured(
        self,
        png_bytes: bytes,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> dict[str, Any] | None:
        if not self._token:
            return None

        b64_img = base64.b64encode(png_bytes).decode("ascii")
        prompt = (
            "Extract the receipt into strict JSON with keys: "
            "merchant_name, receipt_number, receipt_date (YYYY-MM-DD or as printed), "
            "currency, subtotal, discount, cgst, sgst, igst, tax_total, rounding, grand_total, "
            "payment_method, and items (array of {raw_text, name, quantity, unit, "
            "unit_price, total_price}). "
            "Preserve exact printed item names and numbers. Never invent missing brands, "
            "quantities, or prices. "
            "Return JSON only."
        )
        payload = {
            "model": self._model_id,
            "temperature": 0.0,
            "max_tokens": 1200,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64_img}"},
                        },
                    ],
                }
            ],
        }
        headers = {"Authorization": f"Bearer {self._token}"}
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
            for attempt in range(2):
                try:
                    resp = await client.post(
                        self.DEFAULT_ROUTER_URL,
                        json=payload,
                        headers=headers,
                    )
                    if resp.status_code >= 500 and attempt == 0:
                        continue
                    if resp.status_code != 200:
                        logger.warning("VLM receipt call returned HTTP %s", resp.status_code)
                        return None
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if not content:
                        return None
                    cleaned = content.strip()
                    if cleaned.startswith("```"):
                        cleaned = cleaned.strip("`")
                        if cleaned.lower().startswith("json"):
                            cleaned = cleaned[4:].strip()
                    return json.loads(cleaned)
                except Exception as exc:
                    logger.warning("VLM receipt extraction error: %s", type(exc).__name__)
                    if attempt == 0:
                        continue
                    return None
        return None


class AzureReceiptProvider:
    """Adapter for Azure AI Document Intelligence prebuilt-receipt / prebuilt-invoice."""

    name = "azure_doc_intelligence"

    def __init__(self) -> None:
        self.endpoint = os.getenv("AZURE_DOC_INTEL_ENDPOINT", "").strip().rstrip("/")
        self.api_key = os.getenv("AZURE_DOC_INTEL_KEY", "").strip()

    def is_available(self) -> bool:
        return bool(self.endpoint and self.api_key)

    async def analyze_receipt(
        self,
        file_bytes: bytes,
        *,
        content_type: str = "image/png",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> dict[str, Any] | None:
        if not self.is_available():
            return None
        url = (
            f"{self.endpoint}/documentintelligence/documentModels/"
            "prebuilt-receipt:analyze?api-version=2024-11-30"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Type": content_type,
        }
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
            resp = await client.post(url, content=file_bytes, headers=headers)
            if resp.status_code not in (200, 202):
                return None
            if resp.status_code == 200:
                return resp.json()
            op_loc = resp.headers.get("operation-location")
            if not op_loc:
                return None
            poll = await client.get(op_loc, headers={"Ocp-Apim-Subscription-Key": self.api_key})
            return poll.json() if poll.status_code == 200 else None


class TextractExpenseProvider:
    """Adapter stub/wrapper for Amazon Textract AnalyzeExpense (requires AWS credentials +
    boto3).
    """

    name = "aws_textract_expense"

    def is_available(self) -> bool:
        return bool(
            os.getenv("AWS_ACCESS_KEY_ID", "").strip()
            and os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
        )


class VeryfiReceiptProvider:
    """Adapter for Veryfi Receipt OCR API (requires Veryfi client credentials)."""

    name = "veryfi_receipt_api"
    ENDPOINT = "https://api.veryfi.com/api/v8/partner/documents"

    def __init__(self) -> None:
        self.client_id = os.getenv("VERYFI_CLIENT_ID", "").strip()
        self.username = os.getenv("VERYFI_USERNAME", "").strip()
        self.api_key = os.getenv("VERYFI_API_KEY", "").strip()

    def is_available(self) -> bool:
        return bool(self.client_id and self.username and self.api_key)

    async def process_document(
        self,
        file_bytes: bytes,
        *,
        filename: str = "receipt.png",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> dict[str, Any] | None:
        if not self.is_available():
            return None
        headers = {
            "CLIENT-ID": self.client_id,
            "AUTHORIZATION": f"apikey {self.username}:{self.api_key}",
            "Accept": "application/json",
        }
        payload = {
            "file_name": filename,
            "file_data": base64.b64encode(file_bytes).decode("ascii"),
        }
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
            resp = await client.post(self.ENDPOINT, json=payload, headers=headers)
            return resp.json() if resp.status_code in (200, 201) else None
