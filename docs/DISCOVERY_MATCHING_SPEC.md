# Pantry-First Dish & Video Discovery — Matching & Ranking Specification

## 1. Source of Truth

All pantry availability checks **must** read from `get_ingredient_inventory_summary(db, household_id, today=household_today)` (`backend/app/modules/inventory/ingredient_aggregation_service.py`), which already enforces:
- `InventoryLot.household_id == household_id`
- `InventoryLot.lot_status == "active"`
- `InventoryLot.remaining_qty > 0`
- Non-expired lots (`expires_on is None or expires_on >= household_today`)
- Confirmed `IngredientMapping` (`user_confirmed is True`)
- Aggregation by `CanonicalIngredient` with separate `IngredientUnitGroupOut` buckets (`g`, `ml`, `pcs`, and package units).

Batch-load `CanonicalIngredient` (`id`, `variant_id`, `family_id`, `is_unspecified_variant`) to map pantry summaries to canonical variant/family identities without fuzzy or text-based matching.

## 2. Explicit `requirement_mode` & Variant Rules

1. **`exact_variant`**:
   - Matches **only** active in-stock pantry items whose `variant_id == dish_ingredient.variant_id` (and `not is_unspecified_variant`).
   - `Full Cream Milk` and `Toned Milk` remain strictly distinct; generic/unspecified pantry items never satisfy `exact_variant`.
2. **`compatible_family`**:
   - Matches active in-stock pantry items sharing `family_id`, filtered to `compatible_variant_ids` when that allowlist is non-empty.
   - Variants outside the allowlist never match.
3. **`generic_ingredient`**:
   - Matches active in-stock pantry items by `canonical_ingredient_id` or `family_id`, without mutating or promoting pantry identity.
4. **No cross-variant quantity combining**:
   - Alternative variants in a family/allowlist are evaluated independently and the best candidate is selected; quantities are never summed across different variants.
5. **Verified Substitutions**:
   - Checked **only** when the primary requirement is `missing` or `partial`.
   - Only rules with `verified == true` (matching `applicable_dish_slug` when set) may satisfy a requirement.
   - When `quantity` is specified on the requirement, a substitution applies only when both `conversion_ratio` and `conversion_unit` are explicitly provided (using `Decimal` arithmetic; never assuming 1:1).

## 3. Quantity & Unit Comparison Rules

Using `convert_to_target_unit()` from `ingredient_aggregation_service.py` (`Decimal` arithmetic):
1. **No Quantity Specified (`req_qty is None`)**: Any positive active stock (`lot_count > 0`) → **`available`**.
2. **Comparable Units (`g ↔ kg`, `ml ↔ l`, `pcs ↔ dozen`, `cm ↔ m`, or identical packaging unit)**:
   - `comparable_qty >= req_qty` → **`available`**.
   - `0 < comparable_qty < req_qty` → **`partial`** (`INSUFFICIENT_QUANTITY`).
3. **Incomparable Units (e.g., `200 g` required vs `4 pcs` or `1 bottle` in pantry)**:
   - Never invent cross-dimension conversions.
   - Mark **`partial`** (`INCOMPARABLE_UNIT`) and surface both quantities clearly.

## 4. Dish Classification & Deterministic Tuple Ranking

For each active dish, count **only** `requirement_type == "required"` ingredients (`optional` never affects classification):
- **`can_make_now`**: `0` missing or partial required ingredients.
- **`almost_there`**: `1–3` missing or partial required ingredients.
- **`explore_more`**: `> 3` missing or partial required ingredients.
- **Empty pantry**: Never produces `can_make_now`; dishes appear in `almost_there` (`1–3` required) or `explore_more` (`> 3` required).

**Deterministic Tuple-Based Ranking**:
- Exclude dishes with `dish_feedback.action == "hide"` (unless `include_hidden=true`) and hard-exclude dishes incompatible with `HouseholdRecipePreference.diet_type` or `dietary_restrictions`.
- Sort by tuple:
  1. Bucket tier (`can_make_now` > `almost_there` > `explore_more`) — saved/liked dishes never outrank a higher availability bucket.
  2. Fewer missing/partial required ingredients.
  3. Higher required availability ratio (`required_available / required_total`).
  4. More expiring-soon matched required ingredients (within 5 days of `household_today`).
  5. Household feedback (`like` > neutral > `dislike`), `is_saved`, and `preferred_cuisines` / `preferred_meal_types` / `max_prep_minutes`.
  6. Deterministic tie-breakers: `canonical_name.lower() ASC`, `str(dish_id) ASC`.
