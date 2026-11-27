export interface ApiErrorDetail {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
}

export interface ApiErrorEnvelope {
  error: ApiErrorDetail;
}

export interface AuthUser {
  id: string;
  clerk_user_id: string;
  email: string | null;
  display_name: string | null;
}

export interface Household {
  id: string;
  name: string;
  currency_code: string;
  role: string;
}

export interface AuthMeResponse {
  user: AuthUser;
  active_household: Household | null;
  households: Household[];
}

export type BaseUnit = 'g' | 'ml' | 'pcs';

export interface CatalogItem {
  id: string;
  slug: string;
  name: string;
  category: string;
  default_base_unit: BaseUnit;
  default_pack_qty?: string | null;
  default_pack_unit?: string | null;
  default_shelf_life_days?: number | null;
  low_stock_threshold_qty?: string | null;
  is_custom?: boolean;
}

export interface CatalogItemsResponse {
  items: CatalogItem[];
  total: number;
  limit: number;
  offset: number;
}

export type PurchaseDraftStatus = 'draft' | 'confirmed' | 'discarded';
export type PurchaseSourceType = 'ocr' | 'manual';

export interface PurchaseDraftItem {
  id: string;
  line_index: number;
  raw_text?: string | null;
  canonical_item_id: string | null;
  display_name: string;
  secondary_category?: string | null;
  eligibility_reason?: string | null;
  match_confidence?: string | null;
  pack_count?: string | null;
  pack_size_value?: string | null;
  pack_size_unit?: string | null;
  normalized_qty: string;
  base_unit: BaseUnit;
  needs_unit_review?: boolean;
  unit_price_paise?: number | null;
  line_total_paise: number;
  discount_paise?: number;
  tax_paise?: number;
  purchase_qty?: number | null;
  purchase_unit?: string | null;
  tax_rate_bps?: number | null;
  confidence?: number | null;
  warnings?: string[];
  track_inventory?: boolean;
  classification_status?: 'pantry_eligible' | 'prepared_meal' | 'non_food' | 'ambiguous_review' | string;
  food_state?: string | null;
  budget_treatment?: string | null;
  review_reason?: string | null;
  expires_on?: string | null;
  standardized_name?: string | null;
  ingredient_family_id?: string | null;
  ingredient_family_name?: string | null;
  ingredient_variant_id?: string | null;
  canonical_ingredient_id?: string | null;
  ingredient_category?: string | null;
  is_unspecified_variant?: boolean;
  ingredient_confidence_tier?: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNMATCHED' | 'NON_PANTRY' | string | null;
  ingredient_confidence_score?: number | null;
  ingredient_match_source?: string | null;
  ingredient_review_reason?: string | null;
  user_approved_ingredient?: boolean;
  ingredient_suggestions?: Array<{
    variant_id: string;
    variant_name: string;
    family_id: string;
    family_name: string;
    canonical_ingredient_id: string | null;
    score: number;
  }>;
  quantity_confidence_tier?: 'HIGH' | 'MEDIUM' | 'LOW' | string | null;
  quantity_review_reason?: string | null;
  financial_mismatch?: boolean;
  financial_review_reason?: string | null;
  supported_units?: string[];
}

export interface PurchaseAttachment {
  filename?: string | null;
  content_type?: string | null;
  document_type?: string | null;
}

export interface PurchaseDraft {
  id: string;
  status: PurchaseDraftStatus;
  source_type: PurchaseSourceType;
  document_type?: string;
  ocr_adapter?: string | null;
  merchant_name: string | null;
  order_id?: string | null;
  purchased_on: string | null;
  purchased_time?: string | null;
  currency_code: string;
  receipt_category?: string;
  payment_method?: string | null;
  total_amount_paise: number;
  math_mismatch?: boolean;
  items: PurchaseDraftItem[];
  created_at?: string;
  ocr_warnings?: string[];
  missing_fields?: string[];
  attachments?: PurchaseAttachment[];
  math_reconciled?: boolean | null;
  ocr_confidence?: number | null;
  ocr_orchestration?: {
    extraction_job_id: string;
    extraction_status:
      | 'EXPERIMENTAL_SUCCESS'
      | 'EXPERIMENTAL_PARTIAL'
      | 'FALLBACK_SUCCESS'
      | 'ALL_PROVIDERS_FAILED';
    primary_provider: string;
    primary_model: string;
    primary_status: string;
    fallback_triggered: boolean;
    fallback_reason?: string | null;
    fallback_provider?: string | null;
    selected_provider: string;
    total_latency_ms: number;
  } | null;
  food_consumption?: FoodConsumption | null;
  receipt_number?: string | null;
  subtotal_paise?: number | null;
  tax_paise?: number | null;
  cgst_paise?: number;
  sgst_paise?: number;
  igst_paise?: number;
  other_tax_paise?: number;
  additional_charges_paise?: number;
  discount_paise?: number | null;
  rounding_paise?: number | null;
}

