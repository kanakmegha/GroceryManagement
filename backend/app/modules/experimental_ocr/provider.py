"""Standalone Experimental V10.6 Extraction Provider (PP-OCRv6_small + Qwen3-VL-30B-A3B + V10.6/V10.8)."""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

import cv2
import httpx
import numpy as np

from app.core.config import Settings
from app.modules.experimental_ocr.schemas import (
    ExperimentalDateEvidence,
    ExperimentalExtractionResult,
    ExperimentalFee,
    ExperimentalGstBreakdown,
    ExperimentalItem,
    ExperimentalMerchantEvidence,
    FieldEnvelope,
)

_MERCHANT_BLACKLIST_RE = re.compile(
    r"\b(?:tax\s+invoice|bill\s+of\s+supply|original\s+tax\s+invoice|order\s+details|"
    r"items?\s+in\s+order|on\s+the\s+way|bill\s+summary|saved\s+on|get\s+help|bill\s+to|ship\s+to)\b",
    re.IGNORECASE,
)
_PLATFORM_NAMES = {"zepto", "blinkit", "swiggy", "instamart", "bigbasket", "bbnow", "bb now", "dunzo", "amazon"}
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

PROMPT_V10_6 = (
    "Extract this grocery/retail invoice or order screenshot into strict JSON.\n"
    "CRITICAL INTEGRITY RULES:\n"
    "1. MERCHANT EVIDENCE: Only set observed_merchant if a seller/merchant/brand name is LITERALLY PRINTED on the image. "
    "Do NOT guess a platform (like Zepto/Blinkit/Swiggy) from UI colors or layout; if not printed, set observed_merchant=null "
    "and optionally put your guess in inferred_merchant with inference_evidence.\n"
    "2. STRIKETHROUGH & PRICE SEMANTICS: Numbers with a horizontal strikethrough line are original/MRP/waived prices, NOT paid prices. "
    "For each item, record mrp (if struck-out or labeled MRP), original_displayed_price, selling_price (active bold/non-struck price), "
    "and line_total (actual payable line amount). On mobile order screenshots, the active price shown next to an item (even if Qty > 1) "
    "is ALREADY the line_total — NEVER multiply it by quantity.\n"
    "3. FEE SEMANTICS: Do NOT include fees (Handling Fee, Processing Fee, Delivery Fee, Rain Fee, Surge Fee, Late Night Fee, Platform Fee) "
    "in items[]. Record every fee in fees[] with original_amount, actual_amount (0 if waived/₹0), discount_or_waiver, and charge_status "
    "('CHARGED' | 'WAIVED' | 'DISCOUNTED' | 'UNKNOWN').\n"
    "4. GST TABLE COLUMNS: For tabular GST invoices, carefully align columns per row: hsn, taxable_value, cgst_rate, cgst_amount, "
    "sgst_rate, sgst_amount, cess_rate, cess_amount, and line_total (Total Amt / TOTAL Value column, NOT Taxable Amt!). "
    "Do NOT put Taxable Amt or Other Charges into tax fields.\n"
    "5. NO FABRICATION: Use null if a value is absent or unreadable. Never invent unit_price on app screens.\n"
    "Schema:\n"
    "{\n"
    '  "document_type": str,\n'
    '  "merchant_evidence": {"observed_merchant": str|null, "inferred_merchant": str|null, "inference_evidence": str|null, "confidence": float},\n'
    '  "transaction": {"invoice_number": str|null, "purchase_date": str|null, "purchase_time": str|null, "currency": str|null},\n'
    '  "items": [{\n'
    '    "original_description": str, "quantity": float|null, "unit": str|null, "package_size": str|null,\n'
    '    "mrp": float|null, "original_displayed_price": float|null, "selling_price": float|null,\n'
    '    "unit_price": float|null, "item_discount": float|null, "line_total": float|null,\n'
    '    "price_presentation": "active_bold"|"strikethrough_pair"|"tabular_columns"|"unknown",\n'
    '    "gst": {"hsn": str|null, "taxable_value": float|null, "cgst_rate": float|null, "cgst_amount": float|null, '
    '"sgst_rate": float|null, "sgst_amount": float|null, "igst_rate": float|null, "igst_amount": float|null, "cess_rate": float|null, "cess_amount": float|null}\n'
    "  }],\n"
    '  "fees": [{"fee_type": str, "original_amount": float|null, "actual_amount": float|null, "discount_or_waiver": float|null, "charge_status": "CHARGED"|"WAIVED"|"DISCOUNTED"|"UNKNOWN"}],\n'
    '  "financial_summary": {"subtotal_before_tax": float|null, "subtotal_after_tax": float|null, "subtotal": float|null, "grand_total": float|null}\n'
    "}\n"
    "Return valid JSON only."
)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.\s]+", " ", (text or "").lower())).strip()


