# Receipt OCR & Extraction Technology Research and Architecture Recommendation

**Date:** 2026-11-18  
**Project:** GroceryManagement-AgentBenchmark (`mobile` Expo SDK 58 + `backend` FastAPI)  
**Scope:** Universal receipt extraction across mobile photos, angled/shadowed/glare/blurred thermal receipts, digital/scanned/multi-page PDFs, and Indian retail/restaurant/kirana/GST formats (INR, CGST, SGST, IGST).

---

## 1. Why the Existing Implementation Failed

Inspection of `backend/app/infra/ocr/pipeline.py` and `backend/app/modules/purchases/service.py` revealed three concrete root causes:

1. **Missing OCR Engine Binary:** `pipeline.py` relied exclusively on `shutil.which("tesseract")`, which is not installed (`tesseract not found`). Every uploaded image returned `confidence=0.0`, `lines=[]`, and `"tesseract CLI is not installed on PATH"`.
2. **No Image Quality Analysis or Geometric/Photometric Preprocessing:** Even with Tesseract installed, `_run_tesseract_on_image_bytes` only resized images to `max_dim=2400` and ran `tesseract --psm 6` with zero perspective correction, illumination normalization, shadow/glare detection, or blur rejection.
3. **Schema Field Mismatch in `create_ocr_draft`:** `ExtractedReceipt` returned `lines`, `receipt_date`, and `grand_total_paise`, whereas `service.py` read `items`, `purchased_on`, and `total_amount_paise`, silently dropping extracted line items and totals even for text/PDF receipts.

---

## 2. Cloud Receipt Extraction APIs Comparison

| Provider | Model / Endpoint | Pricing (USD) | Free Tier | Line-Item & Wrapped Text | Indian INR / CGST / SGST / IGST | Thermal & Mobile Photos | Latency | Privacy / Cloud Upload |
|---|---|---|---|---|---|---|---|---|
| **Azure AI Document Intelligence** | `prebuilt-receipt` / `prebuilt-invoice` | **$10.00 / 1,000 pages** ($0.01/page); Custom: $30/1,000 pages | 500 pages/mo (F0) | Good on `prebuilt-invoice`; `prebuilt-receipt` struggles on dense/wrapped tables | Generic `Tax` / `Total`; CGST/SGST/IGST & GSTIN often require `prebuilt-invoice` + post-processing or Custom model | Good, but severe thermal fade & angle degrade line items without preprocessing | ~1.5–4s | Cloud upload to Azure region |
| **Amazon Textract** | `AnalyzeExpense` | **$10.00 / 1,000 pages** (first 1M/mo), $8/1,000 above 1M | 100 pages/mo (3-mo AWS Free Tier) | `EXPENSE_ROW` groups items/qty/price; wrapped thermal lines can merge/misalign | Extracts INR & tax totals; CGST/SGST split needs post-processing on `SUMMARY_FIELDS` | Sensitive to faded thermal ink (`3` vs `8`) & shadows without preprocessing | ~2–4s | Cloud upload to AWS region |
| **Veryfi Receipt OCR API** | `/api/v8/partner/documents` | **$500/mo** starter minimum (6,250 receipts = $0.08/receipt) | **100 docs/mo** ($0) | Purpose-built for POS receipts, wrapped lines, and handwritten totals | Native multi-currency & `tax_lines` for GST/CGST/SGST/IGST + GSTIN | Strong on mobile photos & thermal paper | ~3–6s (from India) | Cloud upload (US/EU) |
| **Google Cloud Document AI** | Expense Parser | **$0.10 / document** (up to 10 pages = $100/1,000 docs) | GCP trial credits | Strong header extraction (95–98% clean); multi-column line items need validation | Supports INR & international tax lines; 10x cost of Azure/Textract | Good OCR backbone, drops to ~80s on degraded thermal scans | ~2–5s | Cloud upload to GCP region |

