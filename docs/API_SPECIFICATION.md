# REST API v1 Specification — GroceryManagement-AgentBenchmark

**Base Path:** `/api/v1`  
**Content-Type:** `application/json` (except `POST /api/v1/purchases/ocr` which also accepts `multipart/form-data`)  
**Monetary Representation:** All money fields are integer paise (`*_paise: int`, 1 INR = 100 paise).  
**Quantity Representation:** All inventory/recipe quantities use normalized metric base units (`base_unit: "g" | "ml" | "pcs"`, decimal strings or numbers serialized to 3 decimal places).

---

## 1. Cross-Cutting Conventions

### 1.1 Authentication & Household Context
- All endpoints except `GET /health` require a Clerk-issued Bearer token:
  `Authorization: Bearer <Clerk_JWT>`
- Authentication is managed by Clerk (`@clerk/clerk-expo` + `expo-secure-store` `tokenCache`, `<SignedIn>` / `<SignedOut>`, `useSSO()` on mobile). Custom `/auth/register`, `/auth/login`, `/auth/dev-session`, `/auth/dev-bootstrap`, `password_hash`, `is_dev_user`, and custom HS256 JWT issuance are **not** supported.
- FastAPI verifies `RS256` tokens asynchronously via `PyJWT[crypto]` + `httpx` JWKS against `CLERK_JWKS_URL`, validating `CLERK_ISSUER`, `exp`, and `CLERK_AUTHORIZED_PARTIES` (`azp`).
- Verified Clerk JWT claims:
  - `sub`: Clerk user ID
  - `azp`: Authorized party (validated against `CLERK_AUTHORIZED_PARTIES`)
  - `exp`: Unix timestamp
- **Household Resolution (`X-Household-Id`) & RLS**:
  - Clients pass `X-Household-Id: <uuid>` to target an active household. If omitted on bootstrap (`GET /api/v1/auth/me`), the backend resolves the user's primary/default household (or auto-provisions `"My Kitchen"`).
  - Backend upserts the Clerk user via `SECURITY DEFINER` `upsert_clerk_user`, verifies active `household_members` membership, and sets PostgreSQL `app.current_user_id` and `app.current_household_id` for RLS enforcement.

### 1.2 Idempotency Keys
- State-mutating endpoints that create financial or ledger side effects **require** the header:
  `Idempotency-Key: <uuid-or-opaque-string>`
- Required on:
  - `POST /api/v1/purchases/drafts/{draft_id}/confirm`
  - `POST /api/v1/inventory/lots/{lot_id}/adjust`
  - `POST /api/v1/cooking-plans/{plan_id}/complete`
- **Behavior**:
  - Same key + same payload hash within 24h -> returns cached `response_status` and `response_body`.
  - Same key + different payload hash -> returns `422 Unprocessable Entity` (`IDEMPOTENCY_CONFLICT`).
  - Missing header -> returns `400 Bad Request` (`MISSING_IDEMPOTENCY_KEY`).

### 1.3 Uniform Error Envelope
All `4xx` and `5xx` responses return:
```json
{
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "message": "Not enough active stock for Whole Milk (requested 500.000 ml, available 250.000 ml).",
    "details": {
      "canonical_item_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "requested_qty": "500.000",
      "available_qty": "250.000",
      "base_unit": "ml"
    },
    "request_id": "req_01J9XYZ..."
  }
}
```

