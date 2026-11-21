# Food Classification Dataset Research & LLM-Powered AI Chef Architecture

## 1. Executive Summary
This document presents the dataset research, licensing analysis, canonical food catalogue design, layered classification engine, LLM provider evaluation, and conversational AI Chef architecture for `GroceryManagement-AgentBenchmark`.

**Selected Dataset Combination:**
1. **USDA FoodData Central (Foundation + SR Legacy)** (`CC0 1.0`) — Permissive public-domain backbone for raw/dried/fresh/frozen ingredients, staples, dairy, eggs, meat/seafood, spices, nuts, and oils.
2. **FoodOn Ontology** (`CC BY 4.0`) — Permissive hierarchical food ontology (`>9,600` food product classes) for food-state (`raw`, `dried`, `fresh`, `frozen`, `processed`, `cooked`, `ready-to-eat`) and parent-child category taxonomy.
3. **INDB (Indian Nutrient Databank) & Curated Indian Grocery/Dish Synonym Layer** (`CC BY 4.0` for INDB + custom permissive internal synonym/transliteration seed) — Covers Indian regional ingredients, transliterations (`toor dal`/`arhar dal`, `bhindi`/`okra`, `lauki`/`bottle gourd`, `kala chana`, `doodh`), and `~1,000+` cooked Indian dishes (`phulka`, `kadai paneer`, `chole masala`, `biryani`, `dosa`) to separate **raw pantry ingredients** from **cooked/restaurant dishes**.
4. **Open Food Facts (`in.openfoodfacts.org`)** (`ODbL` / `CC BY-SA`) — Used **only via optional runtime API lookup/caching** (never embedded as a derivative static dump in the proprietary bundle) for packaged/barcoded Indian groceries when local catalogue matching is uncertain.

---

## 2. Dataset Comparison Matrix

