# Receipt Intelligence & Layout-Aware Extraction Audit

**Date:** 2026-11-18  
**Scope:** Full audit of `backend/app/infra/ocr/` (`pipeline.py`, `providers.py`, `preprocessing.py`, `spatial_parser.py`, `validation.py`), `backend/app/modules/purchases/`, and `mobile/src/features/purchases/`.

---

## 1. Current Execution Path & Provider Status

1. **Upload Entry:** `POST /api/v1/purchases/ocr` (`backend/app/modules/purchases/router.py`) reads uploaded `file` bytes (10 MB cap) and invokes `extract_receipt(filename, content_type, data)` in `pipeline.py`.
2. **Stage A (PDF / Text):** `pymupdf` (`fitz`) extracts digital text + line bounding boxes (`OcrTokenBox`) for digital PDFs, or renders scanned PDF pages at 250 DPI (up to 10 pages).
3. **Stage B (Image IQA & OpenCV):** `preprocessing.py` computes resolution-normalized Laplacian blur, brightness, contrast, 4×4 illumination variance, HSV glare ratio, and skew angle. Conditionally applies perspective warp, deskewing (`0.6°–15°`), `upscale_1600`, morphological background division, and LAB L-channel CLAHE (`clipLimit=2.0`).
4. **Stage C (OCR Provider):**
   - **Active on macOS host:** `LocalVisionOcrProvider` (`ocrmac`, Apple Vision `VNRecognizeTextRequest`).
   - **Cross-platform Linux/Docker:** `TesseractOcrProvider` (`tesseract --psm 6 tsv`); binary is not installed on this macOS dev host, installed via `backend/Dockerfile` for Linux/Docker.
   - **Optional VLM / Cloud:** `HuggingFaceVlmProvider` (`RECEIPT_ENABLE_CLOUD_VLM=false`; live test on `Qwen/Qwen2.5-VL-7B-Instruct` returned `HTTP 400 model_not_supported` on current HF account); `AzureReceiptProvider`, `TextractExpenseProvider`, `VeryfiReceiptProvider` are unconfigured adapters.
5. **Stage D & E (Parsing & Validation):** `spatial_parser.py` clusters boxes into rows by y-center and parses lines via regexes, followed by deterministic reconciliation in `validation.py`.
6. **Persistence & Review:** `service.py` persists `Purchase` + `PurchaseItem` (`status='draft'`). Only explicit confirmation (`POST /confirm`) creates `InventoryLot`, `StockLedger`, and `FoodConsumption` records.

---

## 2. Why the Existing Implementation Fails on Dense Tabular Invoices & Digital Order Summaries

1. **No Document-Type Classification:**
   - Digital `bill_summary` screenshots (e.g., `MRP ₹330`, `Product discount ₹44`, `Item total ₹286`, `Handling charge ₹14`, `Bill total ₹300`) have **no product table**. The existing line parser attempts to extract any line with a trailing amount as a product or returns `0` lines without distinguishing a `bill_summary` from an extraction failure.
2. **Regex-Only Line Parsing Breaks on Multi-Column GST Tables:**
   - `_TRAILING_3COL_RE` assumes `<name> <qty> <rate> <total>`. Dense GST invoices have 8–12 columns (`HSN | Qty | MRP | Rate | Disc | Taxable | CGST %/Amt | SGST %/Amt | Cess | Total`), causing column values to collide or fail arithmetic matching.
   - No semantic header row detection (`HSN`, `MRP/RSP`, `Taxable Value`, `CGST`, `SGST`, `Cess`) or x-coordinate column assignment exists.
3. **No Multi-Document Attachment / Merge:**
   - `POST /api/v1/purchases/ocr` only accepts a single `file` and always creates a new draft, preventing users from combining a `bill_summary` screenshot and an `order_item_list` into one purchase draft.
4. **Limited Internationalisation:**
   - European decimal format (`12,50` / `1.234,56`), imperial units (`lb`, `oz`), and suffix currencies (`12.50 EUR`) are not normalized cleanly.
5. **No PII Redaction Layer:**
   - Full delivery addresses, phone numbers, and emails in uploaded screenshots can appear in `raw_text` and `raw_ocr_payload`.
