# Pantry-First Dish & Video Discovery — System Architecture

## 1. Existing Architecture Audit (Reuse vs. Extend vs. Retire)

### 1.1 Existing Recipe & Cooking Modules
| Component | Current File | Role | Decision |
| --- | --- | --- | --- |
| `HouseholdRecipePreference`, `RecipeFeedback` | `backend/app/modules/recipes/models.py` | Household diet/cuisine prefs & recommendation feedback | **Reuse** preferences; extend/parallel feedback for dishes |
| `catalogue_data.py`, `catalogue_steps.py`, `quality_validator.py` | `backend/app/modules/recipes/` | 64 static recipes & step quality validator | **Keep intact** (no deletion); retire as primary Home/Recipes feed |
| `context_engine.py`, `service.py`, `grounding.py` | `backend/app/modules/recipes/` | Context-aware ranking, HF LLM calls, pantry grounding | **Retire from primary discovery path**; keep endpoints working |
| `recipes/router.py` | `backend/app/modules/recipes/router.py` | `/api/v1/recipes/*` | **Keep intact** for backwards compatibility |
| `CookingPlan`, `MealHistory`, `complete_cooking_plan` | `backend/app/modules/cooking/` (`models.py`, `service.py`, `router.py`, `meals_router.py`) | Explicit cooking session & atomic `InventoryLot` + `StockLedger` deduction | **Reuse directly** for explicit post-video stock deduction |
| `RecipesScreen.tsx`, `recipeHelpers.ts` | `mobile/src/features/recipes/` | Recipe feed, detail modal, cooking mode | **Reuse UI patterns/tokens & checklist**; add/switch primary tab to Discover |

### 1.2 Existing Pantry & Inventory Modules (Source of Truth)
| Component | Current File | Role | Decision |
| --- | --- | --- | --- |
| `CanonicalIngredient`, `IngredientMapping`, `IngredientAlias`, `InventoryLot`, `StockLedger` | `backend/app/modules/inventory/models.py` | Canonical ingredient catalogue, household mappings, lots, ledger | **Reuse as sole source of truth** |
| `get_ingredient_inventory_summary` | `backend/app/modules/inventory/ingredient_aggregation_service.py` | Aggregates active, non-expired `InventoryLot`s (`remaining_qty > 0`) by `CanonicalIngredient` with unit groups (`g`, `ml`, `pcs`) | **Reuse directly** for pantry availability |
| `IngredientCatalogue`, `IngredientResolver` | `backend/app/modules/catalog/` (`ingredient_catalogue.py`, `ingredient_resolver.py`) | Variant/family hierarchy (`full_cream_milk` vs `toned_milk` vs `milk`), alias rules | **Reuse** variant & family rules |
| `convert_to_target_unit` / unit normalization | `backend/app/modules/catalog/measurement_service.py` & `app/core/units.py` | Metric mass/volume conversions (`kg↔g`, `l↔ml`) without unsafe cross-dimension conversions | **Reuse** |

## 2. Proposed Architecture (`backend/app/modules/discovery/`)

```
Mobile (Expo)
  ├─ DiscoverScreen (Can Make Now | Missing Ingredients | Saved)
  ├─ DishDetailScreen (Checklist + Video Carousel + Player + Feedback)
  └─ Explicit Confirm Modal ──▶ POST /api/v1/cooking-plans + /complete
          │
          ▼ (HTTPS + Clerk JWT + X-Household-Id)
FastAPI Backend
  ├─ discovery/router.py (/api/v1/discover/*)
  │     ├──▶ discovery/matcher.py ──▶ get_ingredient_inventory_summary()
  │     │                         ──▶ Dish + DishIngredient tables
  │     ├──▶ discovery/video_service.py ──▶ VideoSearchCache + VideoRecord
  │     │                               ──▶ YouTubeDataClient (quota-guarded)
  │     └──▶ cooking/service.py (complete_cooking_plan for explicit stock deduction)
```

## 3. Minimal Database Schema Additions

All new tables live in a dedicated migration (after `0018_purchase_item_resolution_meta`) without altering historical migrations or duplicating `InventoryLot` data:

1. **`dishes`** (global curated catalogue, no `household_id`):
   - `id` (UUID PK), `slug` (unique), `canonical_name`, `alternative_names` (JSON), `cuisine`, `region`, `meal_types` (JSON), `diet_tags` (JSON), `description`, `prep_minutes`, `cook_minutes`, `default_servings`, `search_terms` (JSON), `is_active`, `created_at`, `updated_at`.
2. **`dish_ingredients`** (global requirements per dish):
   - `id` (UUID PK), `dish_id` (FK `dishes.id` CASCADE), `canonical_ingredient_id` (nullable FK `canonical_ingredients.id`), `ingredient_name`, `variant_id` (nullable string matching catalogue), `family_id` (nullable string), `requirement_type` (`"required"` | `"optional"`), `quantity` (Numeric(12,3) nullable), `unit` (`"g"` | `"ml"` | `"pcs"` nullable), `substitutions_json` (JSON list of `{variant_id, canonical_ingredient_id, label}`), `sort_order`.
