# Pantry-First Dish & Video Discovery — Test Plan

## 1. Backend Unit & Integration Tests (`backend/tests/`)

1. **Ingredient Variant Compatibility**:
   - Dish requiring `full_cream_milk` matches `Full Cream Milk` in pantry, does **not** match `Toned Milk` or generic `Milk`.
   - Dish requiring generic `milk` (`family_id="dairy_milk"`) matches `Full Cream Milk`, `Toned Milk`, or generic `Milk`.
2. **Pantry Quantity Sufficiency & Unit Safety**:
   - Comparable units (`1 kg` in pantry satisfies `500 g` required → `available`; `200 g` in pantry for `500 g` required → `partial`).
   - Incomparable units (`4 pcs` Onion in pantry for `200 g` required → `partial` with `INCOMPARABLE_UNIT`, never invented `pcs→g` conversion).
   - Expired lots and `remaining_qty == 0` lots are excluded (verified via `get_ingredient_inventory_summary`).
3. **Missing, Optional & Substituted Ingredients**:
   - Missing optional ingredient does **not** demote a dish out of `can_make_now`.
   - Missing required ingredient without substitution places dish in `missing_ingredients`.
   - Missing required ingredient with an in-stock curated substitution in `substitutions_json` satisfies the requirement (`substituted_count == 1`).
4. **Household Isolation**:
   - Household A's pantry, `saved_dishes`, and `dish_feedback` are never visible to Household B; Household B receives `404` or empty household-scoped state.
5. **YouTube Video Discovery & Caching (Mocked `httpx`)**:
   - Cache hit returns stored `video_records` with **0** YouTube API calls and **0** `youtube_quota_usage` increment.
   - Cache miss calls mocked YouTube `search.list` + `videos.list`, persists `video_records` + `dish_video_cache`, and increments `youtube_quota_usage` by `101`.
   - Duplicate `external_video_id` across queries reuses the existing `video_records` row without unique-constraint errors.
   - Unavailable/private/non-embeddable videos (`embeddable=false` or `availability_status="unavailable"`) are flagged properly and still provide `watch_url` / filtered gracefully.
   - Quota exhaustion (`units_used + 101 > daily_budget` or HTTP `403 quotaExceeded`) does **not** crash: returns stale cache if present, or `videos=[]`, `quota_exhausted=true`, `fallback_search_url`.
6. **No Automatic Stock Deduction & Explicit Consumption**:
   - `GET /dishes`, `GET /dishes/{id}`, `GET /dishes/{id}/videos`, `POST /save`, `POST /feedback` leave `InventoryLot.remaining_qty` and `StockLedger` counts unchanged.
   - `POST /dishes/{id}/consume` creates `CookingPlan` + `MealHistory` + `StockLedger` deductions only for explicitly confirmed ingredients.

## 2. Mobile Jest & TypeScript Tests (`mobile/src/features/discover/__tests__/`)

- `DiscoverScreen` renders `Can Make Now` and `Missing Ingredients` tabs, switches filters, and preserves existing `ApiClient` single-flight auth & timeout behavior.
- `DishDetailScreen` renders ingredient availability badges (`available`, `partial`, `missing`), YouTube embed + external `watch_url` fallback, and does not call `/consume` until explicit confirmation.
- `npx tsc --noEmit` and `npm test -- --runInBand` pass with 0 errors.
