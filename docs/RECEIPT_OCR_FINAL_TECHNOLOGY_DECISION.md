# Receipt OCR Final Technology Decision & Mobile Production Deployment

## 1. Explicit Answers to Decision Questions
1. **Is RapidOCR better than the existing Apple Vision implementation?**
   - **Architecturally: Yes** — `ocrmac` is macOS-only (cannot run in Linux/Docker production) and misreads `₹` as `₴`/`7`/`2` on mobile screenshots (`>80%` error in `dev_local.db`).
   - **Empirically: Pending final image benchmark** — `RapidOCR` could not be executed in this offline sandbox without real receipt images and `rapidocr-onnxruntime` installed; it must pass the 30–50 image acceptance gate before production cutover.
2. **Which exact OCR model should GroceryManagement use?**
   - **`rapidocr-onnxruntime` (Apache 2.0) with `PP-OCRv5` (or `PP-OCRv4`) ONNX mobile/server weights** — verify exact bundled `PP-OCRv5`/`v6` ONNX filenames at install time rather than assuming `PP-OCRv6` is pre-bundled.
3. **Does PaddleOCR provide a meaningful improvement?**
   - Full `PaddleOCR` (`PaddlePaddle` + `PP-StructureV3`) improves complex multi-page GST table parsing, but adds ~1.0–1.5 GB RAM and a heavier Docker image. **RapidOCR** delivers the same PP-OCR detection/recognition weights in a lightweight ONNX Runtime footprint.
4. **Which preprocessing methods actually improve results?**
   - **Keep:** EXIF orientation transpose, 4-corner perspective warp (for angled camera photos), mild deskew, and resolution normalization (`1600–2600px`).
   - **Fix / Avoid:** Bypass `deskew`, `CLAHE`, and glare warnings on clean digital screenshots; avoid destructive adaptive thresholding or grayscale-only flattening on receipts with blue/red pen markings.
5. **Is selective VLM escalation necessary?**
   - **Yes, as an optional fallback only** (already gated by `RECEIPT_ENABLE_CLOUD_VLM=true`), for severely crumpled, faded, or handwritten bills where local OCR fails financial reconciliation. Never as a mandatory primary dependency.
6. **Can the proposed solution run economically on CPU infrastructure?**
   - **Yes.** RapidOCR ONNX runs on standard 2 vCPU / 2–4 GB RAM containers with **\$0/month** additional per-receipt inference API fees.
7. **What remains unresolved?**
   - Local head-to-head CER/WER and `₹` accuracy measurement on 30–50 real receipt images (blocked by missing raw image binaries in `dev_local.db` and offline sandbox).

## 2. Ranked Top 3 Configurations (Current Evidence)
1. **Rank 1 (Recommended Target):** `RapidOCR (PP-OCRv5/v4 ONNX)` + Screenshot-Aware/Color-Preserving Preprocessing + `TableIR`/Spatial Parser + Deterministic Validation + Optional VLM Escalation. *(Pending 30–50 image gate)*
2. **Rank 2:** Full `PaddleOCR` + `PP-StructureV3` + Deterministic Validation. *(Higher RAM/Docker footprint)*
3. **Rank 3 (Current Baseline):** `ocrmac` (macOS) / `Tesseract` (Linux) + Existing Parser + Optional VLM. *(Verified inadequate `₹` accuracy and macOS/Linux parity)*

---

## 3. iOS & Android Production Deployment Architecture (App Store & Google Play)

An audit of `mobile/src/features/purchases/receiptUpload.ts` and `mobile/src/shared/api/` identified critical gaps for public iOS/Android release:

1. **Server-Side OCR vs. On-Device OCR:**
   - **Decision: Server-side OCR.** Bundling ONNX/Paddle models inside the Expo app would bloat the iOS/Android binary by 30–100+ MB, cause thermal/RAM throttling on low-end Android devices, and couple OCR fixes to App Store / Play Store review cycles.
2. **Image Format & HEIC Gap (`receiptUpload.ts:15-21`):**
   - `ALLOWED_MIME_TYPES` currently allows `image/jpeg`, `image/png`, `image/webp`, `application/pdf`, `text/plain` — **it rejects `image/heic` / `image/heif`**, which iOS `ImagePicker` frequently produces.
   - **Fix:** Normalize iOS camera/gallery picks to JPEG via `expo-image-manipulator` (also stripping unnecessary GPS EXIF while applying EXIF orientation) **and** add `pillow-heif` + `image/heic` support on the backend.
3. **Camera Resolutions, Aspect Ratios & Payload Size:**
   - Modern 48MP mobile cameras exceed `MAX_RECEIPT_BYTES = 10 MB`. Resize client-side to max `2400px` longest edge (`quality: 0.85` JPEG) before upload, preserving legibility while cutting upload time on 3G/4G networks by 5–10x.
4. **Network Reliability, Timeouts & Offline Capture (`mobile/src/shared/api/`):**
   - Currently there is **no** `AbortController` timeout, **no** upload retry, and **no** offline pending-upload queue.
   - **Fix:** Add `AbortController` (45s timeout for OCR uploads), idempotent `client_upload_id` / `sha256` deduplication (already supported 60s window on backend), and an AsyncStorage/FileSystem pending-upload queue for offline capture with background retry on reconnect.
5. **Authentication, Household Isolation & Privacy:**
   - Clerk JWT (`Authorization: Bearer`) + `X-Household-Id` isolation is already enforced on all `/api/v1/purchases/*` endpoints.
   - Temporary receipt images (`pending_deletion` lifecycle) must be stored in household-scoped private storage and deleted post-confirmation (never cached in public CDNs).
6. **Backend Scalability & Independent Deployment:**
   - Keep API versioned (`/api/v1/purchases/ocr`) so backend OCR upgrades (e.g., `ocrmac` → `RapidOCR`) deploy independently of iOS/Android app releases.
   - Run `RapidOCR` as a singleton per Uvicorn/Gunicorn worker inside `asyncio.to_thread` (with bounded semaphore concurrency, e.g., `2–4` concurrent OCR jobs per worker) to prevent CPU/RAM spikes under concurrent mobile uploads.