export interface PurchaseDraftListResponse {
  items: PurchaseDraft[];
  total?: number;
}

export interface PurchaseDraftItemUpdateInput {
  id?: string;
  raw_text?: string;
  canonical_item_id: string | null;
  display_name?: string;
  qty?: number;
  unit?: string;
  normalized_qty: string;
  base_unit: BaseUnit;
  unit_price_paise?: number;
  line_total_paise: number;
  discount_paise?: number;
  tax_paise?: number;
  purchase_qty?: number | null;
  purchase_unit?: string | null;
  track_inventory?: boolean;
  classification_status?: 'pantry_eligible' | 'prepared_meal' | 'non_food' | 'ambiguous_review' | string;
  food_state?: string | null;
  budget_treatment?: string | null;
  review_reason?: string | null;
  expires_on?: string | null;
  standardized_name?: string | null;
  ingredient_family_id?: string | null;
  ingredient_family_name?: string | null;
  ingredient_variant_id?: string | null;
  canonical_ingredient_id?: string | null;
  ingredient_category?: string | null;
  is_unspecified_variant?: boolean;
  ingredient_confidence_tier?: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNMATCHED' | 'NON_PANTRY' | string | null;
  ingredient_confidence_score?: number | null;
  ingredient_match_source?: string | null;
  ingredient_review_reason?: string | null;
  user_approved_ingredient?: boolean;
  ingredient_suggestions?: Array<{
    variant_id: string;
    variant_name: string;
    family_id: string;
    family_name: string;
    canonical_ingredient_id: string | null;
    score: number;
  }>;
  quantity_confidence_tier?: 'HIGH' | 'MEDIUM' | 'LOW' | string | null;
  quantity_review_reason?: string | null;
  financial_mismatch?: boolean;
  financial_review_reason?: string | null;
  supported_units?: string[];
}

export interface PurchaseDraftUpdateRequest {
  merchant_name?: string | null;
  receipt_number?: string | null;
  purchased_on?: string | null;
  purchased_time?: string | null;
  payment_method?: string | null;
  subtotal_paise?: number | null;
  discount_paise?: number;
  additional_charges_paise?: number;
  cgst_paise?: number;
  sgst_paise?: number;
  igst_paise?: number;
  other_tax_paise?: number;
  tax_paise?: number;
  purchase_qty?: number | null;
  purchase_unit?: string | null;
  rounding_paise?: number;
  total_amount_paise?: number;
  items: PurchaseDraftItemUpdateInput[];
}

export interface PurchaseDraftCreateRequest {
  merchant_name: string;
  receipt_number?: string | null;
  purchased_on: string;
  subtotal_paise?: number | null;
  discount_paise?: number;
  additional_charges_paise?: number;
  cgst_paise?: number;
  sgst_paise?: number;
  igst_paise?: number;
  tax_paise?: number;
  purchase_qty?: number | null;
  purchase_unit?: string | null;
  rounding_paise?: number;
  total_amount_paise?: number;
  items: PurchaseDraftItemUpdateInput[];
}

export interface CreatedLotSummary {
  lot_id: string;
  canonical_item_id: string;
  remaining_qty: string;
  base_unit: BaseUnit;
  expires_on: string | null;
}

export type MealType = 'breakfast' | 'lunch' | 'dinner' | 'snacks' | 'multiple' | 'other';
export type DiningMode = 'restaurant' | 'takeaway' | 'delivery' | 'other';

export interface FoodConsumption {
  id: string;
  purchase_id: string;
  consumed_on: string;
  meal_type: MealType | string;
  dining_mode: DiningMode | string;
  merchant_name: string | null;
  amount_paise: number;
  currency_code: string;
  items_summary?: Array<{ name: string; qty: number; unit: string; line_total_paise: number }> | null;
  people_count?: number | null;
  notes?: string | null;
}

