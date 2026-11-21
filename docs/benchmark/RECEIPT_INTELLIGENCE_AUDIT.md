# Receipt Intelligence & Purchase Accounting Audit

**Date:** 2026-11-18  
**Scope:** Runtime OCR audit (`pipeline.py`, `providers.py`, `preprocessing.py`, `spatial_parser.py`, `validation.py`), Swiggy Instamart vs failed receipt comparison, and architecture diagram.

---

## 1. Phase 1 — Runtime OCR Audit

| Component / Question | Actual Runtime Status (Verified) |
|---|---|
| Primary runtime OCR provider | `LocalVisionOcrProvider` (`apple_vision_ocrmac`, macOS Vision `VNRecognizeTextRequest`) |
| Provider on Swiggy Instamart receipt (`06600451...`) | `apple_vision_ocrmac` |
| Provider on failed `$` produce receipt (`0009fe25...`) | `apple_vision_ocrmac` |
| Is Apple Vision (`ocrmac`) used? | **Yes** (active on macOS host) |
| Is Tesseract used? | **No** (`tesseract` CLI is not installed on this macOS host; configured in `backend/Dockerfile` for Linux/Docker) |
| Is OpenCV preprocessing applied? | **Yes** (`upscale_1600`, `deskew`, `illumination_divide`, `clahe_lab_2.0` gated by IQA) |
| Is any LLM or VLM invoked for receipts? | **No** (`RECEIPT_ENABLE_CLOUD_VLM=false` by default) |
| Hugging Face `Qwen/Qwen2.5-VL-7B-Instruct` status | **Disabled / Unsupported on account** (live call returned `HTTP 400 model_not_supported`: provider not enabled on HF account) |
| Azure, Textract, Veryfi status | **Adapters only** (no credentials configured; never called) |
| When primary OCR fails/unavailable | Emits explicit `OCR_PROVIDER_UNAVAILABLE` error & warning; returns empty draft (`needs_review=True`), never mutates inventory |

### Runtime Architecture Diagram

```text
Upload (Image / PDF / Text)
  │
  ▼
[Stage A: Input & PDF] ── digital PDF ──► PyMuPDF text + boxes ──┐
  │                                                               │
  │ image / scanned PDF (250 DPI)                                 │
  ▼                                                               │
[Stage B: OpenCV IQA & Conditional Preprocessing]                 │
  ├─ unreadable (blank/blurry/tiny) ──► Reject w/ retake reason   │
  ▼                                                               │
[Stage C: Local OCR Provider]                                     │
  ├─ macOS: Apple Vision (ocrmac) ──► OcrTokenBox[]               │
  ├─ Linux/Docker: Tesseract TSV  ──► OcrTokenBox[]               │
  └─ None available ───────────────► Explicit OCR_PROVIDER_UNAVAILABLE
  │                                                               │
  ▼                                                               ▼
[Stage D: Spatial Row-Clustering & Universal Receipt Parser] ◄────┘
  │ (currency-agnostic: ₹/Rs/R$/$/€/£, weighted sub-lines, fees, taxes)
  ▼
[Stage E: Deterministic Financial Validation] (never mutates values)
  │
  ▼
[Stage F: Purchase & Item Classification (Downstream of OCR)]
  ├─ grocery    ──► InventoryLot + StockLedger + Price History + Recipes
  ├─ restaurant ──► FoodConsumption (meal_type, dining_mode) + Budget
  └─ mixed      ──► Eligible InventoryLot + FoodConsumption + Budget
```

---

## 2. Phase 2 — Swiggy Instamart vs Failed Receipt Comparison

| Metric / Field | Swiggy Instamart (`06600451...`) | Failed Produce Receipt (`0009fe25...`) |
|---|---|---|
| Dimensions | `787 x 1236` px | `823 x 1281` px |
| IQA Blur / Brightness / Contrast | `32.46` / `166.16` / `31.0` (`is_readable=True`) | `126.39` / `182.13` / `43.69` (`is_readable=True`) |
| Preprocessing Applied | `upscale_1600`, `clahe_lab_2.0` | `deskew(+0.7deg)`, `upscale_1600`, `clahe_lab_2.0` |
| OCR Engine & Raw Output Quality | `apple_vision_ocrmac` (readable, `R$` misreads for `Rs`) | `apple_vision_ocrmac` (**100% readable** `$4.66`, `0.778kg NET # $5.99/kg`) |
| Exact Failure Point (Before Fix) | Partial: `R$` leaked into names, `Dates` skipped due to `"date"` substring, fees parsed as items | **Complete 0-item failure**: `_CURRENCY_AMOUNT_RE` & `_TRAILING_AMOUNT_RE` only matched `₹`/`Rs`/`INR`, failing on every `$` price (`$4.66`, `$24.20`) |
| Extraction After Universal Fix | **7/7 items**, `Discount ₹18`, `Charges ₹21`, `Total ₹734.00`, `math_reconciled=True` | **12/12 items**, all `kg` weights + `$/kg` unit prices, `Discount $15.00`, `Total $24.20`, `currency=USD`, `math_reconciled=True` |