Standard error codes: `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `VALIDATION_ERROR`, `MISSING_IDEMPOTENCY_KEY`, `IDEMPOTENCY_CONFLICT`, `DRAFT_ALREADY_CONFIRMED`, `INSUFFICIENT_STOCK`, `INVALID_STATE_TRANSITION`, `INTERNAL_ERROR`.

---

## 2. Health & Auth / Household Bootstrap

### `GET /health`
- **Auth:** None
- **Response (`200 OK`):** `{ "status": "ok", "db": "connected" }`

### `GET /api/v1/auth/me`
- **Auth:** Clerk Bearer JWT (`Authorization: Bearer <Clerk_JWT>`)
- **Headers:** Optional `X-Household-Id: <uuid>`
- **Behavior:** Upserts the authenticated Clerk user via `SECURITY DEFINER` `upsert_clerk_user`. If the user belongs to 0 households, auto-provisions a default `"My Kitchen"` household (`currency_code: "INR"`, `role: "owner"`).
- **Response (`200 OK`):**
  ```json
  {
    "user": { "id": "<uuid>", "clerk_user_id": "user_2abc...", "email": "megha@example.com", "display_name": "Megha" },
    "household": { "id": "<uuid>", "name": "My Kitchen", "currency_code": "INR", "role": "owner" },
    "households": [
      { "id": "<uuid>", "name": "My Kitchen", "currency_code": "INR", "role": "owner" }
    ]
  }
  ```

### `GET /api/v1/households`
- **Auth:** Clerk Bearer JWT
- **Response (`200 OK`):**
  ```json
  {
    "items": [
      { "id": "<uuid>", "name": "My Kitchen", "currency_code": "INR", "role": "owner" }
    ]
  }
  ```

### `POST /api/v1/households`
- **Auth:** Clerk Bearer JWT
- **Request:**
  ```json
  {
    "name": "Weekend Villa",
    "currency_code": "INR"
  }
  ```
- **Behavior:** Creates a new household and adds the authenticated user to `household_members` with `role = "owner"`.
- **Response (`201 Created`):**
  ```json
  {
    "id": "<uuid>",
    "name": "Weekend Villa",
    "currency_code": "INR",
    "role": "owner"
  }
  ```

---

## 3. Canonical Catalog

### `GET /api/v1/catalog/items?q=&category=&limit=50&offset=0`
- **Auth:** Bearer JWT
- **Response (`200 OK`):**
  ```json
  {
    "items": [
      {
        "id": "<uuid>",
        "slug": "whole-milk",
        "name": "Whole Milk",
        "category": "dairy",
        "default_base_unit": "ml",
        "default_pack_qty": "500.000",
        "default_pack_unit": "ml",
        "default_shelf_life_days": 5,
        "low_stock_threshold_qty": "500.000",
        "is_custom": false
      }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0
  }
  ```

### `POST /api/v1/catalog/items`
- **Auth:** Bearer JWT
- **Request:**
  ```json
  {
    "name": "Homemade Paneer",
    "category": "dairy",
    "default_base_unit": "g",
    "default_shelf_life_days": 4,
    "low_stock_threshold_qty": "200.000"
  }
  ```
- **Response (`201 Created`):** Created canonical item object.

---

## 4. Purchases & OCR Drafts (Draft -> Review -> Confirm)

> [!IMPORTANT]
> `POST /purchases/ocr`, `POST /purchases/drafts`, and `PATCH /purchases/drafts/{id}` **NEVER** write to `inventory_lots` or `stock_ledger`.

### `POST /api/v1/purchases/ocr`
- **Auth:** Bearer JWT
- **Request (`application/json` or `multipart/form-data`):**
  - Multipart: `file` (image/jpeg, image/png, text/plain)
  - Or JSON: `{ "raw_text": "AMUL TAAZA 500ML x2 54.00\nONION 1KG 40.00", "merchant_name": "BigBasket" }`
- **Response (`201 Created`):**
  ```json
  {
    "id": "<draft_uuid>",
    "status": "draft",
    "source_type": "ocr",
    "ocr_adapter": "fixture",
    "merchant_name": "BigBasket",
    "purchased_on": "2026-11-17",
    "currency_code": "INR",
    "total_amount_paise": 9400,
    "items": [
      {
        "id": "<draft_item_uuid>",
        "line_index": 0,
        "raw_text": "AMUL TAAZA 500ML x2 54.00",
        "canonical_item_id": "<milk_uuid>",
        "display_name": "Whole Milk",
        "match_confidence": "exact_alias",
        "pack_count": "2.000",
        "pack_size_value": "500.000",
        "pack_size_unit": "ml",
        "normalized_qty": "1000.000",
        "base_unit": "ml",
        "needs_unit_review": false,
        "unit_price_paise": 2700,
        "line_total_paise": 5400,
        "expires_on": "2026-11-22"
      }
    ]
  }
  ```

### `POST /api/v1/purchases/drafts`
- **Auth:** Bearer JWT
- **Purpose:** Create a manual purchase draft without OCR.
- **Response (`201 Created`):** Same draft shape.

### `GET /api/v1/purchases/drafts?status=draft&limit=20&offset=0`
- **Auth:** Bearer JWT
- **Response (`200 OK`):** Paginated list of drafts.

### `GET /api/v1/purchases/drafts/{draft_id}`
- **Auth:** Bearer JWT
- **Response (`200 OK`):** Full draft with `items`.

### `PATCH /api/v1/purchases/drafts/{draft_id}`
- **Auth:** Bearer JWT
- **Precondition:** Draft `status == "draft"` (else `409 Conflict` `DRAFT_ALREADY_CONFIRMED`).
- **Request:**
  ```json
  {
    "merchant_name": "BigBasket Daily",
    "purchased_on": "2026-11-17",
    "items": [
      {
        "id": "<draft_item_uuid>",
        "canonical_item_id": "<milk_uuid>",
        "normalized_qty": "1000.000",
        "base_unit": "ml",
        "line_total_paise": 5400,
        "expires_on": "2026-11-23"
      }
    ]
  }
  ```
- **Response (`200 OK`):** Updated draft.

### `POST /api/v1/purchases/drafts/{draft_id}/confirm`
- **Auth:** Bearer JWT
- **Headers:** `Idempotency-Key: <key>` (Required)
- **Behavior:**
  1. Verifies all draft items have non-null `canonical_item_id` and `normalized_qty > 0`.
  2. Atomically creates `purchases`, `purchase_items`, `inventory_lots`, and `stock_ledger` (`PURCHASE_IN`).
  3. Marks `purchase_drafts.status = 'confirmed'`.
  4. Inserts household-scoped `item_aliases` for newly mapped items.
- **Response (`200 OK`):**
  ```json
  {
    "purchase_id": "<purchase_uuid>",
    "draft_id": "<draft_uuid>",
    "total_amount_paise": 9400,
    "created_lots": [
      {
        "lot_id": "<lot_uuid>",
        "canonical_item_id": "<milk_uuid>",
        "remaining_qty": "1000.000",
        "base_unit": "ml",
        "expires_on": "2026-11-22"
      }
    ]
  }
  ```

### `DELETE /api/v1/purchases/drafts/{draft_id}`
- **Auth:** Bearer JWT
- **Response (`204 No Content`):** Marks draft `discarded` (or deletes if unconfirmed).

### `GET /api/v1/purchases?limit=20&offset=0` & `GET /api/v1/purchases/{purchase_id}`
- **Auth:** Bearer JWT
- **Response (`200 OK`):** Confirmed purchase records and line items.

---

## 5. Inventory, Lots & Stock Ledger

### `GET /api/v1/inventory/summary?category=&q=`
- **Auth:** Bearer JWT
- **Response (`200 OK`):**
  ```json
  {
    "items": [
      {
        "canonical_item_id": "<uuid>",
        "name": "Whole Milk",
        "category": "dairy",
        "total_remaining_qty": "1000.000",
        "base_unit": "ml",
        "active_lot_count": 2,
        "earliest_expires_on": "2026-11-20",
        "has_expired_lots": false
      }
    ]
  }
  ```

### `GET /api/v1/inventory/lots?canonical_item_id=&status=active&expiring_within_days=`
- **Auth:** Bearer JWT
- **Response (`200 OK`):** List of `inventory_lots` ordered by FEFO (`expires_on ASC NULLS LAST, received_at ASC`).

### `POST /api/v1/inventory/lots/{lot_id}/adjust`
- **Auth:** Bearer JWT
- **Headers:** `Idempotency-Key: <key>` (Required)
- **Request:**
  ```json
  {
    "event_type": "WASTE_OUT",
    "quantity_delta": "-250.000",
    "notes": "Milk soured before expiry"
  }
  ```
  - Allowed `event_type`: `"ADJUSTMENT_IN"` (`> 0`), `"ADJUSTMENT_OUT"` (`< 0`), `"WASTE_OUT"` (`< 0`).
- **Response (`200 OK`):** Updated lot + created `stock_ledger` entry.

### `GET /api/v1/inventory/ledger?canonical_item_id=&lot_id=&limit=50&offset=0`
- **Auth:** Bearer JWT
- **Response (`200 OK`):** Chronological (newest first) list of immutable `stock_ledger` entries.

---

## 6. Recipes & Inventory-Backed Recommendations

### `GET /api/v1/recipes?q=&limit=20&offset=0`
- **Auth:** Bearer JWT
- **Response (`200 OK`):** List of available recipes.

### `GET /api/v1/recipes/{recipe_id}`
- **Auth:** Bearer JWT
- **Response (`200 OK`):** Recipe details including `ingredients` and `instructions`.

### `GET /api/v1/recipes/recommendations?limit=10`
- **Auth:** Bearer JWT
- **Hard Invariant:** If the household has **zero** active, non-expired `inventory_lots` with `remaining_qty > 0`, returns:
  ```json
  {
    "empty_inventory": true,
    "items": []
  }
  ```
- **When Inventory Non-Empty (`200 OK`):**
  ```json
  {
    "empty_inventory": false,
    "items": [
      {
        "recipe_id": "<uuid>",
        "title": "Masala Omelette",
        "servings": 2,
        "prep_minutes": 10,
        "match_score": 1.0,
        "matched_ingredients_count": 3,
        "total_required_ingredients_count": 3,
        "uses_expiring_soon": true,
        "missing_ingredients": []
      }
    ]
  }
  ```

---

## 7. Cooking Plans (Start Does Not Deduct; Complete Deducts via FEFO)

### `POST /api/v1/cooking-plans`
- **Auth:** Bearer JWT
- **Behavior:** Creates `cooking_plans` (`status = 'planned'`) and `cooking_plan_items`. **Zero writes to `inventory_lots` or `stock_ledger`.**
- **Request:**
  ```json
  {
    "recipe_id": "<recipe_uuid>",
    "planned_servings": 2
  }
  ```
- **Response (`201 Created`):** Cooking plan with scaled `planned_qty` per ingredient.

### `GET /api/v1/cooking-plans?status=&limit=20&offset=0` & `GET /api/v1/cooking-plans/{plan_id}`
- **Auth:** Bearer JWT
- **Response (`200 OK`):** Cooking plan(s) and ingredient items.

### `POST /api/v1/cooking-plans/{plan_id}/complete`
- **Auth:** Bearer JWT
- **Headers:** `Idempotency-Key: <key>` (Required)
- **Behavior:**
  1. Verifies plan is in `planned` or `in_progress` (else `409 Conflict` `INVALID_STATE_TRANSITION`).
  2. For each ingredient with `actual_used_qty > 0`, acquires `pg_advisory_xact_lock` and deducts in FEFO order (`FOR UPDATE`), appending `COOK_OUT` rows to `stock_ledger`.
  3. Transitions plan `status = 'completed'`.
- **Request:**
  ```json
  {
    "items": [
      { "canonical_item_id": "<milk_uuid>", "actual_used_qty": "250.000" }
    ]
  }
  ```
- **Response (`200 OK`):**
  ```json
  {
    "cooking_plan_id": "<plan_uuid>",
    "status": "completed",
    "completed_at": "2026-11-17T10:15:00Z",
    "deductions": [
      {
        "canonical_item_id": "<milk_uuid>",
        "lot_id": "<lot_uuid>",
        "deducted_qty": "250.000",
        "base_unit": "ml",
        "ledger_entry_id": "<ledger_uuid>"
      }
    ]
  }
  ```

### `POST /api/v1/cooking-plans/{plan_id}/cancel`
- **Auth:** Bearer JWT
- **Behavior:** Transitions `planned` / `in_progress` -> `cancelled`. **Zero stock impact.**
- **Response (`200 OK`):** `{ "id": "<plan_uuid>", "status": "cancelled" }`

---

## 8. Insights & Replenishment

### `GET /api/v1/insights/summary?days=30`
- **Auth:** Bearer JWT
- **Response (`200 OK`):**
  ```json
  {
    "period_days": 30,
    "total_spend_paise": 452000,
    "total_waste_paise": 12500,
    "expiring_soon_count": 3,
    "spend_by_category": [
      { "category": "dairy", "total_paise": 120000 }
    ]
  }
  ```

### `GET /api/v1/replenishment/suggestions`
- **Auth:** Bearer JWT
- **Response (`200 OK`):**
  ```json
  {
    "items": [
      {
        "canonical_item_id": "<uuid>",
        "name": "Whole Milk",
        "current_qty": "200.000",
        "low_stock_threshold_qty": "500.000",
        "suggested_buy_qty": "1000.000",
        "base_unit": "ml",
        "reason": "below_threshold"
      }
    ]
  }
  ```