export interface PurchaseConfirmResponse {
  purchase_id: string;
  draft_id: string;
  total_amount_paise: number;
  created_lots: CreatedLotSummary[];
  food_consumption?: FoodConsumption | null;
}

export type PurchaseRecordItem = PurchaseDraftItem;

export type PurchaseRecord = PurchaseDraft;

export interface PurchasesListResponse {
  items: PurchaseRecord[];
  total?: number;
}

export interface InventorySummaryItem {
  canonical_item_id: string;
  name: string;
  category: string;
  secondary_category?: string | null;
  total_remaining_qty: string;
  base_unit: BaseUnit;
  active_lot_count: number;
  earliest_expires_on: string | null;
  has_expired_lots: boolean;
}

export interface InventorySummaryResponse {
  items: InventorySummaryItem[];
}

export interface IngredientUnitGroup {
  unit: string;
  total_qty: string;
  lot_count: number;
}

export interface IngredientLotDetail {
  lot_id: string;
  canonical_item_id: string;
  purchase_item_id: string | null;
  initial_qty: string;
  remaining_qty: string;
  base_unit: string;
  converted_qty: string | null;
  converted_unit: string | null;
  unit_cost_paise: number;
  purchased_at: string;
  expires_on: string | null;
  lot_status: string;
}

export interface IngredientProductBreakdown {
  canonical_item_id: string;
  name: string;
  category: string;
  base_unit: string;
  total_remaining_qty: string;
  active_lot_count: number;
  has_expired_lots: boolean;
  earliest_expires_on: string | null;
  mapping_source: string;
  matching_confidence: string;
  lots: IngredientLotDetail[];
}

export interface IngredientSummaryItem {
  canonical_ingredient_id: string;
  name: string;
  normalized_name: string;
  category: string;
  subcategory: string | null;
  standard_unit: string;
  total_remaining_qty: string | null;
  unit_completeness_status: 'complete' | 'incomplete' | 'empty';
  unit_groups: IngredientUnitGroup[];
  contributing_product_count: number;
  lot_count: number;
  has_expired_lots: boolean;
  earliest_expires_on: string | null;
  calculation_note: string;
  products: IngredientProductBreakdown[];
}

export interface UnmappedPantryProduct {
  canonical_item_id: string;
  name: string;
  category: string;
  base_unit: string;
  total_remaining_qty: string;
  active_lot_count: number;
  suggestion_status: 'unmapped' | 'unconfirmed_suggestion';
  suggested_ingredient_id: string | null;
  suggested_ingredient_name: string | null;
  suggestion_reason?: string | null;
}

export interface IngredientInventorySummaryResponse {
  ingredients: IngredientSummaryItem[];
  unmapped_products_count: number;
  unmapped_lots_count: number;
  unconfirmed_suggestions_count: number;
  mapping_review_count?: number;
  mapping_review_items?: UnmappedPantryProduct[];
  unmapped_products: UnmappedPantryProduct[];
}

export interface CanonicalIngredientOption {
  id: string;
  name: string;
  normalized_name: string;
  category: string;
  standard_unit: string;
}

export interface CanonicalIngredientsListResponse {
  items: CanonicalIngredientOption[];
}

export type InventoryLotStatus = 'active' | 'depleted' | 'expired' | 'discarded';

export interface InventoryLot {
  id: string;
  canonical_item_id: string;
  item_name?: string;
  category?: string;
  remaining_qty: string;
  initial_qty?: string;
  base_unit: BaseUnit;
  unit_cost_paise?: number | null;
  purchased_on?: string | null;
  expires_on: string | null;
  received_at?: string;
  status: InventoryLotStatus;
}

export interface InventoryLotsResponse {
  items: InventoryLot[];
}

export type AdjustEventType = 'ADJUSTMENT_IN' | 'ADJUSTMENT_OUT' | 'WASTE_OUT';

export interface AdjustLotRequest {
  event_type: AdjustEventType;
  quantity_delta: string;
  notes?: string;
}

export interface StockLedgerEntry {
  id: string;
  lot_id: string;
  canonical_item_id: string;
  event_type: string;
  quantity_delta: string;
  base_unit: BaseUnit;
  notes?: string | null;
  created_at: string;
}

export interface StockLedgerResponse {
  items: StockLedgerEntry[];
}

export interface RecipeIngredient {
  canonical_item_id: string | null;
  name: string;
  required_qty: string;
  base_unit: string;
  is_optional?: boolean;
  essential?: boolean;
  status?: string;
  available_qty?: string | null;
  missing_qty?: string | null;
  unit_compatible?: boolean;
  is_available?: boolean;
}

