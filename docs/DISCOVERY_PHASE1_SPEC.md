# Pantry-First Discovery — Phase 1 Implementation Specification (Refined)

> **Status**: Refined design specification for review (no application code, models, or migrations modified yet).
> **Approved Product Rules Applied**:
> - Three discovery buckets: **Can Make Now** (`0` missing required), **Almost There** (`1–3` missing required), **Explore More** (`> 3` missing required); `optional` ingredients never count toward missing limits.
> - External YouTube playback (`watch_url` / YouTube app or browser) initially — no embedded playback dependencies.
> - 7-day video metadata cache, 9,000 units/day configurable YouTube quota budget, dish discovery independent of YouTube availability.
> - No destructive changes to existing recipe (`backend/app/modules/recipes/`), inventory (`backend/app/modules/inventory/`), or cooking (`backend/app/modules/cooking/`) tables.

---

## 1. PostgreSQL RLS, Connection Pooling & Tenancy Isolation

### 1.1 Existing Runtime Behaviour (Verified in Code)
- **Database engine & pooling (`backend/app/core/db.py`)**:
  - `build_async_engine` configures PostgreSQL (`postgresql+asyncpg`) with `statement_cache_size=0`, `pool_pre_ping=True`, and `pool_recycle=300`.
  - SQLite (`sqlite+aiosqlite`) sets `check_same_thread=False` and `PRAGMA foreign_keys=ON`.
  - `build_session_maker` sets `expire_on_commit=False` and `autoflush=False`.
  - `get_db` yields an `AsyncSession` per request and rolls back on unhandled exceptions.
- **Household context establishment (`backend/app/core/tenancy.py`)**:
  - `get_household_context` authenticates the Clerk JWT (`get_current_user`), resolves the target household (`X-Household-Id` header or sole membership), and verifies `HouseholdMember` membership in SQL before returning `HouseholdContext(user, household, role)`.
  - Immediately after membership verification (line 151), `get_household_context` calls `await set_pg_rls_context(db, user.id, household.id)`.
- **Transaction-local GUC binding (`set_pg_rls_context`)**:
  - `set_pg_rls_context` checks `_is_postgresql(db)`. When `True`, it executes:
    - `SELECT set_config('app.current_user_id', :uid, true)`
    - `SELECT set_config('app.current_household_id', :hid, true)`
  - Passing `true` as the third argument (`is_local = true`, equivalent to PostgreSQL `SET LOCAL`) binds both GUCs strictly to the **current transaction**.
- **Pooled connection safety & mid-request commit caveat**:
  - Because `app.current_household_id` is transaction-local, returning a connection to SQLAlchemy's async pool after `COMMIT` or `ROLLBACK` automatically clears the GUC — tenant context cannot leak across pooled requests.
  - **Caveat**: If an endpoint calls `await db.commit()` mid-request and then executes another query on the same `AsyncSession`, the new transaction starts with an empty `app.current_household_id` unless `set_pg_rls_context` is re-invoked.
- **SQLite development vs PostgreSQL production**:
  - In SQLite (local dev & unit tests), `_is_postgresql(db)` returns `False`, so `set_pg_rls_context` is a no-op and Alembic migrations skip `ENABLE ROW LEVEL SECURITY` / `CREATE POLICY` (matching `0004_food_consumptions_table.py`).
  - In PostgreSQL, `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` enforce:
    `household_id = NULLIF(current_setting('app.current_household_id', true), '')::uuid`.

### 1.2 Phase 1 RLS Policy Rules
1. **Application-level household authorization remains mandatory** on every query and mutation (`WHERE household_id == ctx.household.id` and `household_id=ctx.household.id` on inserts) in both SQLite and PostgreSQL.
2. **PostgreSQL RLS is applied strictly as defense-in-depth** on the two household-scoped tables (`saved_dishes`, `dish_feedback`) using the exact `0004_food_consumptions_table.py` pattern (`is_postgres = bind.dialect.name == "postgresql"`).
3. **Global tables** (`dishes`, `dish_ingredients`, `video_records`, `dish_video_cache`, `youtube_quota_usage`) have no `household_id` column and **do not** enable RLS.
4. Discovery services must avoid mid-request `commit()` before subsequent household-scoped reads unless `set_pg_rls_context(db, ctx.user.id, ctx.household.id)` is explicitly re-applied.