def _parse_num(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = re.sub(r"[^\d.-]", "", str(val))
    try:
        return float(s) if s and s not in {"-", "."} else None
    except ValueError:
        return None


def _classify_row(desc: str) -> str:
    dl = desc.lower().strip()
    if re.search(r"\bhandling\s*(?:fee|charge|charges)\b", dl):
        return "handling_fee"
    if re.search(r"\bplatform\s*(?:fee|charge)\b", dl):
        return "platform_fee"
    if re.search(r"\bdelivery\s*(?:fee|charge|charges)\b", dl):
        return "delivery_fee"
    if re.search(r"\bprocessing\s*(?:fee|charge)\b", dl):
        return "processing_fee"
    if re.search(r"\b(?:rain|surge|late\s*night)\s*(?:fee|charge)\b", dl):
        return "other_fee"
    return "grocery_product"


def parse_date_with_evidence(printed: str | None) -> ExperimentalDateEvidence:
    if not printed or not str(printed).strip():
        return ExperimentalDateEvidence(printed_date=None, parsed_date=None, assumed_convention="UNAVAILABLE", ambiguity_status="UNPARSEABLE", provenance="UNAVAILABLE")
    s = str(printed).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return ExperimentalDateEvidence(printed_date=s, parsed_date=s, assumed_convention="ISO_YYYY_MM_DD", confidence=0.95, ambiguity_status="UNAMBIGUOUS", provenance="OBSERVED")
    m_txt = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", s)
    if m_txt:
        d, mon_s, y = int(m_txt.group(1)), m_txt.group(2)[:3].lower(), int(m_txt.group(3))
        if mon_s in _MONTHS and 1 <= d <= 31:
            return ExperimentalDateEvidence(printed_date=s, parsed_date=f"{y:04d}-{_MONTHS[mon_s]:02d}-{d:02d}", assumed_convention="DD_MMM_YYYY", confidence=0.98, ambiguity_status="UNAMBIGUOUS", provenance="OBSERVED")
    m_num = re.fullmatch(r"(\d{1,2})([/-])(\d{1,2})\2(\d{2,4})", s)
    if m_num:
        a, sep, b, y_s = int(m_num.group(1)), m_num.group(2), int(m_num.group(3)), m_num.group(4)
        y = (2000 + int(y_s)) if len(y_s) == 2 else int(y_s)
        fmt = f"DD{sep}MM{sep}{'YY' if len(y_s) == 2 else 'YYYY'}"
        if a > 12 and 1 <= b <= 12:
            return ExperimentalDateEvidence(printed_date=s, parsed_date=f"{y:04d}-{b:02d}-{a:02d}", assumed_convention=fmt, confidence=0.95, ambiguity_status="UNAMBIGUOUS", provenance="OBSERVED")
        if b > 12 and 1 <= a <= 12:
            return ExperimentalDateEvidence(printed_date=s, parsed_date=f"{y:04d}-{a:02d}-{b:02d}", assumed_convention=f"MM{sep}DD{sep}YYYY", confidence=0.95, ambiguity_status="UNAMBIGUOUS", provenance="OBSERVED")
        return ExperimentalDateEvidence(printed_date=s, parsed_date=None, assumed_convention="AMBIGUOUS_DMY_OR_MDY", confidence=0.0, ambiguity_status="AMBIGUOUS_DAY_MONTH_SWAP", provenance="AMBIGUOUS")
    return ExperimentalDateEvidence(printed_date=s, parsed_date=None, assumed_convention="UNPARSEABLE", confidence=0.0, ambiguity_status="UNPARSEABLE", provenance="AMBIGUOUS")


class ExperimentalV10_6ExtractionProvider:
    """Isolated V10.6 extraction provider (PP-OCRv6_small + Qwen3-VL-30B-A3B-Instruct)."""

    _ppocr_engine: Any = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = (settings.EXPERIMENTAL_OCR_API_KEY or settings.HF_TOKEN).strip()
        self.base_url = settings.EXPERIMENTAL_OCR_BASE_URL.strip()
        self.model = settings.EXPERIMENTAL_OCR_MODEL.strip()
        self.timeout_s = settings.EXPERIMENTAL_OCR_TIMEOUT_SECONDS

    @classmethod
    def is_ppocr_available(cls) -> bool:
        try:
            import rapidocr  # noqa: F401
            return True
        except ImportError:
            return False

    @classmethod
    def _run_ppocr(cls, img_bytes: bytes) -> dict[str, Any]:
        t0 = time.perf_counter()
        if not cls.is_ppocr_available():
            return {"available": False, "boxes_count": 0, "raw_text": "", "latency_ms": 0.0}
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return {"available": True, "boxes_count": 0, "raw_text": "", "latency_ms": 0.0, "error": "decode_failed"}
        if cls._ppocr_engine is None:
            from rapidocr import OCRVersion, RapidOCR
            cls._ppocr_engine = RapidOCR(params={"Det.ocr_version": OCRVersion.PPOCRV6, "Rec.ocr_version": OCRVersion.PPOCRV6})
        res = cls._ppocr_engine(img)
        txts = getattr(res, "txts", None) or []
        ms = round((time.perf_counter() - t0) * 1000.0, 1)
        return {"available": True, "boxes_count": len(txts), "raw_text": "\n".join(str(t) for t in txts), "latency_ms": ms}

    async def extract(
        self,
        *,
        image_bytes: bytes,
        content_type: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> ExperimentalExtractionResult:
        t0 = time.perf_counter()
        if not self.settings.EXPERIMENTAL_OCR_ENABLED:
            return ExperimentalExtractionResult(
                status="failed",
                provider=self.settings.EXPERIMENTAL_OCR_PROVIDER,
                model=self.model,
                error="EXPERIMENTAL_OCR_DISABLED: Set EXPERIMENTAL_OCR_ENABLED=true to run experimental extraction.",
            )
        if not self.api_key or not self.base_url:
            return ExperimentalExtractionResult(
                status="failed",
                provider=self.settings.EXPERIMENTAL_OCR_PROVIDER,
                model=self.model,
                error="EXPERIMENTAL_OCR_MISCONFIGURED: Missing API key or base URL.",
            )

        ocr_summary = self._run_ppocr(image_bytes)
        raw_ocr_text = ocr_summary.get("raw_text", "")
        b64 = base64.b64encode(image_bytes).decode("ascii")
        mime = content_type if content_type.startswith("image/") else "image/jpeg"
        ocr_hint = (
            f"\n\nPP-OCRv6_small raw OCR text evidence ({ocr_summary.get('boxes_count', 0)} boxes):\n"
            f"{raw_ocr_text[:2500]}"
            if raw_ocr_text
            else "\n\nPP-OCRv6_small raw OCR text evidence: (none)"
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, transport=transport) as client:
                resp = await client.post(
                    self.base_url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "temperature": 0.0,
                        "max_tokens": 2400,
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": PROMPT_V10_6 + ocr_hint},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        ]}],
                    },
                )
        except httpx.TimeoutException:
            return ExperimentalExtractionResult(
                status="failed",
                provider=self.settings.EXPERIMENTAL_OCR_PROVIDER,
                model=self.model,
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 1),
                raw_ocr_summary={k: v for k, v in ocr_summary.items() if k != "raw_text"},
                error=f"EXPERIMENTAL_PROVIDER_TIMEOUT after {self.timeout_s}s",
            )
        except Exception as exc:
            return ExperimentalExtractionResult(
                status="failed",
                provider=self.settings.EXPERIMENTAL_OCR_PROVIDER,
                model=self.model,
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 1),
                raw_ocr_summary={k: v for k, v in ocr_summary.items() if k != "raw_text"},
                error=f"EXPERIMENTAL_PROVIDER_ERROR: {type(exc).__name__}: {exc}",
            )

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if resp.status_code != 200:
            return ExperimentalExtractionResult(
                status="failed",
                provider=self.settings.EXPERIMENTAL_OCR_PROVIDER,
                model=self.model,
                latency_ms=latency_ms,
                raw_ocr_summary={k: v for k, v in ocr_summary.items() if k != "raw_text"},
                error=f"EXPERIMENTAL_HTTP_{resp.status_code}",
            )

        try:
            body = resp.json()
            usage = body.get("usage") or {}
            content = body["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE | re.DOTALL).strip()
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("Model JSON root is not an object")
        except Exception as exc:
            return ExperimentalExtractionResult(
                status="failed",
                provider=self.settings.EXPERIMENTAL_OCR_PROVIDER,
                model=self.model,
                latency_ms=latency_ms,
                raw_ocr_summary={k: v for k, v in ocr_summary.items() if k != "raw_text"},
                error=f"EXPERIMENTAL_MALFORMED_RESPONSE: {type(exc).__name__}: {exc}",
            )

        return self._apply_v10_6_hybrid_validation(parsed, raw_ocr_text, ocr_summary, usage, latency_ms)

    def _apply_v10_6_hybrid_validation(
        self,
        parsed: dict[str, Any],
        raw_ocr_text: str,
        ocr_summary: dict[str, Any],
        usage: dict[str, Any],
        latency_ms: float,
    ) -> ExperimentalExtractionResult:
        warnings: list[str] = []
        doc_type = str(parsed.get("document_type") or "").upper()
        is_app_screen = doc_type in {"QUICK_COMMERCE_ORDER_SCREEN", "ORDER_DETAILS_SCREEN", "ORDER_SUMMARY_SCREEN"} or any(
            k in raw_ocr_text.lower() for k in ("order details", "items in order", "on the way", "order again")
        )

        # 1. Merchant Evidence
        me = parsed.get("merchant_evidence") or {}
        obs = me.get("observed_merchant")
        inf = me.get("inferred_merchant")
        ev = me.get("inference_evidence")
        conf = float(me.get("confidence") or 0.0)
        ocr_norm = _norm(raw_ocr_text)
        if obs:
            obs_norm = _norm(obs)
            toks = [w for w in obs_norm.split() if len(w) >= 3]
            ratio = (sum(1 for w in toks if w in ocr_norm) / len(toks)) if toks and ocr_norm else 1.0
            if obs_norm in _PLATFORM_NAMES and ratio < 0.5:
                inf = inf or obs
                ev = ev or "demoted_from_observed_not_in_ocr"
                obs = None
                conf = min(conf, 0.4)
            elif _MERCHANT_BLACKLIST_RE.search(obs):
                obs = None
        merchant_ev = ExperimentalMerchantEvidence(
            observed_merchant=obs,
            inferred_merchant=inf,
            inference_evidence=ev,
            confidence=round(conf, 2),
            unavailable_merchant=obs is None,
            provenance="OBSERVED" if obs else ("INFERRED" if inf else "UNAVAILABLE"),
        )

        # 2. Date Evidence
        tx = parsed.get("transaction") or {}
        date_ev = parse_date_with_evidence(tx.get("purchase_date"))

        # 3. Fees & Reclassification
        fees_out: list[ExperimentalFee] = []
        for f in parsed.get("fees") or []:
            orig = _parse_num(f.get("original_amount"))
            act = _parse_num(f.get("actual_amount"))
            disc = _parse_num(f.get("discount_or_waiver"))
            st = str(f.get("charge_status") or "UNKNOWN").upper()
            if orig is not None and act is not None:
                if act == 0.0 and orig > 0.0:
                    st, disc = "WAIVED", orig
                elif 0.0 < act < orig:
                    st, disc = "DISCOUNTED", round(orig - act, 2)
                elif act > 0.0:
                    st = "CHARGED"
            elif act is not None and act > 0.0:
                st = "CHARGED"
            fees_out.append(ExperimentalFee(fee_type=str(f.get("fee_type") or "fee"), original_amount=orig, actual_amount=act, discount_or_waiver=disc, charge_status=st))  # type: ignore[arg-type]

        # 4. Items + Price Semantics + GST
        fs = parsed.get("financial_summary") or {}
        fs_gt = _parse_num(fs.get("grand_total"))
        items_out: list[ExperimentalItem] = []
        for raw_it in parsed.get("items") or []:
            desc = str(raw_it.get("original_description") or "").strip()
            if not desc:
                continue
            cls = _classify_row(desc)
            if cls != "grocery_product":
                amt = _parse_num(raw_it.get("line_total")) or _parse_num(raw_it.get("selling_price"))
                fees_out.append(ExperimentalFee(fee_type=cls, original_amount=amt, actual_amount=amt, discount_or_waiver=0.0, charge_status="CHARGED" if (amt and amt > 0) else "UNKNOWN"))
                continue

            qty = _parse_num(raw_it.get("quantity"))
            mrp = _parse_num(raw_it.get("mrp"))
            sell = _parse_num(raw_it.get("selling_price"))
            lt = _parse_num(raw_it.get("line_total"))
            up = _parse_num(raw_it.get("unit_price"))
            disc = _parse_num(raw_it.get("item_discount"))
            if disc == 0.0:
                disc = None

            if is_app_screen and lt is not None and sell is not None:
                if mrp is not None and abs(lt - mrp) <= 0.05 and sell < mrp:
                    lt = sell
                    warnings.append(f"Reverted strikethrough MRP to selling_price on '{desc[:30]}'")
                elif qty is not None and qty > 1.0 and abs(lt - sell * qty) <= 0.05 and (fs_gt is None or lt > fs_gt or sell < lt):
                    lt = sell
                    warnings.append(f"Reverted VLM qty*selling_price multiplication on '{desc[:30]}'")

            up_prov: Any = "OBSERVED" if up is not None else "UNAVAILABLE"
            up_val = up
            if is_app_screen and up is not None:
                up_prov = "DERIVED"
                up_val = round(lt / qty, 2) if (lt is not None and qty and qty > 0) else None
            if is_app_screen:
                disc = None

            g = raw_it.get("gst") or {}
            cgst_a, sgst_a, igst_a, cess_a = _parse_num(g.get("cgst_amount")), _parse_num(g.get("sgst_amount")), _parse_num(g.get("igst_amount")), _parse_num(g.get("cess_amount"))
            cgst_r, sgst_r, igst_r, cess_r = _parse_num(g.get("cgst_rate")), _parse_num(g.get("sgst_rate")), _parse_num(g.get("igst_rate")), _parse_num(g.get("cess_rate"))
            tax_val = _parse_num(g.get("taxable_value"))
            obs_tax = round((cgst_a or 0.0) + (sgst_a or 0.0) + (igst_a or 0.0) + (cess_a or 0.0), 2) if any(x is not None for x in (cgst_a, sgst_a, igst_a, cess_a)) else None
            drv_tax = round(tax_val * ((cgst_r or 0.0) + (sgst_r or 0.0) + (igst_r or 0.0) + (cess_r or 0.0)) / 100.0, 2) if (tax_val is not None and any(x is not None for x in (cgst_r, sgst_r, igst_r))) else None
            tax_prov: Any = "OBSERVED" if obs_tax is not None else ("DERIVED" if drv_tax is not None else "UNAVAILABLE")

            items_out.append(ExperimentalItem(
                line_index=len(items_out) + 1,
                product_name=FieldEnvelope(value=desc, provenance="OBSERVED", confidence=0.9, review_status="ACCEPTED"),
                quantity=FieldEnvelope(value=qty, provenance="OBSERVED" if qty is not None else "UNAVAILABLE", confidence=0.9 if qty is not None else 0.0, review_status="ACCEPTED" if qty is not None else "NEEDS_REVIEW"),
                unit_of_measure=FieldEnvelope(value=raw_it.get("unit"), provenance="OBSERVED" if raw_it.get("unit") else "UNAVAILABLE", confidence=0.85 if raw_it.get("unit") else 0.0),
                package_size=FieldEnvelope(value=raw_it.get("package_size"), provenance="OBSERVED" if raw_it.get("package_size") else "UNAVAILABLE", confidence=0.85 if raw_it.get("package_size") else 0.0),
                unit_price=FieldEnvelope(value=up_val, provenance=up_prov, source_text_or_evidence_ref="line_total / quantity" if up_prov == "DERIVED" else None, confidence=0.85 if up_val is not None else 0.0, review_status="NEEDS_REVIEW" if up_prov == "DERIVED" else "ACCEPTED"),
                mrp=FieldEnvelope(value=mrp, provenance="OBSERVED" if mrp is not None else "UNAVAILABLE", confidence=0.85 if mrp is not None else 0.0),
                selling_price=FieldEnvelope(value=sell, provenance="OBSERVED" if sell is not None else "UNAVAILABLE", confidence=0.9 if sell is not None else 0.0),
                line_total=FieldEnvelope(value=lt, provenance="OBSERVED" if lt is not None else "UNAVAILABLE", confidence=0.9 if lt is not None else 0.0, review_status="ACCEPTED" if lt is not None else "NEEDS_REVIEW"),
                item_discount=FieldEnvelope(value=disc, provenance="OBSERVED" if disc is not None else "UNAVAILABLE", confidence=0.8 if disc is not None else 0.0),
                gst=ExperimentalGstBreakdown(
                    hsn=g.get("hsn"), taxable_value=tax_val,
                    cgst_rate=cgst_r, cgst_amount=cgst_a, sgst_rate=sgst_r, sgst_amount=sgst_a,
                    igst_rate=igst_r, igst_amount=igst_a, cess_rate=cess_r, cess_amount=cess_a,
                    observed_tax_total=obs_tax, derived_tax_total=drv_tax, tax_provenance=tax_prov,
                ),
                price_presentation=str(raw_it.get("price_presentation") or "unknown"),
            ))

        sub_bt = _parse_num(fs.get("subtotal_before_tax"))
        sub_at = _parse_num(fs.get("subtotal_after_tax")) or _parse_num(fs.get("subtotal"))
        return ExperimentalExtractionResult(
            status="completed",
            provider=self.settings.EXPERIMENTAL_OCR_PROVIDER,
            model=self.model,
            latency_ms=latency_ms,
            token_usage=usage,
            raw_ocr_summary={k: v for k, v in ocr_summary.items() if k != "raw_text"},
            merchant_evidence=merchant_ev,
            date_evidence=date_ev,
            items=items_out,
            fees=fees_out,
            subtotal_before_tax=FieldEnvelope(value=sub_bt, provenance="OBSERVED" if sub_bt is not None else "UNAVAILABLE", confidence=0.85 if sub_bt is not None else 0.0),
            subtotal_after_tax=FieldEnvelope(value=sub_at, provenance="OBSERVED" if sub_at is not None else "UNAVAILABLE", confidence=0.85 if sub_at is not None else 0.0),
            grand_total=FieldEnvelope(value=fs_gt, provenance="OBSERVED" if fs_gt is not None else "UNAVAILABLE", confidence=0.95 if fs_gt is not None else 0.0, review_status="ACCEPTED" if fs_gt is not None else "NEEDS_REVIEW"),
            warnings=warnings,
        )