export interface RecipeReference {
  title: string;
  url: string;
  source?: string | null;
  link_type?: 'written_recipe' | 'video' | string;
  verified: boolean;
}

export interface RecipeStep {
  step_number: number;
  title: string;
  instruction: string;
  ingredients_used?: string[];
  ingredient_quantities?: Record<string, string>;
  duration_minutes?: number | null;
  heat_level?: string | null;
  technique?: string | null;
  equipment?: string[];
  completion_cue?: string;
  caution?: string | null;
  next_action?: string | null;
}

export interface RecipeRecommendation {
  recipe_id: string;
  title: string;
  description?: string | null;
  meal_type?: string | null;
  servings: number;
  prep_minutes: number;
  cook_minutes?: number | null;
  difficulty?: string | null;
  source_type?: 'ai_generated' | 'external_reference' | string;
  source_label?: string | null;
  recommendation_type?: 'COOK_NOW' | 'COOK_WITH_SUBSTITUTIONS' | 'REQUIRES_SHOPPING' | string;
  why_recommended?: string | null;
  match_score: number;
  matched_ingredients_count: number;
  total_required_ingredients_count: number;
  uses_expiring_soon: boolean;
  missing_ingredients: string[];
  insufficient_ingredients?: string[];
  ingredients?: RecipeIngredient[];
  instructions?: string[];
  preparation_steps?: string[];
  structured_steps?: RecipeStep[];
  equipment_required?: string[];
  quality_warnings?: string[];
  substitutions?: string[];
  cooking_tips?: string[];
  troubleshooting_tips?: string[];
  serving_result?: string | null;
  food_safety_notes?: string[];
  references?: RecipeReference[];
}

export interface RecipeRecommendationsResponse {
  empty_inventory: boolean;
  items: RecipeRecommendation[];
  is_refined?: boolean;
  refinement_pending?: boolean;
  greeting?: string | null;
  meal_window?: string | null;
  meal_window_label?: string | null;
  season?: string | null;
  weather_summary?: string | null;
}

export type RecommendationMode = 'enjoyed' | 'new' | 'balanced';

export interface HouseholdRecipePreferences {
  household_id?: string;
  timezone: string;
  location_name?: string | null;
  diet_type: 'any' | 'vegetarian' | 'vegan' | 'eggetarian' | 'non_vegetarian';
  dietary_restrictions: string[];
  preferred_cuisines: string[];
  preferred_meal_types: string[];
  max_prep_minutes?: number | null;
  recommendation_mode?: RecommendationMode;
  distinct_completed_recipes?: number;
  should_show_personalisation_prompt?: boolean;
  personalisation_prompt_dismissed?: boolean;
  onboarding_completed?: boolean;
  preferences_completed_at?: string | null;
}

export interface RecipeChatExternalLink {
  title: string;
  url: string;
  source?: string | null;
  link_type?: 'written_recipe' | 'video' | string;
  verified: boolean;
}

export interface RecipeChatIngredient {
  name: string;
  canonical_item_id?: string | null;
  quantity: string;
  base_unit: string;
  available: boolean;
  available_qty?: string | null;
}

export interface RecipeChatSuggestion {
  id?: string;
  recipe_id?: string | null;
  title: string;
  meal_type?: string | null;
  servings: number;
  prep_minutes?: number | null;
  cook_minutes?: number | null;
  difficulty?: string | null;
  source_type?: 'ai_generated' | 'external_reference' | string;
  source_label?: string | null;
  recommendation_type?: 'COOK_NOW' | 'COOK_WITH_SUBSTITUTIONS' | 'REQUIRES_SHOPPING' | string;
  why_recommended?: string | null;
  summary?: string | null;
  available_ingredients: RecipeChatIngredient[];
  partial_ingredients?: RecipeChatIngredient[];
  missing_ingredients: RecipeChatIngredient[];
  optional_ingredients?: RecipeChatIngredient[];
  substitutions?: string[];
  cooking_tips?: string[];
  troubleshooting_tips?: string[];
  serving_result?: string | null;
  food_safety_notes?: string[];
  uses_expiring_items?: string[];
  equipment_required?: string[];
  preparation_steps?: string[];
  instructions?: string[];
  structured_steps?: RecipeStep[];
  quality_warnings?: string[];
  external_links?: RecipeChatExternalLink[];
}

