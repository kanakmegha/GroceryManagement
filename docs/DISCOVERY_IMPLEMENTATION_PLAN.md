# Pantry-First Dish & Video Discovery — Implementation Plan

## Recommended Implementation Order

### Phase 1 — Database Schema & Catalogue Foundation (Backend)
1. Add SQLAlchemy models in `backend/app/modules/discovery/models.py`: `Dish`, `DishIngredient`, `VideoRecord`, `DishVideoCache`, `YouTubeQuotaUsage`, `SavedDish`, `DishFeedback`.
2. Create Alembic migration `0019_pantry_discovery_tables.py` (strictly additive; no edits to `0001`–`0018`).
3. Add `YOUTUBE_API_KEY: str = ""` and `YOUTUBE_DAILY_QUOTA_BUDGET: int = 9000` to `Settings` in `backend/app/core/config.py` and `.env.example`.

### Phase 2 — Curated Indian Dish Catalogue (50–100 Dishes)
1. Author `backend/app/modules/discovery/catalogue_data.json` with 65+ regional Indian dishes mapped to verified `variant_id` / `family_id` entries from `indian_household_ingredient_catalogue.json` (`backend/app/modules/inventory/data/`).
2. Add idempotent seed/sync helper (`sync_dish_catalogue_to_db`) and validation test verifying every `variant_id` / `family_id` exists in `IngredientCatalogue`.

### Phase 3 — Deterministic Pantry Matcher
1. Implement `backend/app/modules/discovery/matcher.py` consuming `get_ingredient_inventory_summary(db, household_id)` (`backend/app/modules/inventory/ingredient_aggregation_service.py`) and `HouseholdRecipePreference`.
2. Implement unit/variant/substitution/ranking tests per `DISCOVERY_TEST_PLAN.md`.

### Phase 4 — YouTube Data API v3 Client & Quota-Aware Cache
1. Implement `backend/app/infra/videos/youtube_client.py` (async `httpx` client with timeout, `search.list` + batched `videos.list`, no scraping).
2. Implement `backend/app/modules/discovery/video_service.py` (cache lookup, TTL, deduplication, `youtube_quota_usage` enforcement, `fallback_search_url`).

### Phase 5 — Discovery API & Explicit Cooking Consumption
1. Implement `backend/app/modules/discovery/router.py` (`/api/v1/discover/*`) and register in `backend/app/main.py`.
2. Wire `POST /api/v1/discover/dishes/{id}/consume` to `create_cooking_plan` + `complete_cooking_plan` (`backend/app/modules/cooking/service.py`).
3. Run full backend integration tests + Ruff + Alembic verification.

### Phase 6 — Mobile Discover UI (`mobile/src/features/discover/`)
1. Add `DiscoverScreen` (`Can Make Now` / `Missing Ingredients` / `Saved`), `DishDetailScreen` (ingredient checklist, video cards/player, external YouTube fallback, feedback, explicit `"I Cooked This"` stock confirmation).
2. Wire into `mobile/app/(tabs)/_layout.tsx` (keeping existing cooking/pantry flows intact).
3. Run `npx tsc --noEmit` and `npm test -- --runInBand`.

## Unresolved Product Questions for Review
1. **Tab Navigation**: Should `Discover` replace the `Recipes` tab in `mobile/app/(tabs)/_layout.tsx` (with legacy AI/static recipes moved under a secondary link), or sit alongside it?
2. **YouTube Playback Package**: Should in-app playback use `react-native-webview` (Expo Go compatible without native rebuild) or `expo-video` / external YouTube app deep-link first?
3. **Default `max_missing` Cap**: Confirm whether `Missing Ingredients` should default to `max_missing=5` and whether households with an empty pantry should see popular Indian staples instead of an empty list.
4. **Daily Quota Allocation**: Confirm `YOUTUBE_DAILY_QUOTA_BUDGET = 9000` (leaving 1,000 units/day headroom out of 10,000) and `7 days` cache TTL for `dish_video_cache`.