| Dataset | Source | Coverage | Indian Coverage | Aliases | Food State | Structure | Size | Format | Licence | Maintenance | Integration | Limitations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **IFCT 2017** | ICMR–NIN (India); community JSON `@ifct2017` | Indian raw staples, vegetables, fruits, pulses, spices, fish/meat | High (scientific + English + Indian regional names) | Moderate (Hindi/regional transliterations in tables) | Primarily raw edible portions | Flat tabular | ~528–542 foods | PDF (official), JSON/CSV (GitHub) | Official gov data (attribution); `ifct2017.github.io` wrappers are **AGPL-3.0** | Static 2017 edition | Reference only / avoid AGPL-3.0 wrapper code | No packaged groceries, no cooked restaurant dishes, AGPL wrapper risk |
| **INDB (Indian Nutrient Databank)** | `lindsayjaacks/Indian-Nutrient-Databank-INDB-` | Indian raw foods + ~1,000+ composite Indian cooked dishes | Very high (Indian regional recipes & staples) | Moderate | Raw ingredients + cooked composite dishes | Flat + recipe mapping | ~1,000+ recipes + ingredient tables | XLSX / CSV / R / STATA | **CC BY 4.0** (scripts/outputs); requires NIN source acquisition to rebuild | Academic / periodic | Extract dish & ingredient taxonomy + state distinction | Nutrient-focused; sparse packaged/brand SKUs |
| **FoodOn Ontology** | OBO Foundry (`foodon.org`) | Global farm-to-fork foods, ingredients, processing states | Low–Moderate (major Indian staples) | Good (LanguaL/synonyms) | Explicit (`raw`, `cooked`, `dried`, `frozen`, `canned`) | Deep polyhierarchy | >40,000 classes (>9,600 food products) | OWL / RDF-XML | **CC BY 4.0** | Active (OBO Foundry) | Offline taxonomy & food-state extraction into canonical seed | Heavy OWL format; weak on Indian regional transliterations |
| **Open Food Facts (OFF)** | Open Food Facts (`in.openfoodfacts.org`) | Packaged groceries, snacks, beverages, dairy, RTE | Growing Indian packaged SKU subspace (`en:india`) | Brand + product + multilingual names | Packaged / processed / RTE | Hierarchical category tags | >3,000,000 global (10–43 GB dump) | REST API v2/v3, JSONL, CSV | **ODbL** (share-alike) + **CC BY-SA** images | Very active | Optional runtime API lookup + TTL cache (no static bundling) | ODbL share-alike obligations if bundled; noisy crowdsourced entries |
| **USDA FoodData Central** | USDA ARS (`fdc.nal.usda.gov`) | Foundation, SR Legacy, Survey (FNDDS), Branded | Low (generic staples only) | Moderate (common US/English names) | Raw, cooked, processed, branded | Relational / JSON | ~400k+ total (~8k SR Legacy + Foundation) | JSON / CSV / REST API | **CC0 1.0** (Public Domain) | Active | Embed curated Foundation + SR Legacy subset offline | US-centric; lacks Indian dals, regional produce, and Indian dishes |
| **FAO/EFSA FoodEx2 & INFOODS** | FAO / EFSA / WHO GIFT | Global dietary harmonization, broad commodity groups | Low–Moderate | Facet descriptors (processing/state) | Explicit facets (raw, milled, cooked) | 7-level alphanumeric hierarchy | >30,000 terms | XLSX / XML / CSV | Public-sector open access / **CC BY 4.0** | Active | Taxonomy inspiration & facet alignment | Designed for dietary surveys, not retail receipt SKU matching |
| **FooDB** | TMIC / Wishart Lab (`foodb.ca`) | Food chemical compounds, macro/micronutrients, raw foods | Low | Scientific & common names | Mostly raw | Relational | ~700+ foods, >70k compounds | CSV / JSON / XML / MySQL | **CC BY-NC 4.0** (Non-Commercial) | Periodic (2020 snapshot) | **Excluded** from commercial app due to NC licence | Non-commercial licence; compound chemistry focus |
| **Indian Food Recipes / Indian Food 101** | Kaggle (scraped from Archana's Kitchen / curated) | Indian cooked dishes & ingredient lists | Very high (dish names & regional cuisines) | Dish-level | Cooked dishes | Flat CSV | ~255 (101) to ~6,000+ (Recipes) | CSV | Mixed / custom / unknown (web-scraped) | Static | Use **only** non-copyrightable dish/ingredient naming patterns | Scraping provenance; cannot redistribute full recipe text commercially |
| **BigBasket Products Dataset** | Kaggle (`CC0`) | Indian online grocery SKUs, pack sizes, categories | Very high (Indian retail SKUs) | Brand + variant + pack | Packaged groceries & fresh produce | Flat CSV | ~27,000 rows | CSV | **CC0 1.0** (Kaggle card) | Static snapshot | Reference for Indian grocery receipt phrasing & pack normalization | Static snapshot; brand/price drift; verify no scraping TOS conflict |
| **Hugging Face Indian Food Datasets** | Hugging Face (`indian-foods`) | Image classification of Indian dishes | High (popular dishes) | Minimal | Cooked dishes | Image + label | ~20–100 classes | Parquet / Images | CC0 / MIT | Static | Not suitable for text receipt classification | Image-only, tiny class count |

---

## 3. Licence & Redistribution Analysis
- **Avoided in Static Bundle:**
  - `ifct2017.github.io` wrapper code (`AGPL-3.0`): copyleft viral obligations.
  - `FooDB` (`CC BY-NC 4.0`): prohibits commercial use.
  - Web-scraped recipe texts: copyright and TOS uncertainty; never copy full third-party instructions.
  - `Open Food Facts` bulk dump (`ODbL`): share-alike requirement on derivative databases. Instead, OFF can be queried via its public REST API at runtime with attribution and response caching without creating a redistributed ODbL derivative database.
- **Safe for Offline Embedding & Commercial Use:**
  - `USDA FoodData Central` (`CC0 1.0`), `FoodOn` (`CC BY 4.0` with attribution), `INDB` (`CC BY 4.0` with attribution), and our own curated canonical food catalogue & Indian synonym/transliteration mapping.

---

## 4. Canonical Food Catalogue Schema
Our canonical schema extends existing `CanonicalItem` and `ItemAlias` tables in `backend/app/modules/inventory/models.py` (preserving household isolation and existing FKs) with:
- `id` (UUID), `household_id` (nullable: `NULL` = global verified catalogue, UUID = household-scoped)
- `slug`, `name` (canonical English/market name), `category` (`vegetable`, `fruit`, `pulse`, `grain`, `dairy`, `spice`, `oil`, `nut`, `packaged_grocery`, `snack`, `cooked_meal`, `beverage`, `meat_seafood`, `bakery`, `other`)
- `secondary_category` (e.g., `legume_split`, `leafy_green`, `prepared_curry`)
- `food_state` (`raw`, `dried`, `fresh`, `frozen`, `processed`, `cooked`, `ready_to_eat`, `unknown`)
- `is_prepared_food` (boolean — distinguishes raw ingredient vs cooked/restaurant dish independently of `category`)
- `pantry_eligible` (boolean — whether confirming this item creates `InventoryLot`)
- `budget_treatment` (`pantry_inventory`, `outside_food_expenditure`, `budget_only_grocery`, `requires_user_review`)
- `default_base_unit` (`g`, `ml`, `pcs`), `default_shelf_life_days`
- `language` (`en`, `hi`, `hi-Latn`), `source_dataset`, `source_record_id`, `verified` (boolean)
- `ItemAlias` records storing `normalized_alias`, `alias_kind` (`synonym`, `regional`, `transliteration`, `user_correction`, `brand_variant`), `source`, and `household_id` (user corrections are scoped to the household so they never corrupt global verified records!).

---

## 5. Layered Food Identification & Classification Engine (Part B)
Pipeline after OCR & product description reconstruction:
1. **FoodNameNormalizer**: Strips pack sizes (`500g`, `1 L`, `250 ml`), return-policy noise, packaging words (`pouch`, `carton`, `pack`, `jar`, `bottle`), punctuation, and normalizes whitespace/casing while preserving the original `raw_text` and `display_name`.
2. **Layered Matcher (`FoodClassificationService`)**:
   - **Layer 1**: Exact canonical-name match (`CanonicalItem.name` / `slug`, household-first then global).
   - **Layer 2**: Exact alias match (`ItemAlias.normalized_alias`, household-first then global).
   - **Layer 3**: Normalized token/stem match (handles word reordering like `"dal toor"` ↔ `"toor dal"` or `"panner"` typo).
   - **Layer 4**: Fuzzy similarity matching (`difflib.SequenceMatcher` / token Jaccard >= 0.82).
   - **Layer 5**: Context-aware food-state & accounting classification (combines matched `CanonicalItem.food_state` + `receipt_category` + pack-size evidence so e.g. `Paneer 200g` at a grocery store → `pantry_inventory`, while `Patiala Kadai Paneer` at a restaurant → `outside_food_expenditure`).
   - **Layer 6**: Confidence assessment — if confidence < `0.70` or food state is ambiguous, mark `needs_review = True` / `requires_user_review` so the user can review/correct on mobile, and persist approved corrections as household-scoped `ItemAlias` entries!

---

## 6. LLM Provider Comparison & AI Chef Architecture (Part C)

| Provider / Model | NLU & Recipe Quality | Structured JSON & Tool Calling | Context Window | Latency | Free Tier & Pricing (approx.) | Commercial Use & Privacy |
|---|---|---|---|---|---|---|
| **Google Gemini (`gemini-2.5-flash`)** | Very high; strong Indian & global culinary knowledge | Native `response_schema` JSON + function calling | 1M tokens | Low (~0.8–2s) | Free tier in AI Studio (rate-limited; data may be used on free tier); Paid ~$0.30/1M in, ~$2.50/1M out | Commercial allowed on paid tier |
| **OpenAI (`gpt-4o-mini`)** | High; reliable instruction following | Strict `json_schema` + tool calling | 128K tokens | Low (~0.8–1.8s) | No permanent free API tier; ~$0.15/1M in, ~$0.60/1M out | Commercial allowed; no training on API data by default |
| **Anthropic (`claude-3.5-haiku` / `sonnet`)** | Very high; nuanced substitutions & safety reasoning | Native tool use / JSON | 200K tokens | Low–Med | Paid API credits required | Commercial allowed; zero retention options |
| **Hugging Face Router (`Qwen/Qwen2.5-7B-Instruct` / `Llama-3.3-70B`)** | Good–High | JSON mode via OpenAI-compatible `/v1/chat/completions` | 32K–128K tokens | Variable (1–5s) | $0.10/mo free trial credit; pass-through provider pricing | Depends on model licence (Apache-2.0 for Qwen2.5) |
| **Deterministic Inventory-Grounded Fallback** | Template + constraint + expiring-item scoring | Guaranteed valid Pydantic schema | N/A | <5 ms | $0, works offline with zero API keys | 100% private & local |

**Selected Architecture:**
- **Provider Abstraction (`LLMProviderAdapter`)** in `backend/app/infra/llm/base.py` supporting `huggingface` (`Qwen/Qwen2.5-7B-Instruct`), `gemini` (`gemini-2.5-flash`), `openai` (`gpt-4o-mini`), and automatic **deterministic inventory-grounded fallback** when no API key is configured or on provider timeout/error/invalid JSON.
- **Configured via server-side env vars**: `LLM_PROVIDER` (`auto` | `huggingface` | `gemini` | `openai`), `HF_TOKEN`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `LLM_MODEL_ID`, `LLM_TIMEOUT_SECONDS`. Zero secrets ever reach mobile clients.
- **Multi-turn Conversational AI Chef (`AIChefService`)**:
  - Persists/accepts `conversation_history` (multi-turn context across user & assistant turns) + `RecipeContextBuilder` (loads active `InventoryLot` quantities, base units, expiring items in ≤3 days, and recent `CookingPlan` / prepared meal history).
  - Enforces **strict grounding**: never fabricates pantry stock, separates `available_ingredients`, `partial_ingredients`, `missing_ingredients`, `optional_ingredients`, and `substitutions`, includes `meal_type`, `prep_minutes`, `cook_minutes`, `cooking_tips`, and `food_safety_notes`, and asks targeted follow-up questions when inventory or preferences are ambiguous.
  - **Inventory-Aware Cooking Workflow**: Generating/viewing/planning/starting a recipe **never** deducts stock. Only explicit `POST /api/v1/cooking-plans/{id}/complete` with user-confirmed actual quantities deducts lots via **FEFO** (`allocate_fefo`) and updates `StockLedger`, `CookingPlan`, and home-cooked meal history (never mixed into outside-food restaurant expenditure).
