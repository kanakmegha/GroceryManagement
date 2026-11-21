# Product Requirements Document (PRD) — GroceryManagement

**Project:** `GroceryManagement-AgentBenchmark`  
**Phase:** Phase 1 — Product Definition  
**Status:** Draft for Phase 1 Review  
**Author:** Product Manager (Coordinating Agent)  
**Target Stack:** React Native + Expo (TypeScript) · FastAPI (Python) · Neon PostgreSQL (Authoritative Hosted DB)

---

## 1. Executive Summary & Vision

**GroceryManagement** is a household grocery lifecycle platform that turns raw purchase evidence (camera photos, gallery images, digital PDFs, and scanned multi-page PDFs) into accurate, lot-tracked pantry inventory, real-ingredient recipe discovery, verified cooking consumption, wastage tracking, spending/consumption insights, and data-driven replenishment suggestions.

Unlike generic shopping list apps or grocery delivery aggregators, GroceryManagement closes the full **7-stage household loop**:

```text
PURCHASE ─▶ INVENTORY ─▶ RECIPE DISCOVERY ─▶ COOKING ─▶ CONSUMPTION ─▶ INSIGHTS ─▶ REPLENISHMENT
```

Every quantity change in the pantry is backed by an **append-only stock ledger** and **FEFO (First-Expired, First-Out)** lot accounting, ensuring that household spending in **INR (₹)** and ingredient quantities in **metric units** remain auditable, accurate, and grounded strictly in real household data.

---

## 2. Goals & Non-Goals

### 2.1 Product Goals
- **G1 — Zero-Friction Receipt Capture:** Support receipt camera capture, photo gallery upload, and single/multi-page digital or scanned PDF upload with asynchronous OCR extraction.
- **G2 — Human-Verified Purchase Confirmation:** Provide an editable purchase review screen so users verify/correct OCR-extracted items, quantities, units, and INR totals before any stock is added to inventory.
- **G3 — Auditable Lot-Based Inventory:** Track every confirmed purchase item as an inventory lot with expiry dates and an immutable, append-only stock ledger.
- **G4 — Real-Ingredient Recipe Discovery:** Recommend recipes strictly from real, currently available household inventory (and never fabricate recipes or URLs).
- **G5 — Accurate Cooking & Wastage Accounting:** Separate planning from deduction—starting a cooking plan reserves/previews nothing destructively; only confirmed cooking completion deducts actual used quantities via FEFO and logs wastage separately.
- **G6 — Actionable Household Insights & Replenishment:** Surface INR spending trends, consumption vs. wastage breakdowns, and replenishment suggestions derived from real stock levels and consumption history.
- **G7 — Independent Runnability & Real Auth Isolation:** Ensure the FastAPI backend and Expo mobile app run independently while enforcing genuine backend authentication and strict household data isolation—without showing login/registration UI during development testing.

### 2.2 Explicit Non-Goals
- **NG1 — No Grocery Platform Integrations:** No integrations with Blinkit, Zepto, Swiggy Instamart, BigBasket, Amazon Fresh, or any external ordering/cart APIs.
- **NG2 — No Fake Data or Fabricated URLs:** No mock/seed "demo pantry" presented as real inventory, no fabricated API responses, and no hallucinated or broken recipe URLs.
- **NG3 — No Login/Registration UI in Development Testing:** Development testing must not require a user to type credentials or navigate sign-in/sign-up screens, while backend endpoints still enforce real token/household isolation.
- **NG4 — No Autonomous OCR-to-Inventory Writes:** OCR output must never directly mutate inventory lots or the stock ledger.
- **NG5 — No Speculative Stock Deduction:** Creating or starting a cooking plan must never deduct inventory.

---

## 3. Target Personas

| Persona | Profile | Primary Needs |
|---|---|---|
| **P1: Primary Household Cook & Shopper** | Manages weekly/monthly groceries in an Indian household (INR budgets, metric weights/volumes, mixed kirana/supermarket receipts & PDFs). | Fast receipt capture, easy correction of messy OCR lines, knowing what is about to expire, finding recipes using what is actually in the kitchen. |
| **P2: Household Co-Member** | Shares the same kitchen and pantry with P1. | Viewing up-to-date shared inventory, logging partial consumption or wastage, avoiding duplicate purchases. |
| **P3: Developer / QA Evaluator (Dev-Mode)** | Tests end-to-end flows locally on Expo + FastAPI against Neon PostgreSQL. | Immediate app entry into an authenticated, isolated dev household without login/registration UI blockers, reproducible API behavior, clean empty-state handling. |

