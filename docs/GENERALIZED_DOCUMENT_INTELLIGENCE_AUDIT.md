# Generalized Receipt & Invoice Intelligence — Pipeline Failure Audit

## 1. Scope
End-to-end audit of the production extraction pipeline in `GroceryManagement-AgentBenchmark`:
- Upload & preprocessing (`backend/app/infra/ocr/preprocessing.py`)
- OCR providers & bounding boxes (`backend/app/infra/ocr/providers.py`)
- Document classification (`backend/app/infra/ocr/document_classifier.py`)
- Table & row detection, column mapping, description reconstruction (`backend/app/infra/ocr/table_extractor.py`)
- Financial & spatial parsing (`backend/app/infra/ocr/spatial_parser.py`)
- Consistency & arithmetic validation (`backend/app/infra/ocr/validation.py`)
- Privacy & PII protection (`backend/app/infra/ocr/privacy.py`)
- Draft persistence, API serialization & mobile review (`purchases/service.py`, `purchases/router.py`, `mobile/src/features/purchases/PurchasesScreen.tsx`)

---

## 2. Root Causes Identified Across Pipeline Stages

| Stage | Current Limitation / Failure Mode | Impact on Extraction |
|---|---|---|
| **A. Digital PDF Box Extraction (`preprocessing.py`)** | `page.get_text("dict")` joins all spans in a PDF `line` (`"".join(s["text"])`) into a single `OcrTokenBox` and ignores mixed digital+scanned pages when `combined_text` is non-empty. | Adjacent table columns on the same PDF line collapse into one wide box, destroying column x-coordinates; scanned pages in mixed PDFs are dropped. |
| **B. OCR Provider Metadata (`providers.py`)** | `LocalVisionOcrProvider` and `TesseractOcrProvider` leave `reading_order=0` and `language=None`; `TesseractOcrProvider` flattens `raw_text` with `" ".join(...)` instead of visual rows. | Downstream row/reading-order diagnostics lack deterministic token ordering and language context. |
| **C. Column Boundary Inference (`table_extractor.py`)** | Column splits use midpoints between header `x_center`s (`(prev.x_center + col.x_center) / 2`). No visual grid-line detection or data-cell x-projection refinement is used. | Wide left-aligned product names (e.g., extending past the header midpoint toward `Qty`) get sliced in half, misassigning trailing pack sizes or words into `Qty`/`HSN`. |
| **D. No Intermediate Table Representation (IR)** | `table_extractor.py` jumps directly from `OcrTokenBox` clusters to `ExtractedReceiptLine`, without `TableIR / TableRowIR / TableCellIR`. | Extraction errors (wrong column assignment, unmapped numeric columns, tax-summary rows mistaken for items) cannot be inspected or diagnosed cleanly. |
| **E. Multi-Section & Multi-Page Tables (`table_extractor.py`)** | A second table on the same page (e.g., an `HSN / Tax Summary` table below the item table) or a headerless continuation on page 2+ is either dropped or misparsed as product rows. | Phantom items appear from HSN tax summaries, or page 2+ items are silently lost. |
| **F. Financial & Tax Disambiguation (`table_extractor.py`, `validation.py`)** | Adjacent `CGST % | CGST Amt` sub-columns and `Discount % | Discount Amt` columns are not always disambiguated; `taxable == total` despite separate tax columns is not flagged; discontinuous serial numbers are not checked. | Tax percentages risk misinterpretation if headers are abbreviated; incomplete table extraction goes unflagged. |
| **G. Privacy (`privacy.py`)** | Covers email, phone, and labeled address lines, but misses personal name headers (`Name: ...`, `Billed To: ...`) and unnecessary UPI/UTR transaction IDs. | Personal names and payment transaction IDs can remain in stored `raw_text` or diagnostic logs. |
| **H. Mobile Draft Review (`PurchasesScreen.tsx`)** | Displays `document_type`, `order_id`, and `missing_fields`, but does not yet surface per-line `tax_paise` / `hsn_code` / `source_evidence` or structural table warnings cleanly. | Users cannot easily inspect why a line was flagged or see extracted tax/HSN evidence during review. |

---

## 3. Remediation Plan
1. **Word-level PDF extraction & mixed-PDF support** in `preprocessing.py` (`page.get_text("words")` + vector vertical/horizontal line extraction via `page.get_drawings()`, plus OpenCV grid-line detection for images).
2. **Reading order & visual-row `raw_text`** in `providers.py` (`reading_order`, `language`, line-grouped Tesseract `raw_text`).
3. **Structured Intermediate Representation (`table_ir.py`)**: `TableIR`, `TableColumnIR`, `TableRowIR`, `TableCellIR` preserving `document_id`, `page_number`, `table_id`, `row_id`, `column_id`, `cell_text`, `cell_bbox`, `associated_header`, `semantic_field`, `confidence`, `source_ocr_evidence`.
4. **General Layout-Aware Table Engine (`table_extractor.py`)**:
   - Combine header alignment, data-token x-projection gaps, left/right alignment heuristics (left-aligned text vs right-aligned numeric columns), and optional visual grid lines.
   - Support multi-line headers, `Rate% | Amt` adjacent sub-columns, wrapped multi-line descriptions, multi-page continuation (repeated headers & headerless continuation), and multi-section tables (distinguishing `item_table` from `tax_summary_table`).
5. **Consistency & Financial Checks (`validation.py`)**: Detect discontinuous `sr_no`, missing descriptions, `total == taxable` with non-zero tax columns, `subtotal == grand_total` with taxes/charges, and unmapped numeric columns.
6. **Enhanced Privacy (`privacy.py`)**: Redact personal names (`Name:`, `Billed To:`) and UPI/UTR transaction references alongside emails, phones, and addresses without touching product/financial lines.
7. **Unbiased Evaluation & Held-Out Layouts (`test_generalized_document_eval.py`)**: Evaluate across thermal, digital PDF, dense GST bordered, unbordered, multi-section, multi-page, reordered/abbreviated headers, international formats, restaurant/mixed, bill summary, multi-document merge, and held-out unseen layouts, reporting real vs. synthetic metrics separately.
