# Receipt OCR Current State Audit

## 1. Executive Summary
Contrary to the assumption that the backend runs **PP-OCRv6**, inspection of `backend/pyproject.toml` and `backend/app/infra/ocr/` confirms that **PaddleOCR / PP-OCRv6 is NOT installed or implemented**. The live backend currently executes:
1. **Primary OCR (macOS dev host only):** `LocalVisionOcrProvider` (`apple_vision_ocrmac`, `ocrmac>=1.0.1`).
2. **Fallback OCR (Linux/Docker):** `TesseractOcrProvider` (`tesseract` CLI TSV output — currently `None` / unavailable on this host).
3. **Optional Cloud VLM Escalation:** `HuggingFaceVlmProvider` (`Qwen/Qwen2.5-VL-7B-Instruct` via `router.huggingface.co`, disabled unless `RECEIPT_ENABLE_CLOUD_VLM=true` and `HF_TOKEN` are set).

## 2. Current End-to-End Architecture
- **Upload & API (`backend/app/modules/purchases/router.py`, `service.py`):**
  - `POST /api/v1/purchases/ocr` reads image/PDF/text bytes into memory (`up.read()`), computes `sha256` for 60-second duplicate deduplication, and calls `extract_receipt()`.
  - Original image binaries are **not persisted**; only `{filename, content_type, document_type, sha256}` is saved in `Purchase.attachments_json`.
  - `Purchase.raw_ocr_payload` stores PII-redacted `ExtractedReceipt.model_dump()` (raw token-level `OcrTokenBox` lists are discarded after parsing).
- **Stage A–E Pipeline (`backend/app/infra/ocr/pipeline.py`):**
  - **Stage A:** File size check (`10 MB`), PDF text/box extraction or 250 DPI rendering via PyMuPDF (`fitz`).
  - **Stage B (`preprocessing.py`):** EXIF transpose → 4-corner perspective warp → deskew (`0.6°–15°`) → upscale/downscale (`1600–2600px`) → morphological illumination normalization → LAB CLAHE (`2.0`) + morphological vertical grid line detection (`detect_table_vertical_lines`).
  - **Stage C (`providers.py`):** `LocalVisionOcrProvider` (`ocrmac`) or `TesseractOcrProvider` (`tesseract_tsv`).
  - **Stage D (`table_extractor.py`, `spatial_parser.py`):** Clusters `OcrTokenBox`es into rows, detects semantic headers (`TableIR`), runs regex/spatial fallback, and extracts merchant, date, receipt number, taxes, discounts, and totals.
  - **Stage E (`validation.py`):** Checks line math (`qty * rate == total`), 100x decimal anomalies, discontinuous serials, `INCOMPLETE_EXTRACTION`, `ANNOTATED_GRAND_TOTAL`, and grand total reconciliation (`±₹2.00`).
- **Catalog & Inventory (`catalog/classifier.py`, `canonical_resolver.py`, `purchases/service.py`, `inventory/service.py`):**
  - `clean_canonical_candidate_name` strips receipt serial/rate noise while preserving grades (`40 Chawal`) and distinct ingredients (`Chana Kala` vs `Chana Dal`).
  - Inventory lots (`InventoryLot`) and ledger entries (`StockLedger`) are created **only** upon explicit `POST /api/v1/purchases/drafts/{id}/confirm`.
  - User edits (`PATCH /api/v1/purchases/drafts/{id}`) overwrite draft items in-place without storing field-level correction diffs; only `ItemAlias` captures name mappings on confirmed pantry items.

## 3. Identified Weaknesses & Root Causes (Verified from `dev_local.db` & Code)

### A. Image-Quality & Preprocessing Failures
- **Digital Screenshot Over-Processing:** Clean mobile quick-commerce screenshots (`84671e6a`, `5a3ba615`, `632ba280` in `dev_local.db`) trigger `glare_ratio > 0.86` (white background > 240), unnecessary `deskew(+1.2deg)`, and `clahe_lab_2.0`.
- **Color Channel Loss on Handwritten/Marked Receipts:** Preprocessing converts images to grayscale, erasing color contrast between black dot-matrix/thermal print and blue/red pen markings (`L015174`).

### B. OCR Engine Failures (`apple_vision_ocrmac`)
- **Indian Rupee Symbol (`₹`) Misrecognition:** Verified in `dev_local.db` payloads `84671e6a`, `5a3ba615`, `632ba280`: `₹` is repeatedly misread as `₴` (`₴35`, `₴187.99`, `₴1601`), `7` (`730` for `₹30`, `760` for `₹60`, `725` for `₹25`), `2` (`21077` for `₹1077`), `#` (`#1641`), `$` (`$43`), or `R` (`R117.99`). Adding a leading `7` or `2` directly corrupts numeric totals.
- **Span-Level Box Granularity:** `ocrmac` emits line/phrase-level boxes rather than word-level boxes, causing wide tokens (`S. Description`, `1 LALCHAWAL`) to straddle column boundaries.
- **Platform Lock-in:** `ocrmac` runs only on macOS (`sys_platform == 'darwin'`). Linux/Docker deployments fall back to `TesseractOcrProvider`, which struggles on skewed/noisy phone photos.

### C. Document-Understanding Failures
- **Strikethrough MRP vs. Sale Price:** Quick-commerce screenshots (`84671e6a`: `Item Total ₴187.99 ₴117.99`, `Total Bill ₴213.08 R117.99`) display crossed-out MRP next to the actual paid price; OCR extracts both without strikethrough metadata.
- **Mobile Status-Bar & UI Chrome Leakage:** Android status bar `2:51 • Em • Er all 984` (`5a3ba615`) was extracted as `merchant_name`.
- **Bill Summary Misclassification:** `5a3ba615` and `632ba280` contained `Livon Hyaluron Shampoo` as a line item but were classified as `bill_summary` (`0` items) due to `"Bill Summary"` text.
- **Internal Grid Line Collapse (Fixed in `L015174`):** Previously, an internal `S.No. | Description` vertical line at `x=0.1317` collapsed `description` and dropped 12 of 13 rows.

### D. Product-Understanding Failures
- **Abbreviated / OCR-Garbled Names:** `CHIANDF` (`CHINI DF`), `LOETA LAL` (`LOBIYA LAL`), `TAKHROT` (`AKHROT`), `Matchhox` (`Matchbox`).
- **Missing Qty Before Unit:** `Moong SabuT KG 130 130.00` omits `1`, falling back to `1 pcs` instead of `1 kg`.
- **Historical Canonical Duplicates:** 73 `canonical_items` in `dev_local.db` include 12 duplicate clusters from pre-Stage-1 rate/serial leakage (Stage 1 now cleans new confirmations; historical merges await Stage 2).