export type AiProviderStatus =
  | 'live'
  | 'missing_token'
  | 'unconfigured'
  | 'fallback'
  | 'error'
  | string;

export interface RecipeChatTurnInput {
  role: 'user' | 'assistant';
  content: string;
}

export interface RecipeChatRequest {
  message: string;
  servings?: number;
  dietary_constraints?: string[];
  max_prep_minutes?: number;
  meal_type?: string | null;
  hunger_level?: string | null;
  preference_tags?: string[];
  equipment?: string[];
  recommendation_mode?: RecommendationMode;
  conversation_history?: RecipeChatTurnInput[];
}

export interface RecipeChatResponse {
  reply: string;
  provider_status: AiProviderStatus;
  provider_message?: string | null;
  model?: string | null;
  empty_inventory?: boolean;
  follow_up_questions?: string[];
  suggestions: RecipeChatSuggestion[];
  external_links?: RecipeChatExternalLink[];
}

export type CookingPlanStatus = 'planned' | 'in_progress' | 'completed' | 'cancelled';

export interface CookingPlanItem {
  id?: string;
  line_id?: string;
  canonical_item_id: string;
  name?: string;
  planned_qty: string;
  base_unit: BaseUnit;
  available_qty?: string;
}

export interface CookingPlan {
  id: string;
  recipe_id?: string | null;
  title?: string;
  planned_servings: number;
  status: CookingPlanStatus;
  items: CookingPlanItem[];
  created_at?: string;
  completed_at?: string | null;
}

export interface CookingPlansListResponse {
  items: CookingPlan[];
}

export interface CreateCookingPlanRequest {
  recipe_id?: string | null;
  title?: string;
  planned_servings: number;
  meal_type?: string | null;
  prep_minutes?: number | null;
  cook_minutes?: number | null;
  description?: string | null;
  steps?: string[];
  substitutions?: string[];
  items?: Array<{
    canonical_item_id: string;
    name?: string;
    planned_qty: string;
    base_unit: BaseUnit;
  }>;
}

export interface CompleteCookingPlanItemInput {
  line_id?: string;
  canonical_item_id: string;
  actual_used_qty: string;
  waste_qty?: string;
  omitted?: boolean;
  session_confirmed?: boolean;
  confirmed_qty?: string;
  confirmed_unit?: string;
}

export interface CompleteCookingPlanRequest {
  items: CompleteCookingPlanItemInput[];
}

export interface CookingPlanDeduction {
  canonical_item_id: string;
  lot_id: string;
  deducted_qty: string;
  base_unit: BaseUnit;
  ledger_entry_id: string;
}

export interface CompleteCookingPlanResponse {
  cooking_plan_id: string;
  status: 'completed';
  completed_at: string;
  deductions: CookingPlanDeduction[];
  meal_history_id?: string | null;
}

export interface MealHistoryIngredientUsed {
  canonical_item_id: string;
  name?: string;
  planned_qty?: string;
  actual_qty?: string;
  waste_qty?: string;
  base_unit?: BaseUnit | string;
}

export interface MealHistoryItem {
  id: string;
  household_id: string;
  cooking_plan_id: string;
  cooked_by: string;
  recipe_id?: string | null;
  recipe_title: string;
  meal_type?: string | null;
  cuisine?: string | null;
  servings: number;
  prep_minutes?: number | null;
  cook_minutes?: number | null;
  ingredients_used: MealHistoryIngredientUsed[];
  substitutions: string[];
  recipe_snapshot?: {
    description?: string | null;
    steps?: string[];
    substitutions?: string[];
    modifications?: string | null;
  } | null;
  cooked_at: string;
  rating?: number | null;
  feedback_tags: string[];
  feedback_comment?: string | null;
  would_make_again?: boolean | null;
  feedback_updated_at?: string | null;
  cook_count: number;
}

export interface MealHistoryListResponse {
  items: MealHistoryItem[];
  total: number;
  frequently_cooked: string[];
  favourite_meals: string[];
  disliked_meals: string[];
}

export interface MealFeedbackRequest {
  rating?: number | null;
  feedback_tags?: string[];
  feedback_comment?: string | null;
  would_make_again?: boolean | null;
}

export interface SpendByCategory {
  category: string;
  total_paise: number;
}

export interface SpendByMerchant {
  merchant_name: string;
  total_paise: number;
  purchase_count: number;
}

