# Receipt OCR Implementation Roadmap

> **Note:** Follows existing project sequence; keeps Stage 2 (Canonical Merges) and Stage 3–4 (Receipt Learning) cleanly modularized.

## Phase 1: Real-Receipt Benchmark & Dataset Collection (1–2 weeks)
- **Actions:** Collect 30–50 real annotated receipts across kirana bill-books, supermarket thermal rolls, quick-commerce screenshots, and GST invoices (without synthetic substitutes).
- **Harness:** Extend `backend/benchmarks/` with a local OCR engine comparison runner measuring CER, WER, `₹` symbol accuracy, row recall, grand-total accuracy, and CPU latency/RAM.
- **Risk & Mitigation:** Do not commit PII-containing real receipts to public repos; store anonymized/redacted evaluation fixtures locally.

## Phase 2: Preprocessing & Quick-Commerce Fixes (1 week)
- **Changes (`preprocessing.py`, `document_classifier.py`, `spatial_parser.py`):**
  - Detect clean digital screenshots (high uniform white background, zero skew) and bypass unnecessary `deskew` / `CLAHE` / `glare` warnings.
  - Preserve color-channel ink masks to separate blue/red handwritten strokes from printed black text.
  - Ignore mobile status-bar headers (`2:51 • Em • Er...`) in merchant heuristics and fix `bill_summary` false positives when shipment items are present.
  - Add missing-quantity 3-token recovery (`Moong SabuT KG 130 130.00` → `1 kg`).

## Phase 3: `RapidOCR` Provider Integration & Head-to-Head Gate (1–2 weeks)
- **Changes (`backend/pyproject.toml`, `app/infra/ocr/providers.py`):**
  - Add `rapidocr-onnxruntime` (`Apache 2.0`) as `RapidOcrProvider` with module-level singleton model caching.
  - Split wide span boxes into word/column tokens and refine X-projection column clustering in `table_extractor.py`.
  - **Gate:** Promote `RapidOcrProvider` over `TesseractOcrProvider` / `ocrmac` only after Phase 1 benchmark proves superior CER/WER and `₹` accuracy on CPU.

## Phase 4: Receipt Learning & Temporary Image Lifecycle (Stage 3–4, 2 weeks)
- **Changes:** Implement `receipt_temp_artifacts` (`pending_deletion` + retryable cleanup worker), immutable original OCR baselines + stable line IDs, `receipt_corrections` (separating extraction errors from preference/classification/canonical corrections), and offline-evaluated `merchant_layout_profiles`.
- **Safeguards:** Never train on unconfirmed/discarded drafts; never weaken financial validation.

## Testing & Deployment Strategy
- **Automated Tests:** Mocked unit tests + real-fixture regression suite (`pytest`) with zero live paid API dependency.
- **Deployment:** Single CPU Docker image with pre-cached ONNX weights (~15–30 MB), `OMP_NUM_THREADS` tuning, and non-blocking `asyncio.to_thread` OCR execution.
