# Receipt OCR Dataset Coverage Audit

## 1. Inventory of All 42 Historical Records in `backend/dev_local.db`
A read-only audit of `purchases` (`42` total rows) in `backend/dev_local.db` shows:

| Category | Count | Examples (`purchase.id` / filename / bill) | Ground Truth Available? | Raw Image Binary Retained? |
|---|---:|---|---|---|
| **A/D. Physical Local Grocery / Kirana** | 12 | `AGGARWAL TRADERS` `L015174` (`4eb6d543`), `L006401` (`8097cd75`), `L011139` (`2a38cd03`), `L012633` (`04df4a49`) + discarded retries | Partial (`L011139`, `L012633`, `L015174` have verified line-item ground truth) | **No** (`attachments_json` stores `sha256` only) |
| **B. Quick-Commerce Digital Screenshots** | 15 | Instamart / Blinkit / BigBasket / Zomato (`84671e6a`, `5a3ba615`, `632ba280`, `7d959e63`, `dd5094e8`, `06600451`) | No (only `raw_ocr_payload` text exists) | **No** |
| **C. Supermarket Receipts** | 1 | `Walmart` (`bad39d9b`, discarded test) | No | **No** |
| **E1. GST Tax Invoices (Digital/Screenshot)** | 8 | `Seller Name: Commodum` (`e96cd0f9`), `77341429`, `74e52bcd` | No | **No** |
| **E2. Restaurant / Sweets** | 3 | `Aggarwal sweets` `L032680` (`5345654d`), `L009052` (`71c5d19c`), `Santustl Bhojanam` (`87f95908`) | Partial (`Santushti` fixture in tests) | **No** |
| **E3. Test / Unknown** | 3 | `ZUCHINNI GREEN`, `80e8c0fe` | No | **No** |

*Note:* The 2 `.jpg` files in `.gemini/.../.user_uploaded/` (`media_1794945933309_c3c85fb1.jpg`, `media_1795022223890_55b159e8.jpg`) are app UI screenshots rather than raw grocery bills, and inside this sandbox `ocrmac` returns `0` text boxes on them.

## 2. Dataset Gaps & Collection Plan (Target: 30–50 Real Receipts)
Because `dev_local.db` does **not** retain raw receipt image binaries and has **0** real thermal supermarket rolls or regional-script bills, the current dataset is **insufficient** for a statistically valid multi-engine image benchmark. Synthetic receipts will **not** be used as a substitute.

**Collection & Annotation Plan:**
1. **Kirana / Handwritten Bill Books (10 receipts):** Camera photos with pen ticks, cross-outs, and column borders (e.g., Aggarwal Traders).
2. **Supermarket Thermal Rolls (10 receipts):** Long, slightly faded, or creased thermal receipts (Reliance Fresh, DMart, More, Spencer's).
3. **Quick-Commerce Digital Screenshots (10 receipts):** Swiggy Instamart, Blinkit, Zepto, BigBasket (testing `₹` glyph & strikethrough MRP).
4. **GST Tax Invoices (5–10 receipts):** Multi-column A4/PDF and phone-captured invoices.
5. **Challenging Conditions (5–10 receipts):** Low-light/shadowed photos, perspective tilt, and bilingual (Hindi/regional + English) receipts.
6. **Ground-Truth Format:** Store original image + human-verified JSON (`merchant`, `date`, `bill_no`, `items[{name, qty, unit, rate, total}]`, `subtotal`, `tax`, `discount`, `grand_total`) in a local gitignored evaluation directory.