export interface SpendByProduct {
  product_name: string;
  category: string;
  total_paise: number;
  total_qty: number;
  base_unit: string;
  avg_unit_price_paise: number;
  latest_unit_price_paise: number;
  price_change_paise: number;
}

export interface SpendingTrendPoint {
  date: string;
  total_paise: number;
  purchase_count: number;
}

export interface PeriodPurchase {
  id: string;
  merchant_name: string;
  purchased_on: string;
  receipt_category: string;
  total_amount_paise: number;
  grocery_paise: number;
  outside_food_paise: number;
  unclassified_paise?: number;
  discount_paise: number;
  tax_paise: number;
  additional_charges_paise: number;
  item_count: number;
}

export type BudgetPeriodType = 'daily' | 'weekly' | 'monthly' | 'yearly';
export type BudgetStatusLevel = 'within_budget' | 'approaching_limit' | 'fully_used' | 'exceeded';

export interface BudgetStatus {
  limit_paise: number;
  actual_spend_paise: number;
  remaining_paise: number;
  exceeded_paise: number;
  percentage_used: number;
  status: BudgetStatusLevel;
}

export interface PeriodBudgetEvaluation {
  id: string;
  period_type: BudgetPeriodType;
  effective_from: string;
  overall: BudgetStatus;
  grocery?: BudgetStatus | null;
  outside_food?: BudgetStatus | null;
}

export interface BudgetConfig {
  id: string;
  household_id: string;
  period_type: BudgetPeriodType;
  overall_limit_paise: number;
  grocery_limit_paise?: number | null;
  outside_food_limit_paise?: number | null;
  effective_from: string;
  created_at: string;
  updated_at: string;
}

export interface BudgetUpsertRequest {
  period_type: BudgetPeriodType;
  overall_limit_paise: number;
  grocery_limit_paise?: number | null;
  outside_food_limit_paise?: number | null;
  effective_from?: string | null;
}

export interface BudgetListResponse {
  items: BudgetConfig[];
  current_by_period: Record<string, BudgetConfig>;
}

export interface ItemPriceHistoryEntry {
  purchase_id: string;
  merchant_name: string | null;
  purchased_on: string;
  purchased_qty: number;
  base_unit: BaseUnit;
  pack_size?: string | null;
  raw_text: string;
  unit_price_paise: number;
  normalized_std_price_paise: number;
  line_total_paise: number;
  currency_code: string;
}

export interface ItemPriceHistoryResponse {
  canonical_item_id: string;
  product_name: string;
  base_unit: BaseUnit;
  standard_unit_label: string;
  current_price_paise_per_std: number | null;
  latest_price_paise_per_std: number | null;
  avg_price_paise_per_std: number | null;
  price_change_paise: number | null;
  price_change_pct: number | null;
  entries: ItemPriceHistoryEntry[];
}

export interface InsightsSummaryResponse {
  period_days: number;
  total_spend_paise: number;
  total_waste_paise: number;
  expiring_soon_count: number;
  spend_by_category: SpendByCategory[];
  spend_today_paise?: number;
  spend_this_week_paise?: number;
  spend_this_month_paise?: number;
  gross_subtotal_paise?: number;
  total_discounts_paise?: number;
  total_tax_paise?: number;
  total_cgst_paise?: number;
  total_sgst_paise?: number;
  total_igst_paise?: number;
  total_additional_charges_paise?: number;
  total_rounding_paise?: number;
  final_amount_paid_paise?: number;
  purchase_count?: number;
  average_purchase_paise?: number;
  estimated_replenishment_paise?: number;
  spend_by_merchant?: SpendByMerchant[];
  spend_by_product?: SpendByProduct[];
  spending_trends?: SpendingTrendPoint[];
  outside_food_spend_paise?: number;
  grocery_spend_paise?: number;
  outside_food_count?: number;
  spend_by_meal?: Array<{ meal_type: string; total_paise: number; count: number }>;
  previous_period_spend_paise?: number | null;
  previous_grocery_spend_paise?: number | null;
  previous_outside_food_spend_paise?: number | null;
  selected_year?: number | null;
  selected_month?: number | null;
  period_type?: 'daily' | 'weekly' | 'monthly' | 'yearly' | 'rolling' | string | null;
  period_start?: string | null;
  period_end?: string | null;
  elapsed_days?: number;
  average_daily_spend_paise?: number;
  unclassified_spend_paise?: number;
  accounting_discrepancy_paise?: number;
  budget?: PeriodBudgetEvaluation | null;
  period_purchases?: PeriodPurchase[];
  all_time_spend_paise?: number;
  all_time_grocery_spend_paise?: number;
  all_time_outside_food_spend_paise?: number;
}