---

## 4. Technical & Architectural Context

- **Mobile Client:** React Native + Expo + TypeScript (independently runnable via Expo CLI / dev client, configured via API base URL).
- **Backend API:** FastAPI (Python) (independently runnable via `uvicorn`, exposing OpenAPI/REST endpoints).
- **Database:** Neon PostgreSQL as the **authoritative hosted database** (no SQLite/in-memory fallback in production or integration flows).
- **Isolation:** Every domain record (`purchases`, `receipts`, `inventory_lots`, `stock_ledger`, `cooking_plans`, `consumption_logs`, `insights`) is scoped by `household_id`.

---

## 5. Hard Invariants (Non-Negotiable System Rules)

| ID | Invariant | Verification Criteria |
|---|---|---|
| **HI-1** | **No Grocery Platform Integrations** | Zero external grocery delivery SDKs, cart links, or ordering webhooks exist in backend or mobile. |
| **HI-2** | **No Fake Inventory or Fabricated Responses** | All API responses reflect persisted Neon PostgreSQL state. No hardcoded mock inventory lists or stubbed "success" responses. |
| **HI-3** | **No Fabricated Recipe URLs** | Every recipe returned by Recipe Discovery must either be an internal/curated persisted recipe or have a verified, non-fabricated source reference. External URLs must never be invented by an LLM or static placeholder. |
| **HI-4** | **No Login/Registration UI in Dev Testing + Genuine Backend Auth** | The mobile app renders no login/registration screens in dev testing mode, yet every protected FastAPI route validates a genuine auth token/credential and enforces `household_id` isolation (cross-household access returns `403`/`404`). |
| **HI-5** | **Empty Inventory => Zero Inventory-Based Recommendations** | When a household has `0` available stock across all lots, Recipe Discovery returns `[]` (0 inventory-based recommendations) with a clear empty state. |
| **HI-6** | **OCR Never Directly Updates Inventory** | Receipt upload & OCR extraction only populate a **draft/review** purchase record (`PENDING_REVIEW`). Inventory lots and the stock ledger are written **only** upon explicit user confirmation (`POST /purchases/{id}/confirm`). |
| **HI-7** | **Cooking Plan Start Never Deducts Stock** | Starting a cooking plan (`IN_PROGRESS`) creates no `stock_ledger` debit entries. Only explicit cooking completion confirmation (`POST /cooking-plans/{id}/complete`) deducts actual confirmed ingredient quantities via FEFO. |

---

## 6. Currency & Measurement Rules

### 6.1 Currency (INR)
- All monetary values (receipt line prices, subtotals, taxes, discounts, total spend, wastage cost, insights) are denominated in **Indian Rupees (`INR`, symbol `₹`)**.
- Backend storage uses fixed-point decimal (`NUMERIC(12, 2)` or integer paise) to prevent floating-point rounding drift.
- UI formatting follows Indian/standard INR display (e.g., `₹1,245.50`).

### 6.2 Metric Units & Normalization
- All quantities use **metric units** (plus discrete count `pcs`):
  - **Mass:** `g` (base), `kg` (`1 kg = 1000 g`)
  - **Volume:** `ml` (base), `L` (`1 L = 1000 ml`)
  - **Count:** `pcs` (base)
- Every inventory lot stores both the **display unit/quantity** (as confirmed on receipt) and **canonical base unit/quantity** (`g`, `ml`, or `pcs`) so FEFO deductions, recipe matching, and insights perform exact arithmetic across compatible units (e.g., deducting `250 g` from a `1 kg` lot leaves `750 g`).
- Incompatible unit conversions (e.g., `g` to `ml` without density) are disallowed unless explicitly converted by the user during review or cooking confirmation.

---

## 7. Functional Requirements by Lifecycle Stage

### Stage 1: PURCHASE (Receipt Capture, OCR & Confirmation)
- **FR-1.1 Capture & Upload Sources:** Users can upload receipts via:
  1. Device camera capture (JPEG/PNG).
  2. Device photo gallery upload (JPEG/PNG/HEIC converted as needed).
  3. Digital PDF upload (text-selectable PDF).
  4. Scanned PDF upload (image-based PDF), including **multi-page PDFs**.
