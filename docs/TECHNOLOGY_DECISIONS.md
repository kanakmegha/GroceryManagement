# Technology Decisions — GroceryManagement-AgentBenchmark

**Phase:** 1 (Product Requirements, Research & Architecture)  
**Recorded At:** 2026-11-17T14:42:00+05:30  
**Contributors:** `product-researcher`, `ai-ml-engineer`, `product-architect`, `devops-engineer`, `backend-engineer`, `frontend-engineer`, `security-reviewer`, `qa-engineer`, Coordinator

---

## 1. Resolution of Phase 0 Disagreements

### TD-01: Receipt Extraction — Modality-Routed Vision-LLM-First + Digital PDF Fast-Path
- **Disagreement:** `product-researcher` (Phase 0) proposed OCR-Anchored Vision-LLM (`PaddleOCR` + Vision-LLM), whereas `ai-ml-engineer` (Phase 0) proposed Vision-LLM-First with `PaddleOCR` only as an optional fallback.
- **Decision:** **Modality-Routed Vision-LLM-First + Digital PDF Text-Layer Fast-Path** (no local `PaddleOCR`, `PyTorch`, or ML containers).
  1. **Digital PDFs:** `pymupdf` (`fitz`) / `pdfplumber` text-layer gate (`>= 50` readable chars/page) → direct Text-LLM structured JSON extraction (near-100% digit accuracy, `<1.5s`, ~0 vision token cost).
  2. **Camera / Gallery Images:** Client-side `expo-image-manipulator` (EXIF auto-orient, max edge `<= 2048px`, JPEG 85%) → Vision-LLM structured JSON extraction.
  3. **Scanned / Multi-Page PDFs:** `pymupdf` rasterizes only image-only pages in-memory at 150–200 DPI (bounded to max 5 pages) → Vision-LLM.
  4. **Deterministic Math Gate + HITL Staging:** FastAPI verifies `|sum(line_total_paise) + tax_paise - discount_paise - grand_total_paise| <= 200` (₹2.00) and flags mismatches with `needs_review = true` in `purchase_drafts`.
- **Rationale:** Local `PaddleOCR`/`PyTorch` requires **1.5–5.5 GiB** of disk and adds a heavy ML worker; host disk has only **13 GiB free**. Cloud OCR + LLM doubles latency and adds $1.50–$10/1k pages.
- **Trade-offs:** Relies on external LLM API for non-digital receipts and accepts minor digit-hallucination risk on blurry thermal paper, which is mitigated by the deterministic math gate and mandatory HITL review before inventory mutation (`HI-1`).

### TD-02: Product Normalization — Relational `item_aliases` + Deterministic Parser (Reject `pgvector`, Defer `pg_trgm`)
- **Disagreement:** `ai-ml-engineer` (Phase 0) proposed `pgvector` (HNSW) + `pg_trgm`, while `product-architect` proposed standard relational tables.
- **Decision:** **Reject `pgvector`. Defer `pg_trgm` for MVP.** Use a **Deterministic Regex/Unit Pre-Parser + Relational `item_aliases` table** (`UNIQUE(household_id, normalized_alias)`), with user-confirmed alias learning on draft confirmation.
- **Rationale:** Avoids unnecessary AI/vector infrastructure (embedding API calls, vector sync, HNSW RAM). Furthermore, dense embeddings blur critical grocery SKU differences (`500g` vs `1kg`, `Toned` vs `Full Cream`). Household catalogs are `<2,000` items, so B-tree exact alias lookup resolves repeat purchases deterministically in `<1 ms`.
- **Trade-offs:** Cold-start / unaliased items require user selection in the HITL review UI (or LLM-suggested category/canonical name) on first purchase; `pg_trgm` can be added via a 1-line Neon migration later if fuzzy catalog search is needed.

### TD-03: Money Representation — Decimal INR at Extraction Boundary, Integer Paise (`BIGINT`) in DB & API
- **Disagreement:** `product-architect` used `BIGINT` paise everywhere; `ai-ml-engineer` used decimal INR floats in the extraction schema.
- **Decision:** **Extract `Decimal` INR at the LLM/OCR boundary; convert deterministically in FastAPI (`Decimal.quantize("0.01", ROUND_HALF_UP) * 100`) to `BIGINT` paise.** Store `initial_cost_paise BIGINT` on `inventory_lots` (rather than truncated per-gram `unit_cost_paise`) so proportional consumption/waste cost (`(deducted_qty / initial_qty) * initial_cost_paise`) avoids integer rounding loss on cheap bulk items (e.g., ₹25/1000g).
- **Rationale:** Receipts print decimal INR (`₹149.50`), so asking an LLM to multiply by 100 inline causes 10x/100x errors, while storing floats in PostgreSQL causes IEEE-754 drift.
- **Trade-offs:** Adds a small deterministic boundary converter and proportional cost helper in FastAPI.