### Sources (Cloud APIs)
- Azure Document Intelligence Pricing & Models: Microsoft Learn (`learn.microsoft.com/en-us/azure/ai-services/document-intelligence/`)
- Amazon Textract `AnalyzeExpense` Pricing & Best Practices: AWS Docs (`aws.amazon.com/textract/pricing/`)
- Google Cloud Document AI Pricing & Expense Parser: Google Cloud Docs (`cloud.google.com/document-ai/pricing`)
- Veryfi Pricing & Global Tax Support: Veryfi Docs (`veryfi.com/pricing/`, `docs.veryfi.com`)

---

## 3. Open-Source OCR & Vision-Language Models Comparison

| Engine / Model | Architecture | Cost / License | CPU / Local Feasibility | Line-Item & Layout Handling | Thermal & Angled Photos | Indian / Multilingual |
|---|---|---|---|---|---|---|
| **PaddleOCR (PP-OCRv4 + PP-Structure)** | DBNet++ + SVTR + Table Structure | Apache 2.0 ($0) | Fast on CPU (~15 pages/min) & GPU; `onnxruntime` lacks wheel on macOS `x86_64` | **Best open-source** for dense multi-column receipts when paired with spatial clustering | Strong on thermal print; benefits from perspective warp + CLAHE | English, Hindi/Devanagari, Tamil, etc. |
| **Apple Vision (`VNRecognizeTextRequest` via `ocrmac`)** | On-device macOS Neural/CPU OCR | Built into macOS ($0) | **Very fast (<0.5s/image)** on local macOS dev host | Returns line bounding boxes `(x, y, w, h)` + confidence for spatial row clustering | Strong on tilted/low-contrast text when paired with OpenCV preprocessing | Multi-script + INR symbols |
| **Surya OCR** | Vision Transformer line/layout detector | GPL-3.0 / Model weights | Slow on CPU (~1.5–3 pages/min, 5+ GB RAM); GPU recommended | Excellent layout & reading order | Very robust on skewed/noisy scans, can over-segment tight thermal lines | 90+ languages |
| **docTR** | ResNet/MobileNet + CRNN/SAR | Apache 2.0 ($0) | Moderate (3–4 GB RAM) | Clean word/line boxes; weaker on warped mobile photos without fine-tuning | Drops on curved/angled thermal photos | Primarily Latin scripts |
| **Tesseract v5** | LSTM line OCR | Apache 2.0 ($0) | Very fast (~20–30 pages/min), <500 MB RAM | **Poor on receipts**: no native column/table awareness; jumbles multi-column rows | Degrades sharply on shadows, skew, and faded thermal paper | Multi-language packs |
| **Qwen2.5-VL (7B / 72B Instruct)** | Dynamic-resolution ViT + LLM | Apache 2.0 / HF Router (`~$0.10–$1.00 / 1M tokens`) | 7B/72B via HuggingFace Inference Router (`router.huggingface.co/v1`) or GPU | **Highest zero-shot semantic accuracy** for wrapped item names, CGST/SGST, rounding | Robust to shadows, glare, perspective, and crumpled thermal receipts | Strong multilingual & Indian formats |
| **Gemini 2.5 Flash** | Native Multimodal VLM | Paid API (~$0.0003–$0.001/receipt) | Cloud API only | Native JSON schema enforcement; handles wrapped descriptions & messy layouts | Top-tier zero-shot robustness on mobile photos | Strong Indian & multilingual |

### Sources (Open-Source & VLMs)
- PaddleOCR / PP-OCRv4: `github.com/PaddlePaddle/PaddleOCR`
- Qwen2.5-VL Dynamic Resolution & CORD/SROIE Benchmarks: Bai et al., *Qwen2.5-VL Technical Report* (arXiv:2502.13923)
- CORD Dataset: Park et al., *CORD: A Consolidated Receipt Dataset for Post-OCR Parsing* (NeurIPS Workshop 2019)
- SROIE Benchmark: Huang et al., *ICDAR2019 Competition on Scanned Receipt OCR and Information Extraction*

---

## 4. Image Preprocessing Research Findings

Empirical evaluations show that **always-on aggressive binarization hurts modern OCR and VLMs** by stripping anti-aliasing edges and turning thermal paper grain into dark blobs. Therefore, our pipeline uses **Image Quality Assessment (IQA)-gated OpenCV preprocessing**:

