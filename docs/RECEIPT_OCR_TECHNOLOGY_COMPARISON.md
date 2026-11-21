# Receipt OCR & Document Understanding Technology Comparison

> **Note:** Figures below distinguish **verified repository facts** from **published official documentation and benchmarks**. None of the candidate deep-learning OCR libraries (`paddleocr`, `rapidocr-onnxruntime`, `surya-ocr`, `python-doctr`, `easyocr`, `transformers`) are currently installed in `backend/.venv`.

## 1. OCR Engines Comparison

| Criterion | **Apple Vision (`ocrmac`)** *(Current macOS)* | **Tesseract (`tesseract_tsv`)** *(Current Linux fallback)* | **RapidOCR (`rapidocr-onnxruntime`)** | **PaddleOCR (`PP-OCRv5` / `PP-OCRv6`)** | **docTR (`python-doctr`)** | **EasyOCR** | **Surya OCR** |
|---|---|---|---|---|---|---|---|
| **Code License** | Proprietary (Apple macOS API) | Apache 2.0 | Apache 2.0 | Apache 2.0 | Apache 2.0 | Apache 2.0 | GPL v3 |
| **Weights License** | Proprietary (OS-bundled) | Apache 2.0 | Apache 2.0 (PP-OCR weights) | Apache 2.0 | Apache 2.0 | Apache 2.0 | OpenRAIL-M (free <$2M rev/funding) |
| **Languages** | 18+ (macOS-dependent) | 100+ (`.traineddata`) | 80+ (PP-OCR models) | 50+ unified (`PP-OCRv6`) / 80+ (`v5`) | Primarily Latin/EU | 80+ | 90+ |
| **`₹` / Indian Receipt Accuracy** | Poor on small/mobile fonts (`₹`→`₴`/`7`/`2`) | Weak on skewed/noisy phone photos | Strong on printed + thermal (PP-OCRv4/v5 ONNX) | Strongest open-source det+rec (`PP-OCRv5`/`v6`) | Good on Latin; weak on Indic | Moderate; slower | Strong layout + multi-script |
| **Word-Level BBoxes** | Phrase/line-level only | Word-level | Line/span + char scores | Line/span + word-level option | Word + line-level | Line/span | Line + layout + reading order |
| **CPU Inference** | Apple Silicon Neural Engine (macOS only) | Very fast on clean scans; poor on noise | **Fast** (ONNX Runtime, no Paddle/Torch) | Fast (`PP-OCRv6` 3.9–5.2x CPU speedup; `v5` mobile ~21ms/crop) | Moderate (PyTorch/TF) | Slow–Moderate (PyTorch) | Slow on pure CPU (~650M params) |
| **GPU Support** | Metal/ANE (macOS only) | No | CUDA / DirectML / CoreML via ONNX | CUDA / TensorRT | CUDA / MPS | CUDA | CUDA / MPS |
| **Model Download Size** | 0 MB (OS-bundled) | ~10–50 MB / lang | **~10–30 MB** (ONNX) | `v5` mobile ~21 MB; `v6_small` ~10 MB det; `v6_medium` ~59 MB det | ~100–300 MB | ~100–500+ MB | ~1.3 GB+ (~650M params) |
| **RAM Footprint** | Low | Very low (~100 MB) | **Low (~250–450 MB)** | Medium (~500 MB–1.2 GB) | Medium–High (~1–2 GB) | High (~1.5–2.5 GB) | High (~2.5–4+ GB CPU) |
| **Python Compat (`>=3.11`)** | macOS only (`ocrmac`) | Binary + wrapper | **Yes (`onnxruntime`)** | Yes (`paddlepaddle` / `paddlex`) | Yes | Yes | Yes |
| **Docker / Linux Ready** | **No** (fails in Linux containers) | Yes (`apt install tesseract-ocr`) | **Yes (minimal image)** | Yes (heavier PaddlePaddle wheel) | Yes | Yes | Heavy (PyTorch + weights) |
| **Commercial Safety** | macOS-only constraint | **Unrestricted** | **Unrestricted** | **Unrestricted** | **Unrestricted** | **Unrestricted** | **Restricted** (GPLv3 + $2M cap) |

## 2. Document-Understanding Models Comparison

| Model | License (Code / Weights) | Architecture | Params / Size | Published CORD Receipt Benchmark | CPU Latency | Key Fit / Limitation |
|---|---|---|---|---|---|---|
| **Donut (CORD)** | MIT / MIT | OCR-free Swin + BART encoder-decoder | ~186M (~750 MB) | ~91.0–91.3 F1 on CORD | ~0.6–1.2s+ per image (autoregressive) | Permissive MIT; struggles on dense 13+ row Indian bills with small text & arbitrary layouts without fine-tuning |
| **LayoutLMv3-base** | MIT / **CC BY-NC-SA 4.0** | Multimodal Transformer (OCR text + bbox + image) | ~125M (~500 MB) + OCR | ~95–96%+ F1 on CORD | ~0.1–0.5s (transformer pass) + OCR | **Non-commercial weights license** blocks commercial production use |
| **PP-StructureV3 / Table Recognition** | Apache 2.0 | Layout + Table + OCR pipeline | Modular | High on structured tables | Fast with ONNX/OpenVINO | Best open-source Apache 2.0 structural table parser; heavy if full PaddleX stack installed |
| **Qwen2.5-VL-7B (Cloud VLM)** *(Already wired optionally)* | Apache 2.0 (7B) | Vision-Language Model | 7B | Strong zero-shot on complex bills | Requires GPU or Cloud API | Good selective escalation for unreadable/handwritten bills; too expensive/slow as primary |

## 3. Summary Assessment
- **Best Primary CPU-Friendly OCR Engine:** **RapidOCR (`rapidocr-onnxruntime`) with PP-OCRv5/v4 ONNX weights** (or **PaddleOCR PP-OCRv5/v6** if PaddlePaddle runtime is acceptable). RapidOCR delivers PaddleOCR's detection/recognition accuracy inside a ~15–30 MB Apache 2.0 ONNX Runtime footprint, runs identically on macOS dev and Linux/Docker production, and eliminates `ocrmac`'s macOS lock-in and Tesseract's noise fragility.
- **Avoid for Production Commercial Core:** **LayoutLMv3** (`CC BY-NC-SA 4.0` non-commercial weights) and **Surya OCR** (`GPL v3` + OpenRAIL-M $2M cap + heavy CPU footprint).
