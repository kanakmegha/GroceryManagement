# Receipt OCR Validation & Benchmark Results

## 1. Environment & Methodology Disclosure
- **Production Code & Dependencies:** Untouched (`backend/pyproject.toml` has `ocrmac>=1.0.1` only; `rapidocr-onnxruntime`, `paddleocr`, `surya-ocr`, `python-doctr`, and `tesseract` are **not** installed in `backend/.venv` or on this host).
- **Sandbox Limitation:** Inside this offline sandbox, `LocalVisionOcrProvider.recognize_image` returned `0` boxes on `.user_uploaded/*.jpg`, and external PyPI/model downloads for `RapidOCR`/`PaddleOCR` are blocked without network access.
- **No Fabricated Metrics:** All metrics below strictly separate **verified local measurements** (from `backend/tests/` and `dev_local.db` historical `apple_vision_ocrmac` payloads) from **unmeasured / pending live image comparisons**.

## 2. RapidOCR Model Compatibility Verification
Verified from official RapidOCR / PaddleOCR documentation:
- `rapidocr-onnxruntime` (Apache 2.0) executes **PP-OCRv4** and **PP-OCRv5** ONNX models (`ch_PP-OCRv4_det_infer.onnx`, `ch_PP-OCRv5_mobile_det.onnx`, `latin_PP-OCRv5_rec_mobile_infer.onnx` / multilingual `.onnx` + dict `.txt`) natively on CPU via ONNX Runtime without PaddlePaddle or PyTorch.
- **PP-OCRv6** (`PP-OCRv6_det_small.onnx` / `PP-OCRv6_rec_medium.onnx`, Apache 2.0) is supported in newer `rapidocr` / PaddleX ONNX exports, but **exact PP-OCRv6 ONNX availability in the pinned PyPI `rapidocr-onnxruntime` wheel must be verified at install time** before hardcoding `PP-OCRv6` model names (fallback to `PP-OCRv5` / `PP-OCRv4` ONNX if not bundled in `default_models.yaml`).

## 3. Measured Results (Local Verified Data)

### A. OCR Engine Comparison Status
| Configuration | Status in This Audit | Measured `₹` Glyph Error Rate | Measured Extraction Recall (6 Verified Fixtures, 37 Items) | Avg Latency |
|---|---|---:|---:|---:|
| **A. Apple Vision (`ocrmac`) + Existing Parser** | **Measured** (historical `dev_local.db` + 6 fixtures) | **> 80% error** on mobile screenshots (`₹` → `₴`, `7`, `2`, `#`, `$`) | `37 / 37` (`100%`) on text/box fixtures; `0%` on `5a3ba615`/`632ba280` (`bill_summary` false positive) | `< 15 ms` parser; `331–2586 ms` image pipeline |
| **B. RapidOCR (PP-OCRv4/v5 ONNX) + Existing Parser** | **Pending local image run** (not installed in offline sandbox) | Unmeasured locally | Unmeasured locally | Published: ~0.4–1.2s CPU |
| **C. PaddleOCR + Existing Parser** | **Pending local image run** | Unmeasured locally | Unmeasured locally | Published: ~0.8–2.0s CPU |
| **D. RapidOCR + Improved Preprocessing + Existing Parser** | **Pending local image run** | Unmeasured locally | Unmeasured locally | Unmeasured locally |
| **E. RapidOCR + TableIR + Validation + Selective VLM** | **Pending local image run** | Unmeasured locally | Unmeasured locally | Unmeasured locally |

### B. Preprocessing Audit Findings (from `dev_local.db` `quality_report`s)
- On clean digital screenshots (`84671e6a`, `5a3ba615`, `632ba280`), `preprocessing.py` recorded `glare_ratio = 0.86–0.90` (white UI background misclassified as glare) and applied `deskew(+1.2deg)` + `clahe_lab_2.0`, degrading crisp anti-aliased fonts.
- On handwritten/marked physical bills (`L015174`), grayscale conversion discarded blue-pen vs. black-print color separation.

## 4. Reproducible Isolated Benchmark Command (For Host Execution)
Run in an isolated temporary venv outside `backend/.venv`:
```bash
python3 -m venv /tmp/ocr_bench_venv
/tmp/ocr_bench_venv/bin/pip install rapidocr-onnxruntime opencv-python-headless pillow
/tmp/ocr_bench_venv/bin/python -c "
from rapidocr_onnxruntime import RapidOCR
import time
engine = RapidOCR()
# Run on collected real receipt images in ./eval_receipts/
"
```

## 5. Proposed Acceptance Criteria for Engine Promotion
Before replacing `ocrmac`/`Tesseract` with `RapidOCR` in production, `RapidOCR` must achieve on the **30–50 real-receipt dataset**:
1. **`₹` & Decimal Accuracy:** `>= 98%` on digital screenshots and `>= 95%` on physical bills.
2. **Item Line Recall & Price Accuracy:** `>= 92%` complete-row recall and `>= 95%` line-total accuracy.
3. **Grand-Total & Reconciliation:** `>= 95%` grand-total accuracy; `0` silent incomplete-extraction confirmations (`INCOMPLETE_EXTRACTION` preserved).
4. **Operational:** `<= 1.5s` median CPU latency per receipt, `<= 500 MB` RSS overhead, Apache 2.0 weights verified.