export interface ReplenishmentSuggestion {
  canonical_item_id: string;
  name: string;
  current_qty: string;
  low_stock_threshold_qty: string;
  suggested_buy_qty: string;
  base_unit: BaseUnit;
  reason: string;
}

export interface ReplenishmentSuggestionsResponse {
  items: ReplenishmentSuggestion[];
}

export type ConfidenceLevel = 'insufficient_data' | 'low' | 'medium' | 'high';
export type StockStatus = 'depleted' | 'low_stock' | 'running_out_soon' | 'adequate' | 'unknown';
export type RecommendationGroup = 'buy_now' | 'buy_soon' | 'regular_purchases' | 'dismissed';
export type FeedbackAction =
  | 'purchased'
  | 'already_have_enough'
  | 'dismissed'
  | 'not_relevant'
  | 'remind_later';
export type ShoppingListSourceType =
  | 'depleted'
  | 'low_stock'
  | 'predicted_replenishment'
  | 'regular_purchase'
  | 'recipe_missing'
  | 'manual';
export type ShoppingListStatus = 'pending' | 'purchased' | 'dismissed';

export interface ReplenishmentItem {
  canonical_item_id: string;
  item_name: string;
  category: string;
  base_unit: string;
  group: RecommendationGroup;
  stock_status: StockStatus;
  available_qty: number;
  reserved_qty: number;
  usable_qty: number;
  ingredient_usable_qty?: number | null;
  expired_qty: number;
  normalized_stock_status?: 'SUFFICIENT' | 'LOW' | 'OUT_OF_STOCK' | 'UNKNOWN';
  eligibility_status?: 'ELIGIBLE' | 'SUPPRESSED';
  suppression_reason?: string | null;
  low_stock_threshold_qty: number;
  earliest_expires_on?: string | null;
  purchase_count: number;
  last_purchase_date?: string | null;
  last_merchant?: string | null;
  avg_interval_days?: number | null;
  median_interval_days?: number | null;
  interval_variability_days?: number | null;
  estimated_next_purchase_date?: string | null;
  avg_daily_consumption?: number | null;
  recent_consumption_trend?: string | null;
  estimated_days_remaining?: number | null;
  prediction_horizon_days?: number;
  predicted_requirement_qty?: number | null;
  net_requirement_qty?: number | null;
  recommended_qty?: number | null;
  recommended_unit?: string | null;
  quantity_basis: 'consumption_prediction' | 'historical_pattern' | 'user_target' | 'insufficient_data';
  evidence_basis?: 'consumption_based' | 'pattern_based' | 'user_target_based' | 'insufficient_data';
  unit_mismatch: boolean;
  unit_clarification_note?: string | null;
  confidence: ConfidenceLevel;
  confidence_reason: string;
  reason: string;
  active_feedback_action?: FeedbackAction | null;
  remind_after?: string | null;
  dismissal_count: number;
}

export interface DataQualityIssue {
  canonical_item_id: string;
  item_name: string;
  issue_type: 'excluded_non_food' | 'uncertain_identity' | 'unit_incompatibility';
  detail: string;
}

export interface ReplenishmentRecommendationsResponse {
  generated_at: string;
  buy_now: ReplenishmentItem[];
  buy_soon: ReplenishmentItem[];
  regular_purchases: ReplenishmentItem[];
  dismissed: ReplenishmentItem[];
  needs_more_data?: ReplenishmentItem[];
  data_quality_issues: DataQualityIssue[];
  total_tracked_items: number;
}

export interface ShoppingListSourceEntry {
  source: ShoppingListSourceType;
  ref_id?: string | null;
  added_qty?: number | null;
  added_unit?: string | null;
  added_at: string;
}

