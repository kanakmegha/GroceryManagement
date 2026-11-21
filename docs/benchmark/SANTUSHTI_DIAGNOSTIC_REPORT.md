# Santushti Bhojanam Real Photograph — Diagnostic & Extraction Report

**Source Image:** `media_1794945933309_c3c85fb1.jpg` (actual uploaded mobile photograph)  
**Test Date:** 2026-11-18  
**OCR Engine Used:** `apple_vision_ocrmac` (`VNRecognizeTextRequest`, `recognition_level="accurate"`)  
**End-to-End Latency:** `4,383 ms`

---

## 1. Original Image & Quality Assessment (IQA)

| Metric | Value | Interpretation |
|---|---:|---|
| Dimensions | `661 x 1024` px | Adequate resolution; upscaled to `1600 px` max dim |
| Blur Score (Laplacian Var) | `52.89` | Slightly soft, passes readability threshold (`>= 22.0`) |
| Brightness Mean | `128.86` | Normal indoor lighting |
| Contrast Std | `43.91` | Moderate thermal-paper contrast (`< 48.0`) -> triggered CLAHE |
| Illumination Variance | `26.57` | Uneven lighting/shadow (`> 22.0`) -> triggered background division |
| Glare Ratio | `0.0000` | No specular glare |
| Perspective Corrected | `false` | Dark background border was narrow; receipt filled ~92% of frame |
| Applied Preprocessing Steps | `upscale_1600`, `illumination_divide`, `clahe_lab_2.0` | Original bytes preserved; non-destructive enhancement |
| Readable (`is_readable`) | `true` | Passed IQA |

---

## 2. Bounding-Box & Raw OCR Output (`apple_vision_ocrmac`)

```text
y=0.037 x=0.227 w=0.103 h=0.022 conf=0.50 | SSAI i
y=0.176 x=0.239 w=0.430 h=0.037 conf=0.50 | Santustl Bhojanam
y=0.205 x=0.189 w=0.526 h=0.042 conf=1.00 | Fresh home cooked food at
y=0.239 x=0.300 w=0.290 h=0.026 conf=1.00 | affordable cost
y=0.282 x=0.113 w=0.131 h=0.025 conf=1.00 | Name:
y=0.336 x=0.095 w=0.506 h=0.040 conf=1.00 | Date: 26/04/26 Pick Up
y=0.364 x=0.090 w=0.117 h=0.024 conf=1.00 | 18:45
y=0.388 x=0.078 w=0.630 h=0.046 conf=1.00 | Cashier: biller Bill No.: 2043
y=0.440 x=0.057 w=0.109 h=0.030 conf=0.50 | Item
y=0.452 x=0.355 w=0.417 h=0.039 conf=1.00 | Qty. Price Amount
y=0.506 x=0.043 w=0.143 h=0.030 conf=0.50 | Phulka
y=0.519 x=0.363 w=0.060 h=0.026 conf=1.00 | 30
y=0.521 x=0.458 w=0.315 h=0.044 conf=1.00 | 15.00 450.00
y=0.545 x=0.031 w=0.292 h=0.042 conf=0.50 | Patiala Kadai
y=0.560 x=0.370 w=0.402 h=0.051 conf=1.00 | 5 220.001100.00
y=0.583 x=0.022 w=0.160 h=0.032 conf=1.00 | Paneer
y=0.625 x=0.011 w=0.314 h=0.042 conf=1.00 | Chole Masala
y=0.641 x=0.351 w=0.411 h=0.050 conf=1.00 | 3 180,00 540.00
y=0.713 x=0.483 w=0.090 h=0.030 conf=1.00 | Sub
y=0.717 x=0.133 w=0.303 h=0.039 conf=1.00 | Total Qty: 38
y=0.732 x=0.457 w=0.297 h=0.046 conf=1.00 | Total 2090.00
y=0.781 x=0.299 w=0.269 h=0.033 conf=0.50 | CGST 2.5%
y=0.789 x=0.620 w=0.127 h=0.033 conf=1.00 | 52.25
y=0.812 x=0.295 w=0.267 h=0.038 conf=0.50 | SGST 2.5%
y=0.823 x=0.613 w=0.127 h=0.030 conf=1.00 | 52.25
y=0.876 x=0.300 w=0.174 h=0.027 conf=1.00 | Round off
y=0.886 x=0.627 w=0.100 h=0.022 conf=0.50 | +0.5C
y=0.898 x=0.146 w=0.578 h=0.040 conf=1.00 | Grand Total € 2195.00
y=0.931 x=0.000 w=0.210 h=0.024 conf=1.00 | aid via Card
```

---

## 3. Ground Truth vs Extracted Output Comparison

| Field | Expected Ground Truth | Extracted Output | Match |
|---|---|---|---|
| Merchant Name | Santushti Bhojanam (printed `Santusti Bhojanam`) | `Santustl Bhojanam` | Near-exact (1 glyph `i->l`, editable in UI) |
| Date | `2026-04-26` (`26/04/26`) | `2026-04-26` | Exact |
| Bill Number | `2043` | `2043` | Exact |
| Item 1 | `Phulka` — qty `30` — `₹15.00` — `₹450.00` | `Phulka` — `30.0` pcs — `₹15.00` — `₹450.00` | Exact |
| Item 2 | `Patiala Kadai Paneer` — qty `5` — `₹220.00` — `₹1,100.00` | `Patiala Kadai Paneer` — `5.0` pcs — `₹220.00` — `₹1,100.00` | Exact (wrapped suffix + split `220.001100.00`) |
| Item 3 | `Chole Masala` — qty `3` — `₹180.00` — `₹540.00` | `Chole Masala` — `3.0` pcs — `₹180.00` — `₹540.00` | Exact (`180,00` normalized to `180.00`) |
| Subtotal | `₹2,090.00` | `209000` paise (`₹2,090.00`) | Exact |
| CGST 2.5% | `₹52.25` | `5225` paise (`₹52.25`) | Exact |
| SGST 2.5% | `₹52.25` | `5225` paise (`₹52.25`) | Exact |
| Rounding | `+₹0.50` | `50` paise (`+₹0.50`) | Exact (`+0.5C` normalized to `+0.50`) |
| Grand Total | `₹2,195.00` | `219500` paise (`₹2,195.00`) | Exact |
| Payment Method | Card | `CARD` | Exact |
| Math Reconciled | `true` | `true` (`computed_total_paise = 219500`) | Exact |
| Confidence / Warnings | — | `confidence=0.80`, `warnings=[]`, `needs_review=false` | Verified |

---

## 4. Hugging Face `Qwen/Qwen2.5-VL-7B-Instruct` Live Comparison
- A live inference call was attempted against `https://router.huggingface.co/v1/chat/completions` using `HF_TOKEN` from `backend/.env` with the preprocessed image.
- **Actual Result:** `HTTP 400` (`"The requested model 'Qwen/Qwen2.5-VL-7B-Instruct' is not supported by any provider you have enabled."`). Latency: `1,714 ms`.
- **Conclusion:** Local Apple Vision (`ocrmac`) + OpenCV preprocessing + spatial row-clustering succeeded offline at `$0.00` cost, while HF VLM escalation requires enabling an inference provider for `Qwen/Qwen2.5-VL-7B-Instruct` on the Hugging Face account.
