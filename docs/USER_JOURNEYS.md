# End-to-End User Journeys — GroceryManagement

**Project:** `GroceryManagement-AgentBenchmark`  
**Phase:** Phase 1 — Product Definition  
**Author:** Product Manager (Coordinating Agent)  
**Lifecycle Covered:** `PURCHASE ─▶ INVENTORY ─▶ RECIPE DISCOVERY ─▶ COOKING ─▶ CONSUMPTION ─▶ INSIGHTS ─▶ REPLENISHMENT`

---

## 1. End-to-End Happy Path (All 7 Lifecycle Stages)

### Journey 1 (Stage 1: PURCHASE): Camera/Gallery/PDF Receipt Upload → OCR → Review → Confirm
- **Actor:** Primary Household Shopper (P1)
- **Preconditions:** App is bootstrapped into Household `H1` with a valid backend auth token (no login UI shown in dev mode).
- **Steps:**
  1. User opens **Capture Receipt** and selects one of: **Take Photo**, **Choose from Gallery**, or **Upload PDF** (digital or scanned).
  2. Mobile uploads the file to FastAPI (`POST /api/v1/purchases/receipts`), which stores the receipt metadata, assigns `household_id = H1`, and runs OCR extraction.
  3. Purchase enters `PENDING_REVIEW` status. **No `inventory_lots` or `stock_ledger` rows are created yet.**
  4. User opens the **Purchase Review** screen, inspects extracted merchant, date, INR line items (`₹`), metric quantities (`g`, `kg`, `ml`, `L`, `pcs`), and suggested expiry dates.
  5. User corrects one item name (e.g., `"Amul Toned Mlk"` → `"Toned Milk"`), adjusts quantity to `1 L`, and taps **Confirm Purchase**.
  6. Backend executes `POST /api/v1/purchases/{id}/confirm` inside a Neon PostgreSQL transaction:
     - Marks purchase `CONFIRMED`.
     - Creates `inventory_lots` with canonical base quantities (`1000 ml`), INR unit cost, and `expires_at`.
     - Appends `PURCHASE_IN` rows to `stock_ledger`.
- **Postconditions:** Purchase is `CONFIRMED`; new lots are visible in Inventory; ledger balances match lot `remaining_qty_base`.

### Journey 2 (Stage 2: INVENTORY): Lot Inspection & Expiry Tracking
- **Actor:** Household Member (P1 or P2)
- **Preconditions:** Household `H1` has confirmed lots in Neon PostgreSQL.
- **Steps:**
  1. User navigates to the **Pantry / Inventory** tab.
  2. App fetches `GET /api/v1/inventory/lots` scoped to `H1`, displaying items grouped by ingredient and sorted by soonest `expires_at`.
  3. User taps an item (e.g., `Tomatoes`) and sees two distinct lots:
     - Lot A: `500 g` purchased 4 days ago, expires in `1 day` (flagged **Expiring Soon**).
     - Lot B: `1 kg` (`1000 g`) purchased today, expires in `6 days`.
  4. User inspects the lot's ledger history (`PURCHASE_IN +500 g`).
- **Postconditions:** User has full visibility into lot-level expiry and metric quantities.

### Journey 3 (Stage 3: RECIPE DISCOVERY): Real-Inventory Recipe Recommendations
- **Actor:** Primary Household Cook (P1)
- **Preconditions:** Household `H1` has active, non-expired lots (`Tomatoes`, `Paneer`, `Onions`, `Cooking Oil`).
- **Steps:**
  1. User opens **Recipes** tab.
  2. App calls `GET /api/v1/recipes/recommendations` for `H1`.
  3. Backend queries active lots (`remaining_qty_base > 0`, not expired) and matches them against persisted, verified recipes.
  4. App displays matching recipes ranked by ingredient coverage (e.g., `"Paneer Bhurji — 100% ingredients available"`, `"Tomato Dal — missing 100 g Toor Dal"`). Every recipe shown is grounded in real DB records with zero fabricated URLs.
- **Postconditions:** User sees actionable recipes tailored strictly to real pantry stock.

