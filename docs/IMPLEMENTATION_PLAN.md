# Implementation Plan — GroceryManagement-AgentBenchmark

**Phase:** 1 (Planning & Architecture)  
**Recorded At:** 2026-11-17T14:42:00+05:30  
**Scope:** Phases 2 through 6 (Clean-Room Build in `GroceryManagement-AgentBenchmark/`)

---

## 1. Prerequisites & Approval Gates Before Phase 2
1. **User Approval of Phase 1 Deliverables** (this gate).
2. **Neon Provisioning Approval**: Explicit user approval to create a new dedicated Neon PostgreSQL 18 project (`grocery-management-benchmark`) with `main` + `dev` branches and configure `backend/.env` (never committed).

---

## 2. Phase Breakdown & Disjoint Agent Ownership

### Phase 2: Application & Backend Foundation
- **Goal:** Runnable FastAPI skeleton + Neon PostgreSQL 18 schema/migrations + RLS + genuine JWT & dev-bootstrap + Expo TypeScript app shell.
- **Disjoint Delegation:**
  - `devops-engineer`: `.gitignore`, `backend/pyproject.toml` (`uv`), `backend/.env.example`, `mobile/.env.example`, CI workflow `.github/workflows/ci.yml` (Neon provisioning gated on explicit approval; local/temp DB used for Phase 2 tests).
  - `backend-engineer`: `backend/app/core/*` (`config`, `db`, `security` Clerk JWKS `RS256` verification, `tenancy` RLS `set_config`, `errors`), Alembic migrations (`users` with `clerk_user_id`, `households`, `household_members`, `FORCE ROW LEVEL SECURITY`), `auth` module (`GET /api/v1/auth/me` with default household auto-provisioning), `households` module.
  - `frontend-engineer`: `mobile/` Expo Router + TypeScript scaffold, `@clerk/clerk-expo` + `tokenCache` (`expo-secure-store`), `<SignedIn>` / `<SignedOut>` + `useSSO()`, authenticated API client attaching Clerk `getToken()` + `X-Household-Id`, 5-tab navigation (`Home`, `Inventory`, `Purchases`, `Recipes`, `Settings`).
  - `qa-engineer`: Backend `pytest` + `httpx` AsyncClient suite (ephemeral RSA-2048 + mocked JWKS HTTP transport testing valid/invalid/expired/cross-household Clerk tokens + Alembic migrations), `tsc --noEmit` & mobile Jest/RNTL tests.
- **Exit Gate:** `uv run pytest` passes; `npx tsc --noEmit` passes; backend & mobile start independently.

### Phase 3: Receipt Upload, OCR & Purchase Confirmation
- **Goal:** Camera/gallery/PDF upload, modality-routed extraction, editable purchase review, and atomic `confirm` -> `inventory_lots` + `stock_ledger (PURCHASE_IN)`.
- **Disjoint Delegation:**
  - `ai-ml-engineer`: `backend/app/infra/ocr/*` (`OcrProvider` port, `PyMuPDF` digital PDF text-layer fast-path & scanned PDF rasterizer, Vision-LLM structured adapter, `FixtureOCRAdapter`, PII redaction, deterministic math gate).
  - `backend-engineer`: `backend/app/modules/purchases/*` (multipart upload validation, `purchase_drafts` / `purchase_draft_items`, `PATCH` editing, `item_aliases` learning, atomic `POST /purchases/drafts/{id}/confirm` with `Idempotency-Key`).
  - `frontend-engineer`: `mobile/src/features/purchases/*` (Camera/Gallery/PDF picker, math-mismatch & low-confidence review UI, pack-to-metric preview, confirm mutation).
  - `qa-engineer`: OCR fixture tests, math-mismatch flagging tests, `HI-1` (OCR never mutates inventory), idempotency concurrency tests.
- **Exit Gate:** All Phase 3 unit/integration/UI tests pass; OCR staging invariant verified.

### Phase 4: Inventory & Stock Ledger
- **Goal:** Lot-based inventory views, expiry tracking, manual/waste adjustments, and immutable append-only `stock_ledger`.
- **Disjoint Delegation:**
  - `backend-engineer`: `backend/app/modules/inventory/*` (`GET /inventory/summary`, `GET /inventory/lots`, `POST /inventory/lots/{id}/adjust`, `GET /inventory/ledger`, dynamic expiry evaluation).
  - `frontend-engineer`: `mobile/src/features/inventory/*` (Pantry summary, expiring-soon badges, lot drill-down, adjustment/waste sheet, ledger timeline).
  - `qa-engineer`: Ledger immutability DB trigger tests (`UPDATE`/`DELETE` rejected), `ADJUSTMENT_IN`/`ADJUSTMENT_OUT`/`WASTE_OUT` sign & boundary tests.
- **Exit Gate:** Ledger immutability and lot/expiry queries verified.

### Phase 5: Recipe Recommendations & Cooking Workflow
- **Goal:** Inventory-backed recipe recommendations (`[]` when empty), cooking plan creation/start (zero stock deduction), and confirmed completion with FEFO deduction.
- **Disjoint Delegation:**
  - `backend-engineer`: `backend/app/modules/recipes/*` & `backend/app/modules/cooking/*` (empty-inventory short-circuit `HI-5`, FEFO expiry-urgency ranking, `pg_advisory_xact_lock` sorted by `canonical_item_id ASC` + `FOR UPDATE` deduction on `complete` only `HI-6`/`HI-7`).
  - `frontend-engineer`: `mobile/src/features/recipes/*` & `mobile/src/features/cooking/*` (Recipe discovery + empty state, Start Cooking non-deductive state, Complete Cooking actual-quantity confirmation & waste input).
  - `qa-engineer`: Concurrent FEFO `asyncio.gather` deadlock/race tests, empty-inventory zero-recommendation tests, `start` vs `complete` ledger tests.
- **Exit Gate:** FEFO concurrency and non-deductive start invariants verified.

### Phase 6: Insights, Replenishment, Security Audit & Release Readiness
- **Goal:** Spending/consumption/wastage insights (INR paise), replenishment suggestions & shopping list, full security audit, E2E regression, and benchmark wrap-up.
- **Disjoint Delegation:**
  - `backend-engineer`: `backend/app/modules/insights/*` & `backend/app/modules/replenishment/*`.
  - `frontend-engineer`: `mobile/src/features/insights/*` & `mobile/src/features/replenishment/*`.
  - `security-reviewer`: Full static & runtime security audit (`SEC-01`–`SEC-10`, secret scan, RLS verification, upload/LLM prompt-injection review).
  - `qa-engineer` + `devops-engineer`: Full L1–L4 regression suite (including Playwright Expo-Web E2E), CI verification, and final `BENCHMARK.md` metrics.
- **Exit Gate:** Zero failing tests, zero critical/high security findings, all `HI-1`–`HI-7` and `SEC-01`–`SEC-10` verified.
