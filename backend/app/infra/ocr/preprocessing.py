"""Stage A & B: Input validation, PDF rendering, Image Quality Assessment (IQA), and adaptive OpenCV preprocessing."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.infra.ocr.schemas import OcrTokenBox, ReceiptQualityReport

MAX_RECEIPT_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_PDF_PAGES = 10
PDF_RENDER_DPI = 250


@dataclass
class PreprocessedImageResult:
    """Result of IQA and adaptive preprocessing for a single image/page."""

    enhanced_png_bytes: bytes
    original_png_bytes: bytes
    quality_report: ReceiptQualityReport
    warnings: list[str] = field(default_factory=list)


@dataclass
class PdfExtractionResult:
    """Result of Stage A PDF inspection (digital text blocks or rendered page images)."""

    is_digital: bool
    digital_text: str = ""
    digital_boxes: list[OcrTokenBox] = field(default_factory=list)
    page_images: list[bytes] = field(default_factory=list)
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)


def _order_quad_points(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as (top-left, top-right, bottom-right, bottom-left)."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _detect_receipt_quad(bgr: np.ndarray) -> np.ndarray | None:
    """Find a high-confidence 4-corner receipt boundary covering 25%..94% of frame."""
    h, w = bgr.shape[:2]
    frame_area = float(h * w)
    if frame_area <= 0:
        return None

    scale = min(1.0, 800.0 / max(h, w))
    small = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale < 1.0 else bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Combine Canny edges and Otsu foreground mask to handle dark/wood/patterned backgrounds
    edged = cv2.Canny(blurred, 50, 160)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]

    small_area = float(small.shape[0] * small.shape[1])
    for cnt in contours:
        area = cv2.contourArea(cnt)
        area_ratio = area / small_area
        if area_ratio < 0.25 or area_ratio > 0.94:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.025 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            pts = approx.reshape(4, 2).astype("float32") / scale
            return _order_quad_points(pts)
    return None


def _four_point_warp(bgr: np.ndarray, rect: np.ndarray) -> np.ndarray:
    """Apply perspective transform to flatten a 4-corner receipt."""
    (tl, tr, br, bl) = rect
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_w = max(int(round(max(width_a, width_b))), 100)

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_h = max(int(round(max(height_a, height_b))), 100)

    dst = np.array(
        [[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(
        bgr, matrix, (max_w, max_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _estimate_skew_angle(gray: np.ndarray) -> float:
    """Estimate dominant text skew angle in degrees (-45..45)."""
    h, w = gray.shape[:2]
    scale = min(1.0, 900.0 / max(h, w))
    small = cv2.resize(gray, (int(w * scale), int(h * scale))) if scale < 1.0 else gray
    inv = cv2.bitwise_not(small)
    thresh = cv2.adaptiveThreshold(
        inv, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, -10
    )
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 150:
        return 0.0
    # Sample up to 20k foreground pixels for speed
    if len(coords) > 20000:
        step = len(coords) // 20000
        coords = coords[::step]
    rect = cv2.minAreaRect(coords[:, ::-1].astype(np.float32))
    angle = rect[-1]
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90
    if abs(angle) > 20:
        return 0.0
    return float(round(angle, 2))


def _rotate_bound(bgr: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rotate image by angle_deg without clipping corners."""
    h, w = bgr.shape[:2]
    center = (w / 2.0, h / 2.0)
    m = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    return cv2.warpAffine(
        bgr, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _compute_iqa(bgr: np.ndarray) -> ReceiptQualityReport:
    """Compute resolution-normalized blur, brightness, illumination variance, glare, and skew."""
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # Normalize width to 1000px for resolution-consistent Laplacian variance
    norm_scale = 1000.0 / max(w, 1)
    norm_h = max(int(round(h * norm_scale)), 1)
    norm_gray = cv2.resize(gray, (1000, norm_h), interpolation=cv2.INTER_AREA)
    blur_score = float(cv2.Laplacian(norm_gray, cv2.CV_64F).var())

    brightness_mean = float(np.mean(gray))
    contrast_std = float(np.std(gray))

    # 4x4 grid brightness variance (detects uneven shadows/lighting)
    gh, gw = max(h // 4, 1), max(w // 4, 1)
    tile_means = [
        float(np.mean(gray[r * gh : (r + 1) * gh, c * gw : (c + 1) * gw]))
        for r in range(4)
        for c in range(4)
    ]
    illumination_variance = float(np.std(tile_means))

    # Glare detection in HSV (very high Value + very low Saturation)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    glare_mask = (hsv[:, :, 2] > 248) & (hsv[:, :, 1] < 25)
    glare_ratio = float(np.mean(glare_mask))

    skew_deg = _estimate_skew_angle(gray)

    retake_reasons: list[str] = []
    if min(w, h) < 200:
        retake_reasons.append(f"Image resolution is too low ({w}x{h}px); move closer or retake.")
    if contrast_std < 8.0:
        retake_reasons.append("Image appears blank or has almost no readable contrast.")
    elif blur_score < 12.0 or (blur_score < 22.0 and contrast_std < 26.0):
        retake_reasons.append(
            f"Image is too blurry to read reliably (sharpness={blur_score:.1f}); hold steady and refocus."
        )

    return ReceiptQualityReport(
        width=w,
        height=h,
        blur_score=round(blur_score, 2),
        brightness_mean=round(brightness_mean, 2),
        contrast_std=round(contrast_std, 2),
        illumination_variance=round(illumination_variance, 2),
        glare_ratio=round(glare_ratio, 4),
        estimated_skew_deg=skew_deg,
        is_readable=len(retake_reasons) == 0,
        retake_reasons=retake_reasons,
    )


def preprocess_receipt_image(image_bytes: bytes) -> PreprocessedImageResult:
    """Decode, assess quality, and conditionally preprocess a receipt image."""
    warnings: list[str] = []

    if len(image_bytes) > MAX_RECEIPT_BYTES:
        raise ValueError(f"Receipt file exceeds maximum size of {MAX_RECEIPT_BYTES // (1024 * 1024)} MB.")

    with Image.open(io.BytesIO(image_bytes)) as pil_img:
        pil_img = ImageOps.exif_transpose(pil_img)
        if pil_img.mode not in ("RGB", "L"):
            pil_img = pil_img.convert("RGB")
        rgb = np.array(pil_img.convert("RGB"))
        orig_buf = io.BytesIO()
        pil_img.save(orig_buf, format="PNG")
        original_png_bytes = orig_buf.getvalue()

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    report = _compute_iqa(bgr)

    if not report.is_readable:
        warnings.extend(report.retake_reasons)
        return PreprocessedImageResult(
            enhanced_png_bytes=original_png_bytes,
            original_png_bytes=original_png_bytes,
            quality_report=report,
            warnings=warnings,
        )

    if report.glare_ratio > 0.18 and report.brightness_mean < 225.0:
        warnings.append(
            f"High glare detected ({report.glare_ratio * 100:.0f}% of image); some text may need manual review."
        )

    working = bgr.copy()

    # 1. Perspective correction only when a clean 4-corner receipt boundary is found
    quad = _detect_receipt_quad(working)
    if quad is not None:
        warped = _four_point_warp(working, quad)
        if min(warped.shape[:2]) >= 180:
            working = warped
            report.perspective_corrected = True
            report.applied_steps.append("perspective_warp")

    # 2. Deskew only for mild/moderate tilt (0.6..15 deg)
    skew = _estimate_skew_angle(cv2.cvtColor(working, cv2.COLOR_BGR2GRAY))
    if 0.6 <= abs(skew) <= 15.0:
        working = _rotate_bound(working, skew)
        report.deskewed = True
        report.applied_steps.append(f"deskew({skew:+.1f}deg)")

    # 3. Resolution normalization (upscale tiny text, cap huge 12MP+ photos)
    wh, ww = working.shape[:2]
    max_dim = max(wh, ww)
    if max_dim < 1400:
        up_scale = 1600.0 / max_dim
        working = cv2.resize(
            working,
            (int(round(ww * up_scale)), int(round(wh * up_scale))),
            interpolation=cv2.INTER_CUBIC,
        )
        report.applied_steps.append("upscale_1600")
    elif max_dim > 2600:
        dn_scale = 2600.0 / max_dim
        working = cv2.resize(
            working,
            (int(round(ww * dn_scale)), int(round(wh * dn_scale))),
            interpolation=cv2.INTER_AREA,
        )
        report.applied_steps.append("downscale_2600")

    # 4. Illumination normalization (only when uneven shadows or dark lighting detected)
    if report.illumination_variance > 22.0 or report.brightness_mean < 95.0:
        lab = cv2.cvtColor(working, cv2.COLOR_BGR2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        # Estimate smooth illumination field using large morphological closing
        bg_kernel_size = max(31, (min(working.shape[:2]) // 20) | 1)
        bg_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (bg_kernel_size, bg_kernel_size))
        bg = cv2.morphologyEx(l_ch, cv2.MORPH_CLOSE, bg_kernel)
        bg_safe = np.maximum(bg, 1).astype(np.float32)
        norm_l = np.clip((l_ch.astype(np.float32) / bg_safe) * 235.0, 0, 255).astype(np.uint8)
        working = cv2.cvtColor(cv2.merge((norm_l, a_ch, b_ch)), cv2.COLOR_LAB2BGR)
        report.illumination_normalized = True
        report.applied_steps.append("illumination_divide")

    # 5. Gentle LAB L-channel CLAHE (only when contrast is low or shadows were present)
    if report.contrast_std < 48.0 or report.illumination_normalized:
        lab = cv2.cvtColor(working, cv2.COLOR_BGR2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l_eq = clahe.apply(l_ch)
        working = cv2.cvtColor(cv2.merge((l_eq, a_ch, b_ch)), cv2.COLOR_LAB2BGR)
        report.contrast_enhanced = True
        report.applied_steps.append("clahe_lab_2.0")

    # Encode enhanced image to PNG
    ok, enc = cv2.imencode(".png", working)
    enhanced_png_bytes = enc.tobytes() if ok else original_png_bytes

    return PreprocessedImageResult(
        enhanced_png_bytes=enhanced_png_bytes,
        original_png_bytes=original_png_bytes,
        quality_report=report,
        warnings=warnings,
    )


def detect_table_vertical_lines(png_bytes: bytes) -> list[float]:
    """Detect normalized [0..1] x-coordinates of strong vertical table borders in an image."""
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        return []
    h, w = img.shape[:2]
    if h < 80 or w < 80:
        return []
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    vert_len = max(25, h // 12)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, vert_len))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    col_sums = vert.sum(axis=0) / 255.0
    xs = [float(x) / w for x, count in enumerate(col_sums) if count >= h * 0.15]
    if not xs:
        return []
    # Cluster nearby pixel hits into distinct divider x-coordinates
    clustered: list[float] = [xs[0]]
    for x in xs[1:]:
        if x - clustered[-1] <= 0.012:
            clustered[-1] = (clustered[-1] + x) / 2.0
        else:
            clustered.append(x)
    return clustered


def extract_pdf_inputs(data: bytes) -> PdfExtractionResult:
    """Stage A: Extract digital text+boxes from PDF, or render scanned pages at 250 DPI."""
    warnings: list[str] = []
    if len(data) > MAX_RECEIPT_BYTES:
        raise ValueError(f"PDF exceeds maximum size of {MAX_RECEIPT_BYTES // (1024 * 1024)} MB.")

    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return PdfExtractionResult(
            is_digital=False,
            warnings=["PyMuPDF (fitz) is not installed; cannot extract PDF automatically."],
        )

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        return PdfExtractionResult(
            is_digital=False,
            warnings=[f"Malformed or unreadable PDF ({type(exc).__name__}); manual review required."],
        )

    try:
        total_pages = len(doc)
        if total_pages == 0:
            return PdfExtractionResult(
                is_digital=False,
                page_count=0,
                warnings=["PDF has 0 pages; manual review required."],
            )

        pages_to_process = min(total_pages, MAX_PDF_PAGES)
        if total_pages > MAX_PDF_PAGES:
            warnings.append(
                f"PDF has {total_pages} pages; processed the first {MAX_PDF_PAGES} pages."
            )

        digital_texts: list[str] = []
        digital_boxes: list[OcrTokenBox] = []
        page_images: list[bytes] = []

        for page_idx in range(pages_to_process):
            page = doc[page_idx]
            text = page.get_text("text").strip()
            if text:
                digital_texts.append(text)
                rect = page.rect
                pw = max(float(rect.width), 1.0)
                ph = max(float(rect.height), 1.0)
                # Extract word-level boxes so adjacent table columns preserve distinct x-coordinates
                words = page.get_text("words") or []
                words.sort(key=lambda item: (round(float(item[1]) / ph, 3), float(item[0]) / pw))
                for w_idx, w_item in enumerate(words):
                    if len(w_item) < 5:
                        continue
                    x0, y0, x1, y1, word_txt = (
                        float(w_item[0]),
                        float(w_item[1]),
                        float(w_item[2]),
                        float(w_item[3]),
                        str(w_item[4] or "").strip(),
                    )
                    if not word_txt:
                        continue
                    digital_boxes.append(
                        OcrTokenBox(
                            text=word_txt,
                            confidence=0.98,
                            x=max(0.0, min(1.0, x0 / pw)),
                            y=max(0.0, min(1.0, y0 / ph)),
                            w=max(0.001, min(1.0, (x1 - x0) / pw)),
                            h=max(0.001, min(1.0, (y1 - y0) / ph)),
                            page=page_idx + 1,
                            reading_order=len(digital_boxes),
                            language="en",
                        )
                    )
            else:
                pix = page.get_pixmap(dpi=PDF_RENDER_DPI)
                page_images.append(pix.tobytes("png"))

        combined_text = "\n".join(digital_texts).strip()
        if combined_text:
            return PdfExtractionResult(
                is_digital=True,
                digital_text=combined_text,
                digital_boxes=digital_boxes,
                page_images=page_images,
                page_count=pages_to_process,
                warnings=warnings,
            )

        return PdfExtractionResult(
            is_digital=False,
            page_images=page_images,
            page_count=pages_to_process,
            warnings=warnings,
        )
    finally:
        doc.close()