### Journey 4 (Stage 4: COOKING): Start Cooking Plan → Confirm Actuals → FEFO Deduction
- **Actor:** Primary Household Cook (P1)
- **Preconditions:** User selected `"Paneer Bhurji"` requiring `250 g` Paneer and `300 g` Tomatoes.
- **Steps:**
  1. User taps **Start Cooking** (`POST /api/v1/cooking-plans` -> `IN_PROGRESS`).
  2. **Critical Check:** Inventory lots (`Lot A: 500 g Tomatoes`) remain **unchanged** (`500 g`); `stock_ledger` has **zero** `COOK_OUT` rows.
  3. After cooking, user taps **Complete Cooking**, reviews pre-filled quantities, confirms `250 g` Paneer and `300 g` Tomatoes were used, and submits (`POST /api/v1/cooking-plans/{id}/complete`).
  4. Backend executes FEFO inside a DB transaction:
     - Deducts `300 g` Tomatoes from **Lot A** (soonest expiry) first, leaving `200 g` in Lot A and `1000 g` untouched in Lot B.
     - Appends `COOK_OUT (-300 g)` and `COOK_OUT (-250 g)` to `stock_ledger`.
- **Postconditions:** Cooking plan is `COMPLETED`; FEFO lots are updated atomically; consumption records are created.

### Journey 5 (Stage 5: CONSUMPTION): Usage & Wastage Timeline
- **Actor:** Household Member (P1/P2)
- **Preconditions:** At least one cooking completion or wastage write-off has occurred.
- **Steps:**
  1. User opens **Consumption & Wastage** history (`GET /api/v1/consumption`).
  2. Timeline displays `"Paneer Bhurji"` consumption (`250 g Paneer — ₹95.00`, `300 g Tomatoes — ₹18.00`) alongside any logged wastage events.
- **Postconditions:** Household has a complete, INR-valued audit trail of what was eaten vs. wasted.

### Journey 6 (Stage 6: INSIGHTS): Household Spending & Wastage Analytics
- **Actor:** Primary Household Shopper (P1)
- **Preconditions:** Confirmed purchases and consumption/wastage ledger entries exist for `H1`.
- **Steps:**
  1. User opens **Insights** tab (`GET /api/v1/insights/summary?period=30d`).
  2. App renders:
     - Total INR Spend (`₹4,820.00`) across confirmed receipts.
     - Consumed Value (`₹3,410.00`) vs. Wasted Value (`₹215.00`).
     - Top wasted ingredients (e.g., `Coriander — ₹60.00`).
- **Postconditions:** All metrics reconcile directly with persisted `purchases` and `stock_ledger` rows.

### Journey 7 (Stage 7: REPLENISHMENT): Data-Driven Restock Suggestions
- **Actor:** Primary Household Shopper (P1)
- **Preconditions:** `Toned Milk` has dropped to `0 ml` active stock after prior consumption; `Tomatoes` still have `1200 g` active stock.
- **Steps:**
  1. User opens **Replenishment** tab (`GET /api/v1/replenishment/suggestions`).
  2. Backend suggests `Toned Milk` (depleted, historically consumed) and **does not** suggest `Tomatoes` (sufficient active stock).
  3. User checks off `Toned Milk` on their in-app shopping checklist (no external grocery platform links).
- **Postconditions:** User has a grounded restock checklist ready for their next offline/store trip.

---

## 2. Critical Edge-Case Journeys

### Edge Case 1: Empty Inventory → Zero Inventory-Based Recommendations
- **Preconditions:** Newly bootstrapped household `H_EMPTY` with `0` inventory lots (or all lots depleted/expired).
- **Flow:**
  1. User navigates to **Recipes** tab.
  2. App calls `GET /api/v1/recipes/recommendations`.
  3. Backend verifies active `remaining_qty_base > 0` count is `0` and returns `recommendations: []` (HTTP `200`).
  4. Mobile UI renders an explicit empty state: *"Your pantry is empty. Scan or upload a receipt to get recipe recommendations from your real ingredients."*
- **Invariant Verified:** `HI-2` (no fake inventory) & `HI-5` (empty inventory produces zero inventory-based recommendations).

### Edge Case 2: OCR Math Mismatch & Low-Confidence Review
- **Preconditions:** User uploads a crumpled kirana/supermarket photo where OCR reads `2 x ₹45.00 = ₹90.00` as `₹900.00`, causing `SUM(line_totals) != receipt_total`.
- **Flow:**
  1. Backend flags the `PENDING_REVIEW` purchase with `has_math_mismatch = true` and `ocr_confidence < threshold` on the affected line.
  2. **Inventory remains untouched.**
  3. Purchase Review UI highlights the mismatch banner (`Line sum ₹1,350.00 vs. Receipt total ₹540.00`) and marks the low-confidence line in amber.
  4. User edits the line total from `₹900.00` to `₹90.00` (mismatch clears) and taps **Confirm Purchase**.
  5. Only after user confirmation does the backend create `inventory_lots` with the corrected `₹45.00/unit` cost basis.
