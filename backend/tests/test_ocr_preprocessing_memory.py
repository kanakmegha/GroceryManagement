"""Regression tests for memory-efficient early downscaling in OCR preprocessing."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app.infra.ocr.preprocessing import MAX_INITIAL_IMAGE_DIM, preprocess_receipt_image


def _make_receipt_jpeg(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(250, 250, 250))
    draw = ImageDraw.Draw(img)
    # Draw dark high-contrast receipt-like lines so IQA marks it readable
    for y in range(100, height - 100, max(40, height // 25)):
        draw.rectangle([80, y, width - 80, y + 12], fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_high_res_phone_photo_downscaled_early_to_1800px_preserving_aspect_ratio() -> None:
    raw_jpeg = _make_receipt_jpeg(4000, 3000)
    res = preprocess_receipt_image(raw_jpeg)

    with Image.open(io.BytesIO(res.original_png_bytes)) as orig_out:
        assert max(orig_out.size) == MAX_INITIAL_IMAGE_DIM
        assert orig_out.size == (1800, 1350)

    with Image.open(io.BytesIO(res.enhanced_png_bytes)) as enh_out:
        assert max(enh_out.size) <= MAX_INITIAL_IMAGE_DIM


def test_corrupted_image_raises_cleanly() -> None:
    with pytest.raises(Exception):
        preprocess_receipt_image(b"not-a-valid-image-bytes")