---

## 2. Dish Ingredient Matching Identity

### 2.1 Explicit `requirement_mode` Semantics
Every row in `dish_ingredients` must declare an explicit `requirement_mode` governing how pantry stock satisfies it:

| `requirement_mode` | Required Identity Fields | Matching Rule against Household Pantry | Non-Promotion & Safety Invariant |
| --- | --- | --- | --- |
| `exact_variant` | `variant_id` (non-null, e.g. `besan`, `basmati_rice`, `paneer`, `moong_dal_split_yellow`) | Matches **only** active in-stock pantry `CanonicalIngredient` rows whose `variant_id == dish_ingredient.variant_id` (or a `verified=True` rule in `substitutions_json`). | Generic/unspecified family variants (`is_unspecified_variant=True`) and sibling variants in the same family (e.g. `chana_dal` vs `moong_dal_split_yellow`) **never** match `exact_variant`. |
| `compatible_family` | `family_id` (non-null, e.g. `onion`, `cooking_oil`) + optional `compatible_variant_ids` allowlist | Matches any active in-stock pantry item whose `family_id == dish_ingredient.family_id` AND (if `compatible_variant_ids` is non-empty) whose `variant_id` is in `compatible_variant_ids`. | Variants outside `family_id` or excluded from `compatible_variant_ids` (e.g. `mustard_oil` excluded from a neutral-oil dessert) **never** silently match. |
| `generic_ingredient` | `family_id` and/or `canonical_ingredient_id` | Matches any active in-stock pantry item sharing that `family_id` or `canonical_ingredient_id` (e.g. `salt`, `coriander_leaves`, `green_chilli`). | A generic pantry item (`is_unspecified_variant=True`) satisfies `generic_ingredient` **without** mutating or promoting the pantry item into a specific variant. |

### 2.2 Interaction with `IngredientCatalogue` & `IngredientResolver`
- **Catalogue validation at seed time**:
  - `IngredientCatalogue` (`backend/app/modules/catalog/ingredient_catalogue.py`, backed by `indian_household_ingredient_catalogue.json`) is the single source of truth for `family_id` (`get_family`) and `variant_id` (`get_variant`).
  - The dish catalogue seeder validates every `dish_ingredients` row against `IngredientCatalogue` before upserting:
    - `exact_variant` rows must resolve `catalogue.get_variant(variant_id) is not None` and `not variant.is_unspecified_variant`.
    - `compatible_family` rows must resolve `catalogue.get_family(family_id) is not None` and every entry in `compatible_variant_ids` must belong to that `family_id`.
    - `generic_ingredient` rows must resolve a valid `family_id` or `canonical_ingredient_id`.
  - `canonical_ingredient_id` on `dish_ingredients` is linked to `canonical_ingredients.id` (populated by `sync_catalogue_to_db` in `ingredient_catalogue_sync.py`) for fast SQL joins.
- **Query-time evaluation (no text-guessing)**:
  - Pantry items already carry resolved `canonical_ingredient_id` / `variant_id` / `family_id` assigned by `IngredientResolver` (`backend/app/modules/catalog/ingredient_resolver.py`) during purchase/pantry intake.
  - Discovery matching queries `inventory_lots` via `get_ingredient_inventory_summary` and compares structured IDs (`variant_id`, `family_id`, `canonical_ingredient_id`) — never fuzzy text matching at discovery time.

---

## 3. Substitution Integrity (`substitutions_json`)

`dish_ingredients.substitutions_json` stores a JSON array of validated substitution objects (`DishSubstitutionRule` Pydantic schema). Free-form text strings are prohibited.

### 3.1 Validated Schema
```json
[
  {
    "original_variant_id": "paneer",
    "substitute_variant_id": "tofu_firm",
    "substitute_family_id": null,
    "substitute_name": "Firm Tofu",
    "verified": true,
    "applicable_dish_slug": "palak-paneer",
    "conversion_ratio": 1.0,
    "conversion_unit": "g",
    "notes": "Press tofu before pan-searing; 1:1 by weight."
  }
]
```