- **FR-1.2 Multi-Page PDF Processing:** The backend must extract text/images across **all pages** of a multi-page PDF, preserve page provenance, and merge extracted line items into a single draft purchase for review.
- **FR-1.3 OCR Extraction & Staging:** OCR extracts merchant name, purchase date, currency (`INR`), line items (raw name, normalized ingredient suggestion, quantity, unit, unit price, line total), subtotal, tax/discount, and grand total—storing them in a `PENDING_REVIEW` state. **OCR must never write to `inventory_lots` or `stock_ledger`.**
- **FR-1.4 Editable Purchase Review:** Users can add, edit, split, or delete line items, fix quantities/units/INR amounts, assign/adjust expected expiry dates, and resolve math mismatches (where sum of lines ≠ receipt total) before confirming.
- **FR-1.5 Purchase Confirmation:** Confirming a purchase atomically transitions the purchase to `CONFIRMED`, creates `inventory_lots` for each confirmed item, and appends `PURCHASE_IN` entries to `stock_ledger`. Re-confirming an already confirmed purchase must be idempotent (no duplicate lots).

### Stage 2: INVENTORY (Lot-Based Stock, Expiry & Ledger)
- **FR-2.1 Lot-Based Storage:** Each confirmed purchase line creates a distinct lot with `lot_id`, `household_id`, `ingredient_id`, `initial_qty_base`, `remaining_qty_base`, `base_unit`, `unit_cost_inr`, `purchased_at`, `expires_at`, and `status` (`ACTIVE`, `DEPLETED`, `EXPIRED`, `WRITTEN_OFF`).
- **FR-2.2 Append-Only Stock Ledger:** Every stock movement (`PURCHASE_IN`, `COOK_OUT`, `WASTE_OUT`, `ADJUSTMENT`) is recorded as an immutable ledger row referencing `lot_id`, `delta_qty_base`, `reason`, `reference_id`, and `created_at`. Lot `remaining_qty_base` must always equal the sum of its ledger deltas.
- **FR-2.3 Expiry Tracking & Alerts:** Inventory views surface lots sorted by soonest expiry (`expires_at ASC`), flagging items expiring soon (e.g., within 3 days) and already-expired lots.
- **FR-2.4 Manual Expiry Write-Off:** Users can write off an expired or spoiled lot, recording a `WASTE_OUT` ledger entry for the remaining quantity and marking the lot `WRITTEN_OFF`.

### Stage 3: RECIPE DISCOVERY (Real-Inventory Matching)
- **FR-3.1 Real-Inventory Matching:** Recipe recommendations are computed strictly against active, non-expired household `inventory_lots` (`remaining_qty_base > 0`).
- **FR-3.2 Empty Inventory Behavior:** If the household has zero available inventory, Recipe Discovery **must return zero inventory-based recommendations** and display an actionable empty state prompting receipt capture.
- **FR-3.3 Match Transparency & No Fabricated URLs:** Each recommended recipe displays matched ingredients (available vs. missing/shortfall in metric units) and uses only persisted, verified recipe definitions—never fabricated external URLs.

### Stage 4: COOKING (Planning vs. Confirmed Deduction)
- **FR-4.1 Cooking Plan Creation & Start:** Users can select a recipe (or custom meal) and target servings to create/start a cooking plan (`PLANNED` -> `IN_PROGRESS`). **Starting a cooking plan must NOT deduct stock or write `COOK_OUT` ledger entries.**
- **FR-4.2 Actual Ingredient Confirmation:** Upon finishing cooking, the user reviews pre-filled expected quantities and confirms/edits the **actual quantities** used (supporting partial usage, skipped ingredients, or substitutions) and optional cooking waste.
- **FR-4.3 FEFO Stock Deduction:** Confirming cooking completion executes **FEFO (First-Expired, First-Out)** deduction across active lots of each consumed ingredient (earliest `expires_at` first, then `purchased_at`), appending `COOK_OUT` (and `WASTE_OUT` if logged) entries to `stock_ledger` inside a single DB transaction. Insufficient stock must be rejected or flagged clearly rather than silently creating negative lot balances.

### Stage 5: CONSUMPTION (Usage & Wastage Tracking)
- **FR-5.1 Consumption History:** Every confirmed cooking completion and manual consumption/wastage event is persisted in a queryable timeline showing ingredient, metric quantity, INR cost basis (from lot `unit_cost_inr`), and type (`CONSUMED` vs. `WASTED`).
- **FR-5.2 Wastage Attribution:** Wastage is categorized (e.g., `EXPIRED`, `SPOILED`, `COOKING_LEFTOVER`) with exact INR value lost based on the originating lot's unit cost.

