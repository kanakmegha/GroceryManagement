# Pantry-First Dish & Video Discovery — Matching & Ranking Specification

## 1. Source of Truth

All pantry availability checks **must** read from `get_ingredient_inventory_summary(db, household_id)` (`backend/app/modules/inventory/ingredient_aggregation_service.py`), which already enforces:
- `InventoryLot.household_id == household_id`
- `InventoryLot.lot_status == "active"`
- `InventoryLot.remaining_qty > 0`
- Non-expired lots (`expires_on is None or expires_on >= today`)
- Aggregation by `CanonicalIngredient` with separate `IngredientUnitGroupOut` buckets (`g`, `ml`, `pcs`, and package units).

## 2. Variant & Compatibility Rules

1. **Exact Variant Match First**:
   - If a dish requires a specific variant (e.g., `full_cream_milk` or `toned_milk`), match **only** household stock mapped to that `variant_id` (or `canonical_ingredient_id`).
   - `Full Cream Milk` and `Toned Milk` must **never** substitute for each other unless explicitly listed in `substitutions_json`.
2. **Unspecified / Generic Family Requirement**:
   - If a dish requires a generic family-level ingredient (e.g., `milk` with `is_unspecified_variant=True` or `family_id="dairy_milk"` and no required specific variant), any in-stock variant within that `family_id` (e.g., `full_cream_milk` or `toned_milk` or `milk`) satisfies the requirement.
   - Conversely, generic `Milk` in the pantry must **not** silently satisfy a dish that strictly requires `Full Cream Milk` or `Toned Milk` (mark as `partial` / `needs_verification` or `missing` unless listed as an explicit substitution).
3. **Verified Substitutions**:
   - Checked **only** when the primary required ingredient is `missing` or `insufficient`.
   - Each entry in `substitutions_json` specifies `{variant_id, canonical_ingredient_id, label}` (e.g., `Toor Dal` ↔ `Masoor Dal` in everyday dal, or `Lemon` ↔ `Amchur` where explicitly curated). Never invent substitutions dynamically.

## 3. Quantity & Unit Comparison Rules

Let `req_qty` and `req_unit` be the dish requirement, and `pantry_groups` be the `unit_groups` from `IngredientSummaryOut`:
1. **No Quantity Specified (`req_qty is None`)**:
   - If the household has any active stock (`total_remaining_qty > 0` or any non-empty `unit_groups`), status = **`available`**.
2. **Comparable Units (`req_unit` in `{"g", "kg"}` or `{"ml", "l"}` or `"pcs"`)**:
   - Convert both to base unit (`g`, `ml`, or `pcs`) using `app/core/units.py` / `measurement_service.py` (`1 kg = 1000 g`, `1 l = 1000 ml`).
   - If `pantry_base_qty >= req_base_qty`: status = **`available`**.
   - If `0 < pantry_base_qty < req_base_qty`: status = **`partial`** (insufficient quantity).
3. **Incomparable Units (e.g., dish needs `200 g` Onion, pantry has `4 pcs` Onion)**:
   - **Never** invent a `pcs → g` conversion factor.
   - Mark status = **`partial`** with `reason = "INCOMPARABLE_UNIT"` and surface both quantities clearly (`"Need 200 g · Have 4 pcs"`) so the cook can decide.

## 4. Dish Classification & Ranking Algorithm

For each active dish:
- Partition `required` ingredients into `available_count`, `partial_count`, `missing_count`, `substituted_count`.
- **Category Assignment**:
  - **`can_make_now`**: `missing_count == 0` and `partial_count == 0` (all required ingredients are `available` or satisfied by a verified substitution).
  - **`missing_ingredients`**: `missing_count > 0` or `partial_count > 0` (excluded from default list only if `missing_count > max_missing` when `max_missing` filter is set, e.g. `5`, or if `available_count == 0` when pantry is non-empty unless `include_all=true`).
- **Deterministic Ranking Score** (higher = ranked earlier):
  1. Primary tier: `can_make_now` (tier `2`) > `partial_only` (`missing_count == 0, partial_count > 0`, tier `1`) > `missing_ingredients` (tier `0`).
  2. Within tier:
     - Fewer missing required ingredients (`-missing_count`).
     - Higher required availability ratio (`available_count / total_required`).
     - Expiring-soon pantry bonus: `+0.15` per required ingredient with an active lot expiring within 5 days (encourages reducing food waste).
     - Preference & feedback adjustments:
       - Exclude dishes where `dish_feedback.action == "hide"` (unless `include_hidden=true`).
       - Filter/penalize dishes incompatible with `HouseholdRecipePreference.diet_type` or `dietary_restrictions` (`nut_free`, `dairy_free`, etc.).
       - Small boost for `liked` / `saved` dishes and matching `preferred_cuisines` / `max_prep_minutes`.
  3. Tie-breaker: `canonical_name ASC` (deterministic ordering).