export interface ShoppingListItem {
  id: string;
  household_id: string;
  canonical_item_id?: string | null;
  item_name: string;
  quantity?: number | null;
  unit?: string | null;
  primary_source: ShoppingListSourceType;
  sources: ShoppingListSourceEntry[];
  notes?: string | null;
  status: ShoppingListStatus;
  unit_conflict_note?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ShoppingListResponse {
  pending: ShoppingListItem[];
  purchased: ShoppingListItem[];
  dismissed: ShoppingListItem[];
}


export type DiscoveryRequirementMode =
  | 'exact_variant'
  | 'compatible_family'
  | 'generic_ingredient';

export type DiscoveryRequirementType = 'required' | 'optional';

export type DiscoveryMatchStatus = 'available' | 'partial' | 'missing';

export type DiscoveryMatchMethod = 'primary' | 'verified_substitution';

export type DiscoveryMatchReason =
  | 'MISSING_IN_PANTRY'
  | 'INSUFFICIENT_QUANTITY'
  | 'INCOMPARABLE_UNIT'
  | 'UNVERIFIED_SUBSTITUTION_ONLY';

export type DiscoveryBucket = 'can_make_now' | 'almost_there' | 'explore_more';

export interface MatchedSubstitutionOut {
  substitute_name: string;
  substitute_variant_id: string | null;
  substitute_family_id: string | null;
  conversion_ratio: string | null;
  conversion_unit: string | null;
  notes: string | null;
}

export interface IngredientMatchOut {
  dish_ingredient_id: string;
  ingredient_name: string;
  requirement_mode: DiscoveryRequirementMode;
  requirement_type: DiscoveryRequirementType;
  required_variant_id: string | null;
  required_family_id: string | null;
  status: DiscoveryMatchStatus;
  matched_via: DiscoveryMatchMethod | null;
  matched_canonical_ingredient_id: string | null;
  matched_variant_id: string | null;
  available_qty: string | null;
  available_unit: string | null;
  required_qty: string | null;
  required_unit: string | null;
  reason: DiscoveryMatchReason | null;
  expiring_soon: boolean;
  earliest_expires_on: string | null;
  matched_substitution: MatchedSubstitutionOut | null;
}

export interface DishMatchOut {
  dish_id: string;
  slug: string;
  canonical_name: string;
  alternative_names: string[];
  cuisine: string;
  region: string;
  meal_types: string[];
  diet_tags: string[];
  prep_minutes: number | null;
  cook_minutes: number | null;
  default_servings: number;
  bucket: DiscoveryBucket;
  required_total: number;
  required_available: number;
  required_partial: number;
  required_missing: number;
  missing_ingredient_names: string[];
  expiring_matches: string[];
  is_saved: boolean;
  user_feedback_action: string | null;
  requirements: IngredientMatchOut[];
}

export interface DishMatchCounts {
  can_make_now: number;
  almost_there: number;
  explore_more: number;
  [key: string]: number;
}

export interface DishMatchResponse {
  can_make_now: DishMatchOut[];
  almost_there: DishMatchOut[];
  explore_more: DishMatchOut[];
  counts: DishMatchCounts;
}

export type DailyMealCategory = 'breakfast' | 'lunch' | 'snacks' | 'dinner';
export type DailyPlanStatus = 'pending' | 'generating' | 'ready' | 'failed' | 'unavailable';

export interface DailyMealPlanRecipeOut {
  id: string;
  meal_category: DailyMealCategory;
  position: number;
  slug: string;
  title: string;
  description: string;
  servings: number;
  prep_minutes: number;
  cook_minutes?: number | null;
  difficulty?: string | null;
  cuisine?: string | null;
  dietary_tags: string[];
  equipment_required: string[];
  preparation_steps: string[];
  structured_steps: RecipeStep[];
  instructions: string[];
  substitutions: string[];
  cooking_tips: string[];
  troubleshooting_tips: string[];
  serving_result?: string | null;
  food_safety_notes: string[];
  availability_bucket: DiscoveryBucket;
  available_ingredients: RecipeChatIngredient[];
  partial_ingredients: RecipeChatIngredient[];
  missing_ingredients: RecipeChatIngredient[];
  optional_ingredients: RecipeChatIngredient[];
  quality_status: string;
  quality_warnings: string[];
}

export interface DailyMealPlanResponse {
  status: DailyPlanStatus;
  local_date: string;
  timezone: string;
  active_meal_category: DailyMealCategory;
  auto_attempted: boolean;
  manual_regeneration_count: number;
  manual_regeneration_limit: number;
  is_stale_fallback: boolean;
  onboarding_required?: boolean;
  generated_at?: string | null;
  weather_summary?: string | null;
  provider_metadata: Record<string, unknown>;
  error_code?: string | null;
  error_message?: string | null;
  categories: Record<DailyMealCategory, DailyMealPlanRecipeOut[]>;
  recipes: DailyMealPlanRecipeOut[];
}
