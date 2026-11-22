"""Pluggable OCR and Vision-Language receipt extraction providers."""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
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

        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            tmp.write(png_bytes)
            tmp.flush()
            proc = subprocess.run(
                [tesseract_bin, tmp.name, "stdout", "--psm", "6", "tsv"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
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
