# Pantry-First Dish & Video Discovery — Product Brief

## 1. Executive Summary

GroceryManagement currently generates recipe recommendations internally via `backend/app/modules/recipes/` (`catalogue_data.py`, `catalogue_steps.py`, `context_engine.py`, and `HuggingFaceRecipeProvider`). While useful as a structured fallback, real home cooks—especially for Indian regional cuisine—prefer following authentic cooking videos from trusted Indian creators rather than reading synthetic or templated instructions.

This product direction shifts the primary discovery experience from **internally generated recipes** to **pantry-first dish & cooking-video discovery**:
- Match curated Indian dishes deterministically against the household's real pantry (`CanonicalIngredient` + `InventoryLot` via `get_ingredient_inventory_summary`).
- Group discovery into two primary actionable categories: **Can Make Now** and **Missing Ingredients**.
- Surface real Indian cooking videos via **YouTube Data API v3** with quota-aware backend caching, embeddable playback, and original YouTube link fallback.
- Preserve existing cooking-session tracking (`CookingPlan`, `MealHistory`, `StockLedger`) so stock is **only** deducted after explicit user confirmation.

## 2. Problem Statement

1. **Trust & Authenticity Gap**: Users rarely trust LLM-generated or templated cooking steps for nuanced Indian dishes (tadka timing, bhuna stages, regional spice ratios).
2. **Pantry Disconnect on Video Platforms**: Searching YouTube directly ignores what is already in the user's kitchen, leading to wasted produce and unvetted ingredient assumptions.
3. **Variant & Unit Safety**: Generic matching risks conflating distinct variants (e.g., `Full Cream Milk` vs. `Toned Milk`, or `Chana Dal` vs. `Chana Kala`) or making unsafe cross-dimensional conversions (`g` vs. `pcs`).

## 3. Target Users & Core Jobs-to-be-Done

- **Household Cook**: *"Show me Indian dishes I can cook right now with what's in my pantry, or with 1–2 missing items, and let me watch a real video while checking off my pantry stock."*
- **Kitchen Manager**: *"Keep pantry stock accurate—never deduct anything just because I browsed or watched a video, and let me confirm exact quantities used after cooking."*

## 4. Product Scope & Non-Goals

### In Scope
- Curated **Dish Catalogue** (initial target: 50–100 popular Indian dishes across North, South, East, and West Indian cuisines), strictly separated from video metadata.
- Deterministic **Pantry Matching & Ranking Engine** reusing `get_ingredient_inventory_summary` and `IngredientResolver` rules.
- **YouTube Data API v3** integration behind backend caching and quota protection (100 `search.list` calls/day default quota).
- **Discover UI** in Expo (`Can Make Now` / `Missing Ingredients` filters, dish detail, pantry checklist, video recommendations & playback, feedback, and explicit stock deduction).

### Non-Goals (Out of Scope)
- Scraping YouTube HTML, downloading, or rehosting video streams.
- Deleting or breaking existing `backend/app/modules/recipes/` or `backend/app/modules/cooking/` functionality.
- Automatic stock deduction on dish view, video play, or bookmarking.
- Inventing ingredient variants or performing unverified cross-dimensional unit conversions.