- **Invariant Verified:** `HI-6` (OCR never directly updates inventory) & INR accuracy.

### Edge Case 3: Multi-Page PDF Receipt Processing (Digital & Scanned)
- **Preconditions:** User has a 3-page supermarket invoice PDF (either digital text PDF or scanned image-only PDF).
- **Flow:**
  1. User selects **Upload PDF** and picks the 3-page PDF.
  2. Backend detects `page_count = 3`, extracts text/OCR across **pages 1, 2, and 3** sequentially/asynchronously, and tags each extracted line item with `source_page` (`1`, `2`, or `3`).
  3. Purchase Review displays all merged line items from all 3 pages (e.g., 24 items) with page badges (`Page 1`, `Page 2`, `Page 3`) and a unified INR total check.
  4. User reviews/edits any line and confirms once to create all lots atomically.
- **Requirement Verified:** `FR-1.2` (multi-page PDF processing).

### Edge Case 4: Partial Cooking Consumption & Ingredient Substitution
- **Preconditions:** User starts a cooking plan for `"Veg Pulao"` expecting `500 g` Basmati Rice and `200 g` Green Peas.
- **Flow:**
  1. User taps **Start Cooking**. Stock remains `500 g` Rice and `200 g` Peas.
  2. While cooking, user only uses `350 g` Basmati Rice, `0 g` Green Peas (skipped), and substitutes `150 g` Carrots.
  3. User taps **Complete Cooking**, edits actuals (`Basmati Rice: 350 g`, `Green Peas: 0 g`, adds `Carrots: 150 g`), and confirms.
  4. Backend deducts **only** `350 g` Rice and `150 g` Carrots via FEFO; `Green Peas` remains at `200 g`; `150 g` Rice remains in its lot.
- **Invariant Verified:** `HI-7` (start never deducts; only confirmed actuals deduct).

### Edge Case 5: Expired Lot Write-Off & Wastage Tracking
- **Preconditions:** `Lot C` (`250 g` Spinach, `unit_cost = ₹40.00` total) has `expires_at < today` and `remaining_qty_base = 250 g`.
- **Flow:**
  1. Inventory tab flags `Lot C` as **Expired**.
  2. Recipe Discovery excludes `Lot C` from available ingredients.
  3. User taps **Write Off Expired** on `Lot C` and selects reason `EXPIRED`.
  4. Backend (`POST /api/v1/inventory/lots/{id}/write-off`) appends `WASTE_OUT (-250 g)` to `stock_ledger`, sets `remaining_qty_base = 0`, and marks `Lot C` as `WRITTEN_OFF`.
  5. Insights immediately reflects `+₹40.00` in **Wasted Value**, and Replenishment surfaces Spinach with a mild *"Previously expired — consider smaller pack"* note.
- **Requirements Verified:** `FR-2.4`, `FR-5.2`, `FR-6.2`, `FR-7.2`.

### Edge Case 6: Dev-Mode Household Bootstrap Without Login UI
- **Preconditions:** Fresh app launch in development testing mode (`EXPO_PUBLIC_DEV_AUTH_BOOTSTRAP=true` or dev-mode backend bootstrap endpoint enabled in non-prod).
- **Flow:**
  1. User/tester launches the Expo app. **No login screen, no registration form, and no password prompt are rendered.**
  2. On startup, the app checks for a stored dev session token; if absent, it calls the dev bootstrap endpoint (`POST /api/v1/auth/dev-bootstrap`) which returns a real signed auth token bound to an isolated dev `household_id`.
  3. All subsequent API calls include `Authorization: Bearer <token>`.
  4. If a request is made without a token, or with a token belonging to Household `H2` trying to read/confirm a purchase in Household `H1`, FastAPI rejects it with `401 Unauthorized` / `403 Forbidden` (or `404`).
- **Invariant Verified:** `HI-4` (no login/registration UI in dev testing while preserving genuine backend auth & household isolation).