| Field | Type | Constraints & Semantics |
| --- | --- | --- |
| `original_variant_id` | `str \| null` | Catalogue `variant_id` being substituted (defaults to owning `dish_ingredient.variant_id`). |
| `substitute_variant_id` | `str \| null` | Catalogue `variant_id` of the substitute; at least one of `substitute_variant_id` or `substitute_family_id` must be non-null and validated against `IngredientCatalogue`. |
| `substitute_family_id` | `str \| null` | Catalogue `family_id` when any variant in that family is an acceptable substitute. |
| `substitute_name` | `str` | Non-empty human-readable label for checklist UI. |
| `verified` | `bool` | **Mandatory gate**: Only `verified == true` substitutions may mark a missing ingredient as satisfied (`matched_via="verified_substitution"`). |
| `applicable_dish_slug` | `str \| null` | Optional dish-slug guard (`null` means scoped to the owning `dish_ingredients` row's dish). |
| `conversion_ratio` | `float \| null` | Positive float (`> 0`) when quantity conversion is known (e.g., `1.0`), or `null` when qualitative. |
| `conversion_unit` | `str \| null` | Unit string (`"g"`, `"ml"`, `"tbsp"`, etc.) or `null`. |
| `notes` | `str \| null` | Concise cooking guidance shown in the UI. |

### 3.2 Availability Rule
- **Unverified substitutions (`verified: false`) must NEVER count as available** in `Can Make Now` or `Almost There` bucket calculations. They may only be surfaced as informational suggestions on missing-ingredient rows.

---

## 4. Catalogue Versioning, Transactional Seeding & User Data Preservation

- **Stable identity**: `dishes.slug` (e.g., `"palak-paneer"`, `"masala-dosa"`) is the immutable business key across all catalogue releases. `dishes.id` (UUID) is preserved on updates.
- **Deterministic `content_hash`**:
  - `dishes.catalogue_version` (e.g., `"1.0.0"`) records the catalogue release that last wrote the row.
  - `dishes.content_hash` (`String(64)`) is computed deterministically via `sha256(json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))`, covering all dish fields plus the sorted list of normalized `dish_ingredients` definitions.
- **Transactional execution & rollback (`seed_dish_catalogue`)**:
  - The entire catalogue import (all dish upserts, ingredient reconciliations, and soft deactivations) runs inside **one database transaction** (`async with db.begin():` or caller-owned transaction).
  - All `IngredientCatalogue` validations run before/within the transaction; any validation error or DB constraint failure rolls back the entire import safely without leaving partial dishes or orphaned requirements.
- **Idempotent upsert & zero-write no-op**:
  - Existing `dishes` rows are loaded by `slug`. If `existing.content_hash == incoming_hash` and `existing.is_active is True`, **zero database writes** are emitted for that dish or its `dish_ingredients`.
  - When `content_hash` changes (or `is_active` was `False`), `dishes` is updated in place (preserving `dishes.id`) and `dish_ingredients` for that `dish_id` are reconciled in the same transaction (delete existing `dish_ingredients` for `dish_id`, `flush()`, insert the validated new set).
- **Soft deactivation & user data preservation**:
  - Any `dishes` row whose `slug` is absent from the active catalogue is updated to `is_active = False` (never hard-deleted).
  - `saved_dishes` and `dish_feedback` reference `dishes.id` with `ON DELETE RESTRICT`, guaranteeing household bookmarks, ratings, `cooked` logs, and `hide` preferences remain intact across all catalogue updates.

---

## 5. Distributed YouTube Deduplication & Atomic Quota Accounting

### 5.1 Distributed Search Deduplication (PostgreSQL + SQLite Compatible)
- **Why process-local locks are insufficient**: An in-process `asyncio.Lock` only serializes coroutines within a single Python event loop/worker. Under multi-worker Uvicorn/Gunicorn (`--workers N`) or multiple application instances, concurrent requests for the same uncached `(dish_id, query_hash)` can hit separate processes and trigger duplicate YouTube `search.list` calls.
- **Database-backed lease coordination (`dish_video_cache`)**:
  - `dish_video_cache` (`UNIQUE (dish_id, query_hash)`) serves as the shared distributed coordination point via `refresh_lock_token` (`Uuid | None`) and `refresh_locked_until` (`DateTime(timezone=True) | None`, 30-second lease TTL).
  - **Step 1 — Cache hit fast path**: Read `dish_video_cache` for `(dish_id, query_hash)`. If `expires_at > now_utc`, return cached videos immediately (`0` quota, no lock).
  - **Step 2 — Cooldown check**: If `cooldown_until > now_utc`, return stale cache or fallback immediately without calling YouTube.
  - **Step 3 — Process-local `asyncio.Lock` (optimization only)**: Coroutines in the same worker serialize locally first to avoid redundant DB lock contention; correctness never depends on it.
  - **Step 4 — Atomic DB lease claim**:
    1. Ensure row existence via dialect-safe `INSERT ... ON CONFLICT (dish_id, query_hash) DO NOTHING` (supported by both PostgreSQL and SQLite).
    2. Execute a conditional `UPDATE`:
       ```sql
       UPDATE dish_video_cache
       SET refresh_lock_token = :token,
           refresh_locked_until = :now_plus_30s,
           last_attempt_at = :now
       WHERE dish_id = :dish_id
         AND query_hash = :query_hash
         AND (expires_at IS NULL OR expires_at <= :now)
         AND (cooldown_until IS NULL OR cooldown_until <= :now)
         AND (refresh_locked_until IS NULL OR refresh_locked_until <= :now)
       ```
    3. Commit and inspect `result.rowcount`:
       - `rowcount == 1`: This worker holds the exclusive distributed claim and proceeds to quota reservation + YouTube search. Different `(dish_id, query_hash)` pairs never block each other.
       - `rowcount == 0`: Either another worker just populated `expires_at > now` (re-read and return fresh cache), or another worker holds an active lease (`refresh_locked_until > now`). Waiting callers perform bounded jittered polls (e.g., up to ~1s) for fresh cache, then fall back to stale cache/empty fallback without issuing a second search.
  - **Failure & crash recovery**:
    - On search error, the claim holder runs a cleanup update (`WHERE dish_id = :dish_id AND query_hash = :query_hash AND refresh_lock_token = :token`) to clear `refresh_lock_token = NULL`, `refresh_locked_until = NULL`, increment `consecutive_failures`, and set `cooldown_until`.
    - If a worker crashes mid-flight (`SIGKILL`/OOM), `refresh_locked_until` expires automatically after 30 seconds so subsequent requests recover cleanly.

### 5.2 Atomic Quota Accounting
- **Stable, non-secret project identity**:
  - `youtube_quota_usage` is keyed by `UNIQUE (quota_date, quota_project_key)`, where `quota_date` is the current date in `America/Los_Angeles` (matching YouTube Data API v3 midnight PT reset).
  - `quota_project_key` uses `YOUTUBE_QUOTA_PROJECT_ID` if configured, or `f"key_sha256_{sha256(YOUTUBE_API_KEY)[:16]}"` if unset. **No raw API key or credential is ever stored in the database.**
- **Reserve-before-call atomic transaction**:
  1. Ensure the daily row exists via `INSERT ... ON CONFLICT (quota_date, quota_project_key) DO NOTHING`.
  2. Execute a single atomic conditional `UPDATE` in a committed transaction **before** calling YouTube:
     ```sql
     UPDATE youtube_quota_usage
     SET units_used = units_used + :cost,
         search_calls = search_calls + :search_inc,
         video_detail_calls = video_detail_calls + :detail_inc,
         updated_at = :now
     WHERE quota_date = :quota_date
       AND quota_project_key = :quota_project_key
       AND (units_used + :cost) <= :daily_budget
     ```
  3. Check `result.rowcount == 1`:
     - PostgreSQL row-level `UPDATE` locking and SQLite write serialization ensure concurrent requests **cannot** exceed `daily_budget` (default `9,000` units/day; `search.list` = `100` units).
     - If `rowcount == 0`, release the `dish_video_cache` claim token, skip the external call, and return stale cache (`is_stale = True`) or graceful fallback (`fallback_reason = "quota_exhausted"`).
- **Failed request & cache hit policy**:
  - **Cache hits** never enter the reservation path and consume **0 quota units**.
  - **Failed external requests**: Reserved quota is **not refunded** on HTTP 4xx/5xx or network timeouts ("conservative reserve-and-burn"), because YouTube may still count the request against the project quota and undercounting risks exceeding the daily cap.

---

## 6. Database Table Review (All 7 Proposed Tables)

### 6.1 Summary & MVP Necessity Assessment
All 7 tables are genuinely required for the MVP because each serves a distinct lifecycle and isolation boundary:
1. `dishes` — Global canonical dish identity (separate from legacy `recipes` table).
2. `dish_ingredients` — Normalized dish-to-ingredient matching rules (needed for SQL/service-layer `Can Make Now` / `Almost There` calculation).
3. `video_records` — Deduplicated YouTube video metadata (prevents duplicate video rows across dishes/queries).
4. `dish_video_cache` — 7-day TTL + cooldown state per `(dish_id, query_hash)`.
5. `youtube_quota_usage` — Daily quota accounting per `(quota_date, quota_project_key)`.
6. `saved_dishes` — Household-scoped bookmarks (RLS-protected).
7. `dish_feedback` — Household-scoped feedback (`like`, `dislike`, `cooked`, `hide`, `rating`, `notes`) (RLS-protected).

*No inventory duplication*: Pantry stock remains exclusively in `inventory_lots` and `stock_ledger`.

### 6.2 Table-by-Table Schema Specification

#### 1. `dishes` (Global — No RLS)
| Column | Type | Nullable | Default / Constraint | Notes |
| --- | --- | --- | --- | --- |
| `id` | `Uuid(as_uuid=True)` | No | PK, `uuid4` | Immutable dish UUID |
| `slug` | `String(128)` | No | `UNIQUE` | Stable business key (e.g. `palak-paneer`) |
| `canonical_name` | `Text` | No | — | Primary display name |
| `alternative_names` | `JSON` | No | `list` | Synonyms / transliterations |
| `regional_names` | `JSON` | No | `dict` | Map of region/language -> names |
| `cuisine` | `String(64)` | No | `'Indian'` | Primary cuisine |
| `region` | `String(64)` | No | `'Pan-Indian'` | Regional Indian classification |
| `meal_types` | `JSON` | No | `list` | e.g. `["lunch", "dinner"]` |
| `diet_tags` | `JSON` | No | `list` | e.g. `["vegetarian", "gluten_free"]` |
| `description` | `Text` | No | `''` | Short dish summary |
| `prep_minutes` | `Integer` | Yes | `NULL` | Optional prep estimate |
| `cook_minutes` | `Integer` | Yes | `NULL` | Optional cook estimate |
| `default_servings` | `Integer` | No | `2` | Baseline servings |
| `search_terms` | `JSON` | No | `list` | Curated YouTube query terms |
| `catalogue_version` | `String(32)` | No | `'1.0.0'` | Seeder version |
| `content_hash` | `String(64)` | No | `''` | SHA-256 for idempotent seeding |
| `is_active` | `Boolean` | No | `true` | Soft deactivation flag |
| `created_at` | `DateTime(timezone=True)` | No | `_utcnow` | — |
| `updated_at` | `DateTime(timezone=True)` | No | `_utcnow` | — |
- **Indexes**: `ix_dishes_cuisine_region (cuisine, region)`, `ix_dishes_is_active (is_active)`.

#### 2. `dish_ingredients` (Global — No RLS)
| Column | Type | Nullable | Default / Constraint | Notes |
| --- | --- | --- | --- | --- |
| `id` | `Uuid(as_uuid=True)` | No | PK, `uuid4` | — |
| `dish_id` | `Uuid(as_uuid=True)` | No | FK `dishes.id ON DELETE CASCADE` | Owning dish |
| `canonical_ingredient_id` | `Uuid(as_uuid=True)` | Yes | FK `canonical_ingredients.id ON DELETE SET NULL` | Linked catalog row |
| `ingredient_name` | `Text` | No | — | Display label |
| `requirement_mode` | `String(32)` | No | `CHECK IN ('exact_variant', 'compatible_family', 'generic_ingredient')` | Matching mode |
| `requirement_type` | `String(16)` | No | `CHECK IN ('required', 'optional')` | Bucket eligibility |
| `variant_id` | `String(128)` | Yes | — | Required for `exact_variant` |
| `family_id` | `String(128)` | Yes | — | Required for `compatible_family` / `generic_ingredient` |
| `compatible_variant_ids` | `JSON` | No | `list` | Optional allowlist within `family_id` |
| `quantity` | `Numeric(12, 3)` | Yes | `NULL` | Optional display quantity |
| `unit` | `String(32)` | Yes | `NULL` | Optional unit |
| `substitutions_json` | `JSON` | No | `list` | Validated `DishSubstitutionRule[]` |
| `sort_order` | `Integer` | No | `0` | Display ordering |
- **Indexes**: `ix_dish_ingredients_dish_id (dish_id)`, `ix_dish_ingredients_variant_id (variant_id)`, `ix_dish_ingredients_family_id (family_id)`.

#### 3. `video_records` (Global — No RLS)
| Column | Type | Nullable | Default / Constraint | Notes |
| --- | --- | --- | --- | --- |
| `id` | `Uuid(as_uuid=True)` | No | PK, `uuid4` | — |
| `provider` | `String(32)` | No | `'youtube'` | Video provider |
| `external_video_id` | `String(64)` | No | — | YouTube video ID |
| `title` | `Text` | No | — | Video title |
| `channel_id` | `String(128)` | Yes | — | Creator channel ID |
| `channel_name` | `Text` | No | — | Creator name |
| `thumbnail_url` | `Text` | Yes | — | Preview thumbnail |
| `watch_url` | `Text` | No | — | External YouTube URL |
| `duration_seconds` | `Integer` | Yes | `NULL` | Video duration |
| `language` | `String(16)` | Yes | `NULL` | e.g. `en`, `hi`, `ta` |
| `published_at` | `DateTime(timezone=True)` | Yes | `NULL` | Upload timestamp |
| `view_count` | `BigInteger` | Yes | `NULL` | Ranking signal |
| `metadata_json` | `JSON` | No | `dict` | Extra sanitized metadata |
| `fetched_at` | `DateTime(timezone=True)` | No | `_utcnow` | Last metadata refresh |
- **Unique Constraints**: `uq_video_records_provider_external_id (provider, external_video_id)`.

#### 4. `dish_video_cache` (Global — No RLS)
| Column | Type | Nullable | Default / Constraint | Notes |
| --- | --- | --- | --- | --- |
| `id` | `Uuid(as_uuid=True)` | No | PK, `uuid4` | — |
| `dish_id` | `Uuid(as_uuid=True)` | No | FK `dishes.id ON DELETE CASCADE` | Owning dish |
| `query_hash` | `String(64)` | No | — | SHA-256 of normalized query + filters |
| `query_text` | `Text` | No | — | Normalized search string |
| `video_record_ids` | `JSON` | No | `list` | Ordered list of `video_records.id` strings |
| `fetched_at` | `DateTime(timezone=True)` | Yes | `NULL` | Successful fetch timestamp |
| `expires_at` | `DateTime(timezone=True)` | Yes | `NULL` | `fetched_at + 7 days` (`NULL` until first fetch) |
| `last_attempt_at` | `DateTime(timezone=True)` | Yes | `NULL` | Last refresh attempt |
| `cooldown_until` | `DateTime(timezone=True)` | Yes | `NULL` | Prevents retry storms on failure |
| `consecutive_failures` | `Integer` | No | `0` | Backoff counter |
| `refresh_lock_token` | `Uuid(as_uuid=True)` | Yes | `NULL` | Distributed claim owner token |
| `refresh_locked_until` | `DateTime(timezone=True)` | Yes | `NULL` | 30s lease TTL for crash recovery |
- **Unique Constraints**: `uq_dish_video_cache_dish_query (dish_id, query_hash)`.
- **Indexes**: `ix_dish_video_cache_expires_at (expires_at)`.

#### 5. `youtube_quota_usage` (Global — No RLS)
| Column | Type | Nullable | Default / Constraint | Notes |
| --- | --- | --- | --- | --- |
| `id` | `Uuid(as_uuid=True)` | No | PK, `uuid4` | — |
| `quota_date` | `Date` | No | — | UTC/PT quota date |
| `quota_project_key` | `String(64)` | No | — | Project ID or API-key hash prefix |
| `units_used` | `Integer` | No | `0` | Total quota units consumed |
| `search_calls` | `Integer` | No | `0` | Number of `search.list` calls |
| `video_detail_calls` | `Integer` | No | `0` | Number of `videos.list` calls |
| `updated_at` | `DateTime(timezone=True)` | No | `_utcnow` | — |
- **Unique Constraints**: `uq_youtube_quota_date_project (quota_date, quota_project_key)`.

#### 6. `saved_dishes` (Household-Scoped — PostgreSQL RLS Enabled)
| Column | Type | Nullable | Default / Constraint | Notes |
| --- | --- | --- | --- | --- |
| `id` | `Uuid(as_uuid=True)` | No | PK, `uuid4` | — |
| `household_id` | `Uuid(as_uuid=True)` | No | FK `households.id ON DELETE CASCADE` | Tenant owner |
| `dish_id` | `Uuid(as_uuid=True)` | No | FK `dishes.id ON DELETE RESTRICT` | Preserved across catalogue updates |
| `saved_by_user_id` | `Uuid(as_uuid=True)` | Yes | FK `users.id ON DELETE SET NULL` | Audit actor |
| `created_at` | `DateTime(timezone=True)` | No | `_utcnow` | — |
- **Unique Constraints**: `uq_saved_dishes_household_dish (household_id, dish_id)`.
- **Indexes**: `ix_saved_dishes_household_id (household_id)`.

#### 7. `dish_feedback` (Household-Scoped — PostgreSQL RLS Enabled)
| Column | Type | Nullable | Default / Constraint | Notes |
| --- | --- | --- | --- | --- |
| `id` | `Uuid(as_uuid=True)` | No | PK, `uuid4` | — |
| `household_id` | `Uuid(as_uuid=True)` | No | FK `households.id ON DELETE CASCADE` | Tenant owner |
| `dish_id` | `Uuid(as_uuid=True)` | No | FK `dishes.id ON DELETE RESTRICT` | Preserved across catalogue updates |
| `user_id` | `Uuid(as_uuid=True)` | Yes | FK `users.id ON DELETE SET NULL` | Audit actor |
| `action` | `String(24)` | No | `CHECK IN ('like', 'dislike', 'cooked', 'hide')` | Feedback event type |
| `rating` | `Integer` | Yes | `CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5))` | Optional 1–5 rating |
| `notes` | `Text` | Yes | `NULL` | Optional user note |
| `created_at` | `DateTime(timezone=True)` | No | `_utcnow` | — |
- **Indexes**: `ix_dish_feedback_household_dish (household_id, dish_id)`, `ix_dish_feedback_household_action (household_id, action)`.

---

## 7. Next Implementation Step (Pending Review Approval)

Upon approval of this refined specification:
1. Create `backend/app/modules/discovery/models.py` and `backend/app/modules/discovery/schemas.py`.
2. Create Alembic migration `0019_pantry_discovery_tables.py` (`down_revision = "0018_purchase_item_resolution_meta"`) with PostgreSQL RLS policies on `saved_dishes` and `dish_feedback`.
3. Implement `backend/app/modules/discovery/catalogue_data.py` and `catalogue_seed.py` (with `IngredientCatalogue` validation and idempotent upsert).
4. Add Phase 1 unit & migration tests covering RLS/tenancy, matching identity, substitution gating, catalogue upsert/deactivation, and quota/single-flight behavior.