1. **Orientation & Aspect Check:** Respect EXIF orientation (`ImageOps.exif_transpose`) and validate resolution (`min_dim >= 320px`).
2. **Quality Metrics (IQA):**
   - **Blur / Sharpness:** Variance of Laplacian (`cv2.Laplacian(gray, cv2.CV_64F).var()`). Images below threshold (`< 35.0`) are flagged as blurry; extremely blank/flat images (`< 8.0` or low contrast std `< 10.0`) are rejected with a retake prompt rather than hallucinating items.
   - **Illumination Unevenness & Shadows:** Grid-based brightness standard deviation across 4×4 tiles; triggers background division + gentle LAB L-channel CLAHE (`clipLimit=2.0, tileGridSize=(8,8)`).
   - **Glare Detection:** Percentage of near-saturated low-saturation pixels (`V > 248` and `S < 25`); warns user when glare exceeds 12% of receipt area.
3. **Geometric Rectification:**
   - Edge + morphological contour detection for 4-corner receipt boundaries covering 20%–95% of frame -> 4-point perspective transform (`cv2.getPerspectiveTransform`).
   - Deskewing via minimum-area text bounding box angle when tilt is between `1.5°` and `15°`.
4. **Original Preservation:** Original uploaded bytes are preserved untouched; enhanced grayscale/RGB PNG is passed to OCR.

---

## 5. Recommended Hybrid Architecture & Justification

For **GroceryManagement-AgentBenchmark** (budget-conscious household app, INR + GST focus, FastAPI backend, `HF_TOKEN` already configured, macOS dev host + Linux Docker deployment):

- **Stage A (Input & PDF):** Validate magic bytes & 10 MB limit. Extract embedded text from digital PDFs via `pymupdf` (`fitz`); render scanned/image-only PDF pages at 250 DPI.
- **Stage B (IQA + Adaptive OpenCV Preprocessing):** Measure blur, brightness, illumination variance, glare, and skew; apply only needed OpenCV corrections; reject unreadable images early with actionable retake reasons.
- **Stage C (Hybrid Multi-Engine OCR):**
  1. **Primary Local Engine ($0, offline, <1s):** `LocalVisionOcrProvider` (`ocrmac` on macOS) or `TesseractOcrProvider` (Linux/Docker fallback) returning **bounding-boxed text lines + confidences**.
  2. **Spatial Row-Clustering & Indian Receipt Parser:** Clusters boxes into rows using y-overlap and column x-gaps, handles wrapped multi-line item names, and extracts merchant, date, bill number, line items, CGST/SGST/IGST, discounts, rounding, and grand total.
  3. **Selective VLM Escalation (Optional, gated by `RECEIPT_ENABLE_CLOUD_VLM=true`):** Only when local extraction has low confidence (`< 0.65`), zero lines, or math reconciliation failure, escalate the preprocessed image to `Qwen/Qwen2.5-VL-7B-Instruct` via Hugging Face Router (`HF_TOKEN`).
  4. **Pluggable Cloud Adapters:** Clean `ReceiptExtractionProvider` adapters implemented for Azure Document Intelligence, Amazon Textract, and Veryfi (inactive unless explicitly configured with credentials).
- **Stage D & E (Deterministic Validation):** Reconcile `qty * unit_price == line_total`, `sum(lines) - discount == subtotal`, and `subtotal + taxes + rounding == grand_total`; detect duplicates and 100x decimal errors; **never silently mutate values**.
- **Stage F (Human Review):** Persist `draft` purchase with `warnings`, `math_reconciled`, and editable line items; inventory lots are created **only** on explicit user confirmation.

### Expected Cost Model
- **Default Local Hybrid (OpenCV + Local OCR + Spatial Parser):** **$0.00 / receipt**.
- **Optional HF VLM Fallback (`Qwen2.5-VL-7B-Instruct`, ~15–25% hard receipts only):** **< $0.001 / escalated receipt** (within HF free/PRO monthly credits).
- **Optional Enterprise Cloud (Azure/Textract):** **$0.01 / page** ($10 / 1,000 pages) if enabled later.
