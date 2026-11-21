# Receipt OCR Benchmark Report (Current Baseline & Dataset Audit)

## 1. Available Dataset & Ground-Truth Inventory
An audit of the repository and `backend/dev_local.db` found:
- **Verified Text/Spatial Ground-Truth Regression Fixtures (6 receipts, 37 annotated line items):**
  1. `AGGARWAL TRADERS L011139` (10 rows, ₹2,260.00, section headings + `LAL MALKA` rate anomaly)
  2. `AGGARWAL TRADERS L012633` (10 rows, ₹3,459.00, clean grocery bill)
  3. `AGGARWAL TRADERS L015174` (13 rows, ₹5,319.00, handwritten markings, `DAL -=`, `Atem Qty: 28`, annotated `5119` over `5319`)
  4. `SANTUSHTI BHOJANAM` (2 rows, ₹320.00, restaurant bill)
  5. `Swiggy Instamart` (2 rows, ₹181.00, digital quick-commerce format)
  6. `14-Column GST Tax Invoice` (2 rows, ₹414.52 subtotal, dense multi-row header)
- **Live `dev_local.db` OCR Payloads (`apple_vision_ocrmac`, 9 distinct real uploads):**
  - Physical bills: `L015174`, `L012633`, `L011139`, `L006401` (₹2,564), `Aggarwal sweets L032680` (₹2,891), `Aggarwal sweets L009052` (₹2,405).
  - Quick-commerce mobile screenshots: `Screenshot_2026-11-18-14-31-33...jpg` (`84671e6a`), `IMG-20261118-WA0005.jpg` (`5a3ba615`), `WA_1794993792882.jpeg` (`632ba280`).
- **Missing Dataset Categories (Raw Image Files Not Retained on Disk):**
  Because original receipt images were discarded after upload (`attachments_json` stores only `sha256`), raw image binaries for supermarket thermal rolls, crumpled/shadowed photos, non-English/regional-script bills, and multi-page PDFs are **not** stored in the repo. Synthetic receipts are **not** substituted for real-world image evaluation.

## 2. Measured Baseline Results (Current Pipeline — Local Verified Runs)

### A. Deterministic Parser + Spatial/TableIR Pipeline on Ground-Truth Fixtures (6 receipts, 37 items)
Measured locally via `backend/tests/test_aggarwal_traders_ocr_regression.py` and `test_cross_column_and_row_reconstruction.py`:
- **Merchant Identification Accuracy:** `6 / 6` (`100%`)
- **Purchase Date Accuracy:** `5 / 5` dated fixtures (`100%`)
- **Item Row Recall:** `37 / 37` (`100%` on text/box fixtures after `L015174` grid-boundary fix)
- **Incorrect Heading Inclusions:** `0` (`DRY FRUITS=====>`, `DAL -=`, `Atem Qty: 28` excluded)
- **Grand Total / Subtotal Accuracy:** `6 / 6` (`100%`, with `L015174` `5319` vs `5119` flagged as `ANNOTATED_GRAND_TOTAL`)
- **Average Parsing Latency (excluding image OCR):** `< 15 ms` per receipt

### B. Raw `apple_vision_ocrmac` Engine Failures Observed on Real Uploads (`dev_local.db`)
- **Quick-Commerce Screenshots (`84671e6a`, `5a3ba615`, `632ba280`):**
  - **Currency Glyph Error Rate on `₹`:** **> 80%** of `₹` symbols were misread as `₴`, `7`, `2`, `#`, `$`, or `R` (e.g., `₹30` → `730`, `₹60` → `760`, `₹1077` → `21077`), corrupting line totals and grand totals.
  - **Merchant Accuracy:** `1 / 3` (`5a3ba615` extracted Android status bar `2:51 • Em • Er all 984` as merchant).
  - **Item Recall on `5a3ba615` & `632ba280`:** `0 / 1` (`0%`) because `"Bill Summary"` triggered `bill_summary` classification despite `"1 item in shipment"` being present.
- **Physical Bill `L015174` (`IMG-20261119-WA0018.jpg`):**
  - `ocrmac` captured all 13 lines in `raw_text`, but produced character errors (`ATY,` for `QTY`, `Atem Qty`, `CHIANDF`, `LOETA LAL`, `G. AL`) and wide line boxes.

### C. Candidate Engine Comparison Status
- **Installed & Measured Locally:** `apple_vision_ocrmac` (macOS only). `tesseract` is not installed (`None`).
- **Not Yet Benchmarked Locally (Pending Phase 1 Eval Harness + Real Image Collection):** `RapidOCR (ONNX)`, `PaddleOCR (PP-OCRv5/v6)`, `Donut (CORD)`. Published metrics are documented in `RECEIPT_OCR_TECHNOLOGY_COMPARISON.md` and will **not** be presented as local measurements.

## 3. Dataset Collection Plan for Fair Head-to-Head Evaluation
Before swapping OCR engines, collect and preserve **30–50 real annotated receipt images** (with human-verified JSON ground truth) across:
1. Local kirana / bill-book receipts (including handwritten marks & cross-outs)
2. Supermarket thermal rolls (long, faded, creased)
3. Quick-commerce app screenshots (Blinkit, Zepto, Swiggy Instamart, BigBasket)
4. Multi-column GST tax invoices (A4/PDF & camera photos)
5. Poor-quality photos (low light, shadows, perspective tilt)
6. Bilingual / regional-script receipts