### TD-04: Quantity & Unit Representation — Raw Pack Units at Extraction Boundary, Metric Base Units (`g`, `ml`, `pcs`) in DB
- **Disagreement:** `ai-ml-engineer` extracted mixed pack units (`kg`, `l`, `pack`, `dozen`), while `product-architect` enforced `g`, `ml`, `pcs`.
- **Decision:** **Preserve raw pack fields (`pack_count`, `pack_size_value`, `pack_size_unit`, `raw_unit_text`) on `purchase_draft_items`, and deterministically convert to `base_unit IN ('g', 'ml', 'pcs')` (`NUMERIC(12, 3)`) in FastAPI before persistence.** Enforce matching `base_unit` dimensions (`g` vs `ml` vs `pcs`) between `canonical_items`, `purchase_draft_items`, and `recipe_ingredients`.
- **Rationale:** Preserves receipt provenance for HITL review while guaranteeing uniform FEFO deduction and recipe matching.
- **Trade-offs:** Multi-packs without weight/volume (e.g., `"1 pack"`) default to `pcs` unless the user specifies inner metric size during review.

---

## 2. Platform, Database & Runtime Decisions

### TD-05: Authoritative Database — Hosted Neon PostgreSQL 18 (Dedicated Project)
- **Decision:** Create a **new, dedicated Neon PostgreSQL 18 project** (e.g., `grocery-management-benchmark`) with `main` (protected) and `dev` / ephemeral `test` branches, rather than reusing any of the 5 existing Neon projects (`AltTextPipeline` active, `TerritoryRun`/`Evergreen`/`Mixtape`/`SourceBin` archived) or running local Docker Postgres.
- **Rationale:** Zero local disk footprint (~500 MB+ saved on 13 GiB host), clean-room isolation from existing projects, instant copy-on-write test branching.
- **Trade-offs:** Requires internet connectivity during backend tests; `asyncpg` against Neon `-pooler` (PgBouncer transaction mode) requires `statement_cache_size=0`, `pool_pre_ping=True`, `pool_recycle=300`, and a separate direct URL (`DIRECT_DATABASE_URL`) for Alembic migrations.

### TD-06: Independent Local Runtimes — `uv` (FastAPI) & Expo Go (Mobile)
- **Decision:** Run FastAPI locally via `uv` + Python 3.11 `.venv` (~150 MB) and Mobile via Node 22 + Expo Go (`npx expo start`), without local Docker app containers or bare native Xcode/Android builds.
- **Rationale:** Respects the 13 GiB disk constraint (avoids 1.5+ GB Docker images and 4–8 GiB Xcode/Gradle build caches) and keeps backend and mobile independently runnable.
- **Trade-offs:** Expo Go constrains native modules to Expo SDK's bundled set (sufficient for camera, image picker, document picker, secure store, network).

### TD-07: Clerk-Managed Authentication & RLS Alignment (Supersedes Custom JWT / Dev-Bootstrap)
- **Decision:** Adopt **Clerk** as the sole authentication provider, superseding the Phase 1 custom JWT and `dev-bootstrap` proposal (`RW-01`):
  1. **Mobile (`@clerk/clerk-expo`):** Uses `ClerkProvider` + `@clerk/clerk-expo/token-cache` (`expo-secure-store`) for persistent sessions and Clerk's supported `<SignedIn>` / `<SignedOut>` + `useSSO()` flow (no custom login/registration forms).
  2. **Backend (`PyJWT[crypto]` + async `httpx` JWKS):** Verifies Clerk `RS256` JWTs (`exp`, `nbf`, `iss`, `azp`), maps `sub` -> `users.clerk_user_id` via `SECURITY DEFINER` `upsert_clerk_user`, and auto-provisions a default `"My Kitchen"` household on `GET /api/v1/auth/me` if the user has 0 households.
  3. **RLS & `household_members`:** Sets both `app.current_user_id` and `app.current_household_id` via transaction-local `set_config(..., true)` inside explicit `BEGIN ... COMMIT` transactions, with `FORCE ROW LEVEL SECURITY` on all tenant tables.
  4. **FEFO Deadlock Prevention & Lot Cost:** Sort cooking-plan ingredients by `canonical_item_id ASC` before `pg_advisory_xact_lock` + `FOR UPDATE`; store `initial_cost_paise BIGINT` on `inventory_lots`.
