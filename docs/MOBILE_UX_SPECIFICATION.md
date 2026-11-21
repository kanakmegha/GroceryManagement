# Mobile UX & Frontend Architecture Specification — GroceryManagement

**Project:** `GroceryManagement-AgentBenchmark`  
**Phase:** Phase 1 — Product & UX Definition  
**Author:** Frontend Engineer  
**Target Stack:** React Native + Expo (Expo Go Compatible) · TypeScript · Expo Router · TanStack Query · Zustand · `expo-secure-store`  
**Related Specs:** [PRD.md](file:///Users/skanakmegha/Documents/MEGHA/Personal-Development/GroceryManagement/GroceryManagement-AgentBenchmark/docs/PRD.md) · [USER_JOURNEYS.md](file:///Users/skanakmegha/Documents/MEGHA/Personal-Development/GroceryManagement/GroceryManagement-AgentBenchmark/docs/USER_JOURNEYS.md) · [SECURITY_ARCHITECTURE.md](file:///Users/skanakmegha/Documents/MEGHA/Personal-Development/GroceryManagement/GroceryManagement-AgentBenchmark/docs/SECURITY_ARCHITECTURE.md)

---

## 1. Mobile Architecture & Expo Go Compatibility

### 1.1 Runtime & Expo Go Constraints
The mobile application is designed to run **independently** from the FastAPI backend via Expo CLI (`npx expo start`) and **Expo Go** on iOS/Android without requiring custom native builds (`expo prebuild` / EAS Dev Client).

- **Approved Expo SDK / JS-Only Modules:**
  - Navigation: `expo-router` (file-based routing with typed routes)
  - Secure Storage: `expo-secure-store` (Keychain/Keystore for JWT persistence; never `AsyncStorage`)
  - Capture & Files: `expo-image-picker` (camera & gallery), `expo-document-picker` (digital & scanned PDFs), `expo-file-system` (file size/URI inspection before upload)
  - Cryptography: `expo-crypto` (`randomUUID()` for `Idempotency-Key` headers)
  - Network State: `expo-network` (online/offline detection)
- **Avoided Heavy Native Dependencies:** No custom native OCR SDKs (e.g., MLKit/Tesseract native bindings), no native SQLite/Realm sync engines, and no external grocery SDKs (`HI-1`). All OCR and PDF rasterization occur server-side in FastAPI.

### 1.2 Feature-Sliced Directory Structure
```text
mobile/
├── app/                              # Expo Router entrypoints (thin route wrappers only)
│   ├── _layout.tsx                   # Root providers (QueryClient, DevAuthBootstrapGate, SafeArea)
│   ├── (tabs)/                       # Primary 5-tab navigation shell
│   │   ├── _layout.tsx
│   │   ├── index.tsx                 # Pantry / Inventory (Stage 2)
│   │   ├── recipes.tsx               # Recipe Discovery (Stage 3)
│   │   ├── capture.tsx               # Center CTA / Purchase Hub (Stage 1)
│   │   ├── insights.tsx              # Spending & Wastage Insights (Stage 6)
│   │   └── restock.tsx               # Replenishment Checklist (Stage 7)
│   ├── purchases/
│   │   ├── capture-modal.tsx         # Camera / Gallery / PDF upload modal
│   │   └── [id]/review.tsx           # Editable Purchase Review & Confirmation
│   ├── inventory/
│   │   └── [ingredientId].tsx        # Lot-level details, expiry & stock ledger
│   ├── recipes/
│   │   └── [id].tsx                  # Recipe detail & Start Cooking trigger
│   ├── cooking/
│   │   ├── [planId]/active.tsx       # Active cooking plan (non-destructive)
│   │   └── [planId]/complete.tsx     # Actual ingredient confirmation & FEFO submit
│   └── consumption/
│       └── index.tsx                 # Consumption & Wastage timeline (Stage 5)
├── src/
│   ├── features/                     # Feature-sliced domain modules
│   │   ├── auth/                     # Dev-bootstrap hook, SecureStore token manager
│   │   ├── purchases/                # Upload, OCR polling, Review form & math validator
│   │   ├── inventory/                # Lots list, expiry badges, write-off sheet
│   │   ├── recipes/                  # Recommendations query, match breakdown
│   │   ├── cooking/                  # Plan lifecycle, actuals editor, FEFO preview
│   │   ├── consumption/              # Usage & wastage timeline
│   │   ├── insights/                 # INR spend & waste charts/cards
│   │   └── replenishment/            # Restock suggestions & checklist state
│   ├── shared/
│   │   ├── api/                      # Typed OpenAPI client, auth/idempotency interceptors
│   │   ├── formatters/               # INR (paise -> ₹) & Metric (g/kg, ml/L, pcs) utils
│   │   ├── components/               # Accessible UI primitives (Button, Banner, EmptyState)
│   │   └── config/                   # Public env allowlist validator
```

### 1.3 State Management (TanStack Query + Zustand)
| State Layer | Tool | Responsibilities | Rules |
|---|---|---|---|
| **Server State** | **TanStack Query** (`@tanstack/react-query`) | All FastAPI reads/mutations, OCR status polling (`refetchInterval` while `PROCESSING`), cache invalidation across lifecycle stages. | Never fabricate optimistic inventory (`HI-2`). Invalidate downstream queries on `confirm`, `complete`, and `write-off` (e.g., `confirm` invalidates `['purchases']`, `['inventory']`, `['recipes']`, `['insights']`). |
| **Ephemeral Client State** | **Zustand** | Draft edits on Purchase Review before submit, active cooking timer/notes, local restock checklist checked UI state (synced if persisted), active modal state. | Zero persisted auth secrets or authoritative stock balances in Zustand. Reset draft stores on unmount/completion. |

### 1.4 Typed OpenAPI Client & `Idempotency-Key` Generation
- **OpenAPI Codegen:** Generate TypeScript types (`schema.d.ts`) from FastAPI's `/openapi.json` using `openapi-typescript` and consume via `openapi-fetch` (or typed `fetch` wrapper).
- **Idempotency Protocol (`SEC-06`):**
  - Every state-mutating request (`POST /purchases/{id}/confirm`, `POST /cooking-plans/{id}/complete`, `POST /inventory/lots/{id}/write-off`) attaches `Idempotency-Key: <uuidv4>` generated via `expo-crypto`.
  - The key is generated **once per user intent** (stored in component/mutation ref when the user taps Confirm) and **reused** across automatic or manual retries of that exact action, preventing duplicate `stock_ledger` entries on flaky mobile networks. A new key is generated only after a `409 Conflict` or a fresh user action.

### 1.5 Dev-Mode Silent Auth Bootstrap (`HI-4`, `SEC-01`, `SEC-02`)
- **Zero Login/Registration UI in Dev Mode:** When `__DEV__` and `EXPO_PUBLIC_DEV_BOOTSTRAP === "true"`, `DevAuthBootstrapGate` in `app/_layout.tsx` runs silently on startup:
  1. Reads JWT from `expo-secure-store` (`gm_access_token`).
  2. Checks JWT `exp` claim locally (with 60s buffer).
  3. If missing or expired, calls `POST ${EXPO_PUBLIC_API_URL}/api/v1/auth/dev-bootstrap` and stores the returned signed JWT in `expo-secure-store`.
  4. Renders a minimal splash/spinner during bootstrap (< 500ms) and enters `(tabs)` directly—never rendering email/password or sign-up screens.
- **Public Env Allowlist:** Only `EXPO_PUBLIC_API_URL`, `EXPO_PUBLIC_APP_ENV`, and `EXPO_PUBLIC_DEV_BOOTSTRAP` may exist in mobile env config.

---

## 2. Navigation Map & Lifecycle Screen Specifications

### 2.1 Navigation Map

```text
Root Stack (_layout.tsx + DevAuthBootstrapGate)
├── (tabs) Bottom Tab Navigator (44x44pt+ tap targets)
│   ├── [Pantry]      app/(tabs)/index.tsx          ──▶ inventory/[ingredientId].tsx (Lot & Ledger)
│   ├── [Recipes]     app/(tabs)/recipes.tsx        ──▶ recipes/[id].tsx ──▶ cooking/[planId]/active.tsx ──▶ cooking/[planId]/complete.tsx
│   ├── [+ Scan]      app/(tabs)/capture.tsx        ──▶ purchases/capture-modal.tsx ──▶ purchases/[id]/review.tsx
│   ├── [Insights]    app/(tabs)/insights.tsx       ──▶ consumption/index.tsx (Usage & Wastage Timeline)
│   └── [Restock]     app/(tabs)/restock.tsx        (In-app Replenishment Checklist)
```

### 2.2 Screen Specifications (All 7 Lifecycle Stages)

| Stage | Screen & Route | Primary Data & Actions | Key UI Elements & Invariants |
|---|---|---|---|
| **1. Purchase** | **Receipt Capture** `purchases/capture-modal.tsx` | Select source: **Take Photo**, **Choose from Gallery**, **Upload PDF** (digital or scanned, up to 5 pages). Upload via `POST /api/v1/purchases/receipts`. | Client pre-checks file size (`<= 10 MiB`) and MIME (`jpeg`, `png`, `webp`, `pdf`). Shows upload progress, then transitions to `PENDING_REVIEW` or polls `PROCESSING` status without blocking navigation. |
| **1. Purchase** | **Editable Purchase Review** `purchases/[id]/review.tsx` | `GET /api/v1/purchases/{id}`, edit draft, `POST /api/v1/purchases/{id}/confirm`. | Displays merchant, date, INR line items, math mismatch banner, low-confidence amber highlights, multi-page PDF badges (`Page 1..N`), raw-to-metric preview, and expiry date picker. **Inventory untouched until Confirm.** |
| **2. Inventory** | **Pantry & Expiry** `(tabs)/index.tsx` | `GET /api/v1/inventory/lots` grouped by ingredient, sorted by soonest `expires_at ASC`. | Summary chips (`Expiring <= 3d`, `Expired`), search/filter, ingredient cards showing total metric stock and earliest expiry badge. |
| **2. Inventory** | **Lot Detail & Write-Off** `inventory/[ingredientId].tsx` | View individual lots (`lot_id`, `purchased_at`, `expires_at`, `unit_cost_inr`, `remaining_qty_base`), ledger history, `POST /api/v1/inventory/lots/{id}/write-off`. | Displays immutable ledger rows (`PURCHASE_IN`, `COOK_OUT`, `WASTE_OUT`). Expired/spoiled lots show **Write Off** action requiring reason (`EXPIRED`, `SPOILED`). |
| **3. Recipe Discovery** | **Recipes** `(tabs)/recipes.tsx` & `recipes/[id].tsx` | `GET /api/v1/recipes/recommendations` matched strictly against active, non-expired lots. | Ranked cards with coverage % (`100% Ready` vs `Missing 100 g Toor Dal`), metric quantities needed vs. available. **Zero fabricated external URLs (`HI-3`). Strictly `[]` when inventory is empty (`HI-5`).** |
| **4. Cooking** | **Active Cooking Plan** `cooking/[planId]/active.tsx` | `POST /api/v1/cooking-plans` (`PLANNED -> IN_PROGRESS`). | Displays recipe steps, target servings, and expected ingredient list with explicit badge: **"Planning Mode — Stock not deducted yet"** (`HI-7`). Actions: **Complete Cooking** or **Cancel Plan**. |
| **4. Cooking** | **Complete & Confirm Actuals** `cooking/[planId]/complete.tsx` | `POST /api/v1/cooking-plans/{id}/complete` with `Idempotency-Key`. | Pre-filled metric quantities, inline actuals editor, "Skipped (0)" toggle, "+ Add Substitution", optional cooking waste logger, and FEFO deduction confirmation. |
| **5. Consumption** | **Consumption & Wastage Timeline** `consumption/index.tsx` | `GET /api/v1/consumption` | Chronological feed of `CONSUMED` (from cooking) and `WASTED` (expired/spoiled/leftover) events with metric quantities and INR cost basis. |
| **6. Insights** | **Household Insights** `(tabs)/insights.tsx` | `GET /api/v1/insights/summary?period=7d\|30d\|90d` | Total INR Spend card, Consumed vs. Wasted INR comparison bar, Top Wasted Ingredients list, and link to Consumption Timeline. All figures derived strictly from persisted DB records. |
| **7. Replenishment** | **Restock Checklist** `(tabs)/restock.tsx` | `GET /api/v1/replenishment/suggestions` | Data-driven restock items (depleted/low stock with prior consumption history), wastage warning badges (*"Previously expired — consider smaller pack"*), and in-app checkbox state. **Zero external store/cart links (`HI-1`).** |

---

## 3. Editable Purchase Review UX (Stage 1 Deep-Dive)

Because OCR never directly mutates inventory (`HI-6`), the Purchase Review screen is the primary human-in-the-loop quality gate:

1. **Math Mismatch Handling:**
   - The client continuously computes `computedLineSumPaise = sum(line.lineTotalPaise)` and compares it against `receiptTotalPaise` (accounting for tax/discount).
   - If `has_math_mismatch` is true (or client edit creates a delta != 0), a persistent **amber reconciliation banner** appears at the top:
     > *"Line items sum to ₹1,350.00, but receipt total is ₹540.00 (Difference: +₹810.00). Review highlighted lines before confirming."*
   - Users can either fix the offending line item(s) or explicitly adjust the receipt total/discount. Confirming while a mismatch remains triggers a non-blocking confirmation dialog (*"Confirm with ₹810.00 discrepancy?"*) so users are never silently misled on INR insights.
2. **Low-Confidence Field Highlighting:**
   - Any line item or field with `ocr_confidence < threshold` renders with an **amber left border** and a subtle `"Needs check"` pill. Tapping the row opens an inline/bottom-sheet editor focused on the low-confidence field (name, quantity, unit, or INR price).
3. **Multi-Page PDF Provenance:**
   - Merged items from multi-page PDFs display a compact `Page 1`, `Page 2`, `Page 3` chip so users can cross-reference the original PDF page if needed.
4. **Raw Pack → Metric Base Unit Preview:**
   - Each line item shows the editable pack/display input alongside a live read-only **Metric Base Preview** and **Unit Cost**:
     - Input: `2` × `500` `g` at `₹90.00` total → Preview: `= 1,000 g (1 kg) · ₹90.00/kg`
     - Input: `1` × `1` `L` at `₹54.00` total → Preview: `= 1,000 ml (1 L) · ₹54.00/L`
   - Unit selector is restricted to allowed metric/count units (`g`, `kg`, `ml`, `L`, `pcs`). Incompatible conversions (e.g., `g` ↔ `ml`) are blocked with inline validation.
5. **Line Manipulation & Confirmation Guard:**
   - Users can **Add Line**, **Edit**, **Split** (e.g., split 1 pack into two lots with different expiry dates), or **Delete** any line, and set/adjust `expires_at` (with category-based default suggestions).
   - Tapping **Confirm Purchase** disables the button immediately (preventing double-taps), attaches `Idempotency-Key`, and transitions to Inventory upon `200 OK`.

---

## 4. Cooking Flow UX (Stage 4 Deep-Dive)

To enforce `HI-7` (starting a cooking plan never deducts stock; only confirmed completion deducts actuals via FEFO):

1. **Non-Destructive Start (`Active Cooking`):**
   - Tapping **Start Cooking** on a recipe creates/starts a plan (`IN_PROGRESS`) and displays a clear informational banner: *"Cooking started — your pantry stock will not be updated until you confirm what you actually used."*
   - Users can view ingredients, scale servings, or **Cancel Plan** with zero side effects on `inventory_lots` or `stock_ledger`.
2. **Actual Ingredient Confirmation (`Complete Cooking`):**
   - Pre-fills every recipe ingredient with the planned metric quantity (`e.g., Paneer: 250 g`, `Tomatoes: 300 g`).
   - **Per-Ingredient Controls:**
     - Numeric stepper / input in the ingredient's metric unit (`g`/`kg`, `ml`/`L`, `pcs`).
     - **Skip (`0`)** quick-toggle for omitted ingredients (e.g., skipped `Green Peas`).
     - **Available Stock Indicator:** Shows current active household stock (e.g., `Available: 500 g` across FEFO lots). If user enters `> available`, an inline error blocks submission (*"Only 500 g Tomatoes available across active lots"*) to prevent negative stock (`FR-4.3`).
   - **Substitutions & Extra Ingredients:** `+ Add Ingredient` button opens a picker filtered to **active household lots** (e.g., substituting `150 g Carrots`).
   - **Optional Cooking Waste:** Expandable *"Log leftover/waste?"* row per ingredient to record `WASTE_OUT` alongside `COOK_OUT` in the same atomic transaction.
   - **Confirm & Deduct:** Disables button during in-flight request, sends `Idempotency-Key`, and displays a brief FEFO deduction summary toast on completion.

---

## 5. Empty States & Grounded Behavior

| Screen | Condition | Empty State Copy & Primary CTA |
|---|---|---|
| **Recipes (`HI-5`)** | Household has `0` active, non-expired stock (`recommendations: []`) | **Title:** *"Your pantry is empty"* · **Body:** *"Scan or upload a receipt to get recipe recommendations from your real ingredients."* · **CTA:** `Capture Receipt` (navigates to `purchases/capture-modal`) |
| **Recipes (Partial)** | Inventory > 0, but no recipes match current items | **Title:** *"No matching recipes yet"* · **Body:** *"Add more staple ingredients from your next receipt to unlock recipe matches."* · **CTA:** `View Pantry` |
| **Pantry / Inventory** | Zero active lots | **Title:** *"No items in your pantry"* · **Body:** *"Upload a grocery receipt photo or PDF to start tracking lots and expiry dates."* · **CTA:** `Scan Receipt` |
| **Pending Reviews** | No `PENDING_REVIEW` purchases | **Title:** *"All receipts reviewed"* · **Body:** *"Capture a new receipt anytime to update your pantry."* |
| **Consumption** | Zero `COOK_OUT` / `WASTE_OUT` events | **Title:** *"No consumption logged yet"* · **Body:** *"Complete a cooking plan or write off an expired lot to see your usage history."* |
| **Insights** | Zero confirmed purchases & ledger events | **Title:** *"No insights available yet"* · **Body:** *"Confirm your first purchase to track INR spending and household wastage."* |
| **Replenishment** | All historically used items have sufficient active stock | **Title:** *"Pantry is well stocked"* · **Body:** *"No restock suggestions right now. Items will appear here as your active stock runs low."* |

---

## 6. Offline, Error Handling, Formatting & Accessibility

### 6.1 Offline & Network Error Handling
- **Read-Only Cache:** TanStack Query retains last-fetched server state in memory during brief offline periods, accompanied by a non-intrusive top banner: *"You're offline. Showing last synced pantry data."*
- **Mutation Blocking:** State-mutating actions (`Confirm Purchase`, `Complete Cooking`, `Write Off Lot`, `Upload Receipt`) require connectivity and never fabricate optimistic success (`HI-2`). If offline, buttons display `"Requires internet connection"` or queue a safe retry prompt preserving the `Idempotency-Key`.
- **HTTP Error Mapping:**
  - `401 Unauthorized`: In `__DEV__`, silently attempt one `dev-bootstrap` token refresh via `expo-secure-store` and replay the request once; if it still fails, surface a *"Session expired — tap to reconnect"* error state.
  - `403 / 404`: Display *"Record not found or belongs to another household"* and navigate back safely.
  - `409 Conflict`: Refresh server state (purchase already confirmed / plan already completed) and inform the user without duplicating writes.
  - `413 / 415 / 422`: Show specific validation message (e.g., *"File exceeds 10 MiB limit"*, *"PDF exceeds 5-page limit"*, *"Insufficient stock for FEFO deduction"*).

### 6.2 INR Currency & Metric Formatting Rules
- **INR Formatting (`paise` integer → `₹`):**
  - Backend/API transmits monetary values in **integer paise** (or fixed-point decimal string); client never performs floating-point currency math.
  - Format using `Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2, maximumFractionDigits: 2 })` → e.g., `124550` paise → `₹1,245.50`.
- **Metric Unit Formatting:**
  - **Mass (`g` base):** Display `< 1000 g` as `500 g`; `>= 1000 g` as `1 kg` or `1.25 kg` (trimming trailing `.00`).
  - **Volume (`ml` base):** Display `< 1000 ml` as `250 ml`; `>= 1000 ml` as `1 L` or `1.5 L`.
  - **Count (`pcs` base):** Display as `6 pcs` or `1 pc`.
  - Input fields always allow switching between `g`/`kg` and `ml`/`L`, converting to canonical base units (`g`, `ml`, `pcs`) before display preview and API submission.

### 6.3 Accessibility (WCAG 2.1 AA)
- **Tap Targets:** Minimum `44×44 pt` (iOS) / `48×48 dp` (Android) on all tab items, steppers, checkboxes, and action buttons.
- **Contrast & Semantics:** Text contrast `>= 4.5:1` (never rely on color alone for amber low-confidence or red expiry alerts—always pair with icon + text label). Every interactive element defines `accessibilityLabel` and `accessibilityRole`.

---

## 7. Key UX & Architecture Trade-offs

| Decision | Chosen Approach | Alternative Rejected | Rationale & Trade-off |
|---|---|---|---|
| **Expo Go Compatibility** | Server-side OCR/PDF + `expo-image-picker` / `expo-document-picker` | On-device native OCR (`react-native-vision-camera` + MLKit) | Avoids native build tooling (`prebuild`) and keeps mobile independently runnable in Expo Go; trades offline OCR for simpler, uniform multi-page PDF & PII-redacted server processing. |
| **State Split** | TanStack Query (server) + Zustand (local draft/UI) | Redux Toolkit or pure Context | Eliminates boilerplate for polling OCR jobs and cross-stage cache invalidation while keeping Purchase Review & Cooking Actuals drafts responsive. |
| **Purchase Review Guard** | Mandatory `PENDING_REVIEW` screen with math & confidence highlights | Auto-confirming high-confidence receipts | Prevents silent INR & metric corruption (`HI-6`) at the cost of one explicit confirmation tap. |
| **Cooking Two-Step** | Non-destructive `Start` + editable `Complete` actuals | Deducting expected quantities on `Start` | Reflects real kitchen behavior (partial use, skipped items, substitutions) and preserves FEFO accuracy (`HI-7`). |
