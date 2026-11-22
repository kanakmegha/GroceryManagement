# Pantry-First Dish & Video Discovery — Product Requirements Document (PRD)

## 1. Goals & Success Criteria

1. **Zero-Friction Pantry Discovery**: Users see **Can Make Now** dishes first, followed by **Missing Ingredients** dishes ranked by availability ratio and missing count.
2. **100% Ingredient Truth**: Every dish requirement references `CanonicalIngredient` / `variant_id` from `indian_household_ingredient_catalogue.json` and `get_ingredient_inventory_summary`—no duplicate inventory calculation.
3. **Quota-Safe Video Discovery**: Stay well within YouTube Data API v3's default **10,000 units/day** (`100 search.list` calls/day at 100 units/call) via persistent DB caching, deduplication, and lazy per-dish fetching.
4. **Explicit-Only Stock Mutation**: Viewing dishes, playing videos, saving dishes, or submitting feedback never mutates `InventoryLot` or `StockLedger`. Stock changes only via explicit user confirmation (`POST /api/v1/cooking-plans` + `POST /api/v1/cooking-plans/{id}/complete` or the discovery consume endpoint).

## 2. Functional Requirements

### FR-1: Dish Catalogue (Separated from Videos)
- Each dish record stores:
  - `slug` (stable identifier), `canonical_name`, `alternative_names` (e.g. *Dal Tadka* / *Yellow Dal Fry*), `cuisine` (e.g. `Indian`), `region` (e.g. `North Indian`, `South Indian`, `West Indian`, `East Indian`, `Pan-Indian`), `meal_types`, `diet_tags` (`vegetarian`, `vegan`, `gluten_free`, etc.), `description`, `prep_minutes`, `cook_minutes`, `default_servings`.
  - `search_terms`: English + Hindi/regional transliterations (e.g., `["paneer bhurji", "पनीर भुर्जी", "paneer bhurji recipe"]`).
  - **Requirements**: `required` vs. `optional` ingredients, `variant_id` / `family_id` / `canonical_ingredient_id` references, optional `quantity` + `unit` (`g`, `ml`, `pcs`), and curated `verified_substitutions`.

### FR-2: Two-Category Pantry Discovery
- **Can Make Now**: All `required` ingredients are satisfied by active, non-expired household pantry stock (or verified in-stock substitutions), with sufficient quantity when units are comparable.
- **Missing Ingredients**: 1 or more `required` ingredients are missing or insufficient (default cap e.g. `max_missing=5` for discovery usefulness, configurable via query param).
- **Per-Ingredient Status**: Each requirement displays `available`, `partial` (in stock but insufficient quantity or incomparable unit needing confirmation), or `missing`, along with matched household pantry quantity/unit.

### FR-3: Video Discovery & Playback
- Opening a dish fetches/returns cached YouTube videos for that dish.
- Displays video thumbnail, title, channel name, duration, view count (where available), embeddability flag, and direct `https://www.youtube.com/watch?v=...` link.
- Supports in-app embed playback when `embeddable=true` and external YouTube app/browser fallback at all times.
- If YouTube quota is exhausted or the provider is unavailable, returns cached videos (if any) or a graceful empty/fallback state with a manual YouTube search link—never failing the dish detail screen.

### FR-4: Save, Feedback & Explicit Cooking Consumption
- Users can **Save / Unsave** dishes (`SavedDish`, household-scoped).
- Users can record **Cooking Feedback** (`like`, `dislike`, `cooked`, `hide`, optional `rating` / `notes`).
- Users can open a **Pantry Checklist** while watching a video and explicitly **Confirm Cooked & Update Stock**, which delegates to `complete_cooking_plan` in `backend/app/modules/cooking/service.py` to write `InventoryLot` deductions, `StockLedger` entries, and `MealHistory`.

## 3. Non-Functional Requirements

- **Household Isolation**: Every household-owned table (`saved_dishes`, `dish_feedback`, plus existing `inventory_lots`, `cooking_plans`, `meal_history`) enforces `household_id` scoping from `get_household_context` (`backend/app/core/tenancy.py`).
- **API Key Protection**: `YOUTUBE_API_KEY` lives exclusively in backend `Settings` (`backend/app/core/config.py`) and is never exposed to `mobile/` or logged.
- **Backwards Compatibility**: Existing `/api/v1/recipes/*` and `/api/v1/cooking-plans/*` routes remain operational.
