# Pantry-First Dish & Video Discovery — Backend API Specification

All endpoints live under `/api/v1/discover` and require Clerk JWT authentication + `HouseholdContext` (`get_household_context` from `backend/app/core/tenancy.py`), matching existing API conventions.

## 1. Endpoints

### `GET /api/v1/discover/dishes`
List dishes matched and ranked against the active household's pantry.
- **Query Params**:
  - `category`: `"all" | "can_make_now" | "almost_there" | "explore_more"` (default `"all"`)
  - `saved_only`: `bool` (default `false`)
  - `region`: optional string (e.g., `"North Indian"`, `"South Indian"`)
  - `meal_type`: optional string (`"breakfast"`, `"lunch"`, `"dinner"`, `"snack"`, `"beverage"`)
  - `diet_tag`: optional string
  - `q`: optional search query (matches `canonical_name`, `alternative_names`, `search_terms`)
  - `limit`: `int` (default `20`, `1..50`), `offset`: `int` (default `0`)
- **Response (`200 OK`)**:
  ```json
  {
    "items": [
      {
        "id": "uuid",
        "slug": "palak-paneer",
        "canonical_name": "Palak Paneer",
        "alternative_names": ["Spinach Paneer"],
        "cuisine": "Indian",
        "region": "North Indian",
        "meal_types": ["lunch", "dinner"],
        "diet_tags": ["vegetarian", "gluten_free"],
        "prep_minutes": 15,
        "cook_minutes": 25,
        "default_servings": 2,
        "availability_category": "can_make_now",
        "required_total": 6,
        "required_available": 6,
        "required_partial": 0,
        "required_missing": 0,
        "missing_ingredient_names": [],
        "expiring_matches": ["Spinach"],
        "is_saved": false,
        "cached_video_count": 3
      }
    ],
    "counts": { "can_make_now": 4, "almost_there": 8, "explore_more": 10 },
    "total": 22
  }
  ```

### `GET /api/v1/discover/dishes/{dish_id}`
Fetch full dish detail and per-ingredient pantry availability. Does **not** mutate inventory.
- **Response (`200 OK`)**: Dish metadata + `requirements` array:
  ```json
  {
    "id": "uuid",
    "slug": "palak-paneer",
    "canonical_name": "Palak Paneer",
    "description": "...",
    "is_saved": true,
    "availability_category": "missing_ingredients",
    "requirements": [
      {
        "id": "uuid",
        "ingredient_name": "Paneer",
        "variant_id": "paneer",
        "canonical_ingredient_id": "uuid",
        "requirement_type": "required",
        "required_qty": "250.000",
        "required_unit": "g",
        "status": "available",
        "pantry_qty": "400.000",
        "pantry_unit": "g",
        "reason": null,
        "matched_substitution": null
      }
    ]
  }
  ```

### `GET /api/v1/discover/dishes/{dish_id}/videos`
Discover cooking videos for a dish (reads `dish_video_cache` first; calls YouTube Data API v3 only on cache miss and within daily quota).
- **Query Params**: `refresh: bool = false`, `limit: int = 5` (`1..10`)
- **Response (`200 OK`)**:
  ```json
  {
    "dish_id": "uuid",
    "source": "cache",
    "quota_exhausted": false,
    "fallback_search_url": "https://www.youtube.com/results?search_query=Palak+Paneer+recipe+Indian",
    "videos": [
      {
        "id": "uuid",
        "external_video_id": "abc123xyz",
        "title": "Dhaba Style Palak Paneer Recipe",
        "channel_title": "Chef Ranveer Brar",
        "thumbnail_url": "https://i.ytimg.com/vi/abc123xyz/hqdefault.jpg",
        "duration_seconds": 742,
        "embeddable": true,
        "availability_status": "available",
        "watch_url": "https://www.youtube.com/watch?v=abc123xyz"
      }
    ]
  }
  ```

### `POST /api/v1/discover/dishes/{dish_id}/save` & `DELETE /api/v1/discover/dishes/{dish_id}/save`
Save or unsave a dish for the active household (`saved_dishes` table). Idempotent (`200 OK` / `204 No Content`).

### `POST /api/v1/discover/dishes/{dish_id}/feedback`
Record household feedback (`like`, `dislike`, `cooked`, `hide`, optional `rating` 1–5, `notes`). Does **not** deduct inventory.

### `POST /api/v1/discover/dishes/{dish_id}/consume`
Explicit post-cooking confirmation to update pantry stock.
- **Request Body**: `{ "servings": 2, "ingredients": [{ "canonical_ingredient_id": "uuid", "qty": 250, "unit": "g" }], "notes": "..." }`
- **Behavior**: Resolves household `CanonicalItem`s mapped to each confirmed `canonical_ingredient_id` and delegates atomically to `create_cooking_plan` + `complete_cooking_plan` (`backend/app/modules/cooking/service.py`) so `InventoryLot` deductions, `StockLedger` entries, and `MealHistory` stay 100% consistent with existing cooking flows.

## 2. Mobile UX Architecture (`mobile/src/features/discover/`)

- **Primary Tab**: Update the Recipes tab (or add Discover alongside it) in `mobile/app/(tabs)/_layout.tsx` using existing `colors`, `spacing`, `typography` tokens from `mobile/src/shared/theme/tokens.ts`.
- **Discover Screen**:
  - Segmented filter bar: **Can Make Now** | **Missing Ingredients** | **Saved** + region/diet chips + search bar.
  - **Dish Cards**: Dish name, region badge, prep/cook time, availability pill (`"All 6 ready"` in green vs. `"Missing 1: Spinach"` in amber), and Save bookmark icon.
- **Dish Detail & Video Cooking Mode**:
  - **Video Links**: Open YouTube videos externally via `Linking.openURL(watch_url)` + fallback `"Search on YouTube"` link when offline/quota-exhausted (no embedded playback dependencies initially).
  - **Interactive Pantry Checklist**: Grouped by `Required` and `Optional`, showing exact pantry quantities (`"Have 400 g / Need 250 g"`), checkboxes for what the user actually used.
  - **Explicit Action Footer**: `"I Cooked This — Update Pantry"` opens confirmation sheet with editable quantities before calling `POST /api/v1/discover/dishes/{dish_id}/consume`.
  - **Feedback Bar**: Like / Dislike / Hide buttons.