3. **`video_records`** (global normalized YouTube video metadata):
   - `id` (UUID PK), `provider` (`"youtube"`), `external_video_id` (unique with `provider`), `title`, `channel_id`, `channel_title`, `thumbnail_url`, `duration_seconds`, `embeddable` (bool), `availability_status` (`"available"` | `"unavailable"` | `"restricted"`), `watch_url`, `published_at`, `fetched_at`, `expires_at`.
4. **`dish_video_cache`** (maps dishes & query hashes to cached `video_records` + quota tracking):
   - `id` (UUID PK), `dish_id` (FK `dishes.id` CASCADE), `query_text`, `query_hash` (indexed), `video_ids_json` (ordered list of `video_records.id`), `total_results`, `fetched_at`, `expires_at` (e.g. 7-day TTL).
5. **`youtube_quota_usage`** (daily quota ledger):
   - `quota_date` (Date PK, UTC/Pacific PT), `units_used` (Integer), `search_calls` (Integer), `updated_at`.
6. **`saved_dishes`** (household-scoped):
   - `id` (UUID PK), `household_id` (FK `households.id` CASCADE), `dish_id` (FK `dishes.id` CASCADE), `created_at`; unique `(household_id, dish_id)`.
7. **`dish_feedback`** (household-scoped):
   - `id` (UUID PK), `household_id` (FK `households.id` CASCADE), `dish_id` (FK `dishes.id` CASCADE), `action` (`"like"` | `"dislike"` | `"cooked"` | `"hide"`), `rating` (Integer nullable), `notes` (Text nullable), `created_at`; index `(household_id, dish_id)`.

## 4. Video Discovery Integration (YouTube Data API v3)

- **Quota Budget**: YouTube Data API v3 default quota is **10,000 units/day**; `search.list` costs **100 units/call** (**100 searches/day max**), while `videos.list` (`contentDetails,status,snippet`) costs **1 unit/call** (up to 50 video IDs batched).
- **Query Generation**: Constructed deterministically from `dish.canonical_name` + `dish.search_terms` + Indian cooking qualifiers (e.g., `"Palak Paneer recipe Indian"`), filtered with `type=video`, `videoEmbeddable=true` (or checked via `videos.list` `status.embeddable`), `safeSearch=moderate`, `relevanceLanguage=en` / `hi`.
- **Quota-Aware Caching & Scheduling**:
  1. Always check `dish_video_cache` first (`expires_at > now()`). Cache hit = **0 quota units**.
  2. On cache miss, check `youtube_quota_usage` for today (`units_used + 101 <= daily_budget`, default e.g. `9000` reserve).
  3. Deduplicate by `(provider, external_video_id)` and channel diversity (avoid 5 videos from the same channel if alternatives exist).
  4. If quota is exhausted or YouTube returns `403 quotaExceeded` / network error, return stale cache if available, otherwise return `videos: []` with `quota_exhausted: true` and `fallback_search_url` (`https://www.youtube.com/results?search_query=...`) so the user can still open YouTube directly.
- **Compliance**: No HTML scraping, no `yt-dlp`, no downloading or rehosting video files.

## 5. Initial Curated Indian Dish Catalogue Plan (50–100 Dishes, Not Yet Populated)

Organized across regions & meal types for Phase 2 seeding:
- **North Indian (20)**: Dal Tadka, Dal Makhani, Rajma Masala, Chole Masala, Paneer Butter Masala, Palak Paneer, Kadai Paneer, Paneer Bhurji, Aloo Gobi, Aloo Matar, Bhindi Masala, Baingan Bharta, Matar Paneer, Shahi Paneer, Dum Aloo, Jeera Rice, Veg Pulao, Aloo Paratha, Methi Thepla/Paratha, Chana Masala.
- **South Indian (15)**: Masala Dosa, Idli Sambar, Medu Vada, Upma, Ven Pongal, Curd Rice, Lemon Rice, Tamarind Rice (Puliyogare), Coconut Chutney, Tomato Rasam, Vegetable Kurma, Avial, Bisi Bele Bath, Pesarattu, Paniyaram.
- **West & Central Indian (10)**: Poha (Kanda Poha), Sabudana Khichdi, Misal Pav, Pav Bhaji, Dhokla (Khaman), Undhiyu, Dal Baati, Bharli Vangi, Sev Tamatar, Methi Thepla.
- **East Indian (8)**: Aloo Posto, Cholar Dal, Luchi Aloo Dum, Begun Bhaja, Dalma, Khichuri, Shukto, Ghugni.
- **Everyday Staples, Breakfast & Beverages (12)**: Moong Dal Khichdi, Vegetable Khichdi, Masala Chai, Haldi Doodh (Turmeric Milk), Besan Chilla, Moong Dal Chilla, Suji Halwa, Kheer, Raita (Boondi/Cucumber), Egg Bhurji, Egg Curry, Chicken Curry (Homestyle).
*(Total: 65 initial dishes, expandable to 100 before launch.)*