### Stage 6: INSIGHTS (Household Spending & Consumption Analytics)
- **FR-6.1 Spending Insights (INR):** Aggregate confirmed purchase spend in INR by time window (weekly/monthly) and category/ingredient.
- **FR-6.2 Consumption vs. Wastage Insights:** Show total metric quantities and INR value consumed vs. wasted, highlighting top wasted items to help reduce household waste.
- **FR-6.3 Grounded Metrics Only:** All insight numbers must be computed directly from persisted `purchases`, `inventory_lots`, and `stock_ledger` records for the authenticated `household_id`.

### Stage 7: REPLENISHMENT (Data-Driven Restock Suggestions)
- **FR-7.1 Low-Stock & Depletion Detection:** Identify frequently purchased/consumed ingredients whose current active stock is zero or below a household usage threshold (derived from real consumption/purchase history).
- **FR-7.2 Expiry-Aware Suggestions:** Avoid suggesting replenishment for items that already have sufficient active, non-expired stock, and surface wastage warnings if a previously wasted item is suggested.
- **FR-7.3 In-App Checklist Only:** Replenishment produces an in-app restock list/checklist—never external grocery platform links or cart actions.

---

## 8. Non-Functional Requirements

- **NFR-1 Independent Runnability:** FastAPI backend and Expo mobile app must start and run independently with clear `.env` configuration and zero circular runtime coupling.
- **NFR-2 Transactional Integrity:** Purchase confirmation, FEFO cooking deduction, and lot write-off must execute inside ACID PostgreSQL transactions with row-level locking (`SELECT ... FOR UPDATE`) to prevent race-condition negative stock.
- **NFR-3 Authentication & Household Isolation:** Backend validates genuine auth tokens on all household-scoped endpoints. In dev testing, the mobile app bootstraps a dev household session automatically via a backend-issued dev token/session without rendering login/registration UI.
- **NFR-4 Asynchronous OCR Resilience:** Receipt OCR (especially multi-page PDFs) must not block the UI thread; upload returns a job/purchase ID and status can be polled or refreshed cleanly.
- **NFR-5 Accuracy & Auditability:** Ledger sum invariant (`SUM(stock_ledger.delta_qty_base) == inventory_lots.remaining_qty_base`) must hold at all times.

---

## 9. Rationale & Trade-offs for Key Product Decisions

| Decision | Chosen Approach | Alternative Rejected | Rationale & Trade-off |
|---|---|---|---|
| **OCR-to-Inventory Boundary** | Two-step: OCR writes `PENDING_REVIEW` draft; user confirms to write lots. | Direct OCR-to-inventory insertion. | Receipt OCR frequently misreads weights (`500g` vs `5000g`), prices, or abbreviations. Staging prevents corrupting inventory/INR insights at the cost of one extra user tap. |
| **Inventory Accounting** | Lot-based inventory + append-only `stock_ledger` + FEFO. | Single mutable `quantity` column per ingredient. | Mutable quantities cannot track per-batch expiry dates, INR cost basis, or audit trails. Append-only ledger + FEFO enables accurate expiry alerts and wastage valuation, with slightly higher query/schema complexity. |
| **Cooking Deduction Timing** | Deduct only on confirmed cooking completion with editable actuals. | Deduct immediately when starting a cooking plan. | Cooks frequently abort plans, scale servings, or substitute ingredients. Deferring deduction until confirmation preserves pantry accuracy and prevents phantom stockouts. |
| **Dev-Mode Auth UX** | Real backend auth + `household_id` isolation, auto-bootstrapped in dev without login UI. | Completely disabling auth in dev OR forcing login screens during testing. | Disabling auth hides isolation bugs; forcing login UI slows benchmark testing. Auto-bootstrapping a real dev token satisfies both fast testing and genuine security boundaries. |
| **Recipe Sourcing** | Persisted/curated real recipes matched strictly to available lots. | LLM-generated recipes with guessed external URLs. | Prevents hallucinated URLs and ensures deterministic, testable matching (including returning `0` recipes on empty inventory). |
| **Split Runtime Architecture** | Independently runnable FastAPI + Expo RN TS + Neon PostgreSQL. | Monolithic server-rendered app or local SQLite. | Enables true mobile camera/gallery UX, clean API contract testing, and hosted PostgreSQL persistence across devices. |
