# Receipt OCR & Document Intelligence Architecture Recommendation

## 1. Final Decisions (Direct Answers)
1. **Should we retain PP-OCRv6?** PP-OCRv6 is **not** currently implemented (`ocrmac` + `tesseract` are). We should **adopt PP-OCRv5/v6 ONNX weights via RapidOCR** (`rapidocr-onnxruntime`) as our cross-platform primary OCR engine, rather than relying on macOS-only `ocrmac` or noise-sensitive Tesseract.
2. **Should we replace `ocrmac`/Tesseract?** Yes—replace them as primary with **RapidOCR (PP-OCRv5/v6 ONNX)** for cross-platform consistency across macOS and Linux Docker, keeping `TesseractOcrProvider` / `ocrmac` only as optional fallbacks.
3. **Should we combine multiple OCR engines?** Do **not** run multiple OCR engines on every receipt (that doubles latency and RAM). Instead, use **confidence-based selective escalation**: fast CPU RapidOCR first (~90% of receipts), escalating only low-confidence/unreconciled/handwritten receipts to the existing optional VLM (`Qwen2.5-VL`) or targeted review.
4. **Which document-understanding model should we use?** Keep our **layout-aware `TableIR` + `spatial_parser` + deterministic `validation`** as the primary structural extractor (fast, explainable, zero hallucination, Apache 2.0). Avoid LayoutLMv3 (`CC BY-NC-SA 4.0` non-commercial license). Evaluate **PP-StructureV3** / lightweight fine-tuned layout rerankers only after collecting 50+ confirmed real receipts; keep Donut (MIT) only as an offline benchmark comparison.
5. **Which parts should be rule-based?** Preprocessing/IQA, `TableIR` column clustering, arithmetic reconciliation (`qty * rate == total`, subtotal/tax/grand-total checks), `INCOMPLETE_EXTRACTION` detection, crossed-out/uncertainty flagging, and canonical ingredient resolution.
6. **Which parts should use ML or LLMs?** Neural text detection + recognition (`RapidOCR` / `PP-OCRv5/v6`), selective VLM escalation only on unreadable/handwritten/unreconciled bills, and periodic offline batch evaluation of confirmed user corrections.
7. **How should user corrections improve future extraction?** Through the approved **Stage 3–4 Receipt Learning** design: temporary image retention (`pending_deletion` + retryable cleanup), immutable OCR baselines + stable line IDs, 13-category failure diagnosis, household-isolated `ItemAlias` for names, and batch-evaluated `merchant_layout_profiles` (never retraining after every single edit).
8. **Best CPU-friendly deployment architecture?** Single FastAPI container (or worker process) with `rapidocr-onnxruntime` singleton initialized once at app startup (model caching in RAM ~300–450 MB, ~15–30 MB weights, Apache 2.0), async `asyncio.to_thread` execution, and zero mandatory GPU or paid API dependencies.
9. **Expected infrastructure cost?** **\$0/month additional inference cost** on existing CPU backend hosting (2 vCPU / 2–4 GB RAM container); optional HuggingFace VLM escalation only when explicitly enabled.
10. **What should be implemented first?** (1) Preprocessing fixes for digital screenshots + color-ink separation, (2) `RapidOCR` adapter + local head-to-head benchmark on real receipt images, (3) Stage 3 temporary image & correction evidence capture.

## 2. Top 3 Ranked Architectures

| Rank | Architecture | Accuracy & Robustness | CPU Latency & RAM | Deployment & License | Verdict |
|---|---|---|---|---|---|
| **1 (Recommended)** | **RapidOCR (PP-OCRv5/v6 ONNX) + Adaptive Preprocessing + `TableIR`/Spatial Parser + Deterministic Validation + Selective VLM Escalation** | High across kirana, thermal, GST & mobile screenshots; fixes `₹` glyph errors and Linux/macOS parity | ~0.4–1.2s CPU / ~350 MB RAM | **Apache 2.0**, minimal Docker image, CPU-only | **Best balance** of accuracy, cost, speed, and maintainability |
| **2** | **PaddleOCR (full PaddlePaddle + PP-StructureV3) + Deterministic Validation** | Highest open-source table structure + OCR accuracy | ~0.8–2.0s CPU / ~1.0–1.5 GB RAM | Apache 2.0, heavier PaddlePaddle wheel | Strong alternative if complex multi-page GST tables dominate |
| **3** | **Current (`ocrmac`/Tesseract) + Heuristic Fixes + Cloud VLM** | `ocrmac` fails `₹` glyphs & is macOS-only; Tesseract degrades on phone photos | Fast on macOS; poor on Linux CPU | `ocrmac` proprietary; `Tesseract` Apache 2.0 | **Insufficient** for production Linux deployment |

## 3. Proposed Hybrid Pipeline
```
Receipt Upload → Temp Image Store (pending_deletion lifecycle)
  → Adaptive Preprocessing (skip CLAHE/deskew on clean digital screenshots; preserve ink color)
  → Primary OCR: RapidOCR (PP-OCRv5/v6 ONNX, CPU singleton)
  → Word-level Token Splitting + X-Projection TableIR + Spatial Parser
  → Deterministic Validation (INCOMPLETE_EXTRACTION, ANNOTATED_GRAND_TOTAL, math checks)
  → Selective VLM Escalation (ONLY if unreconciled/unreadable & enabled)
  → Draft Review → User Confirm → Persist Learning Evidence → Delete Temp Image
```
