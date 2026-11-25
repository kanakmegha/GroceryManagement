/** Response shapes of the read-only /api/v1/demo/* endpoints (mirrors backend demo schemas). */

export type NumericLike = number | string;

export interface DemoInventorySummaryItem {
  canonical_item_id: string;
  name: string;
  slug: string;
  category: string;
  secondary_category?: string | null;
  total_remaining_qty: NumericLike;
  base_unit: string;
  active_lot_count: number;
  earliest_expires_on?: string | null;
  has_expired_lots: boolean;
}

export interface DemoInventoryLot {
  id: string;
  canonical_item_id: string;
  item_name: string;
  category: string;
  initial_qty: NumericLike;
  remaining_qty: NumericLike;
  base_unit: string;
  unit_cost_paise: number;
  purchased_at: string;
  expires_on?: string | null;
  lot_status: string;
}

export interface DemoInventoryResponse {
  demo_mode: true;
  summary: DemoInventorySummaryItem[];
  lots: DemoInventoryLot[];
  total_items: number;
  total_lots: number;
  expiring_soon_count: number;
  limit: number;
  offset: number;
}

export interface DemoPurchaseItem {
  id: string;
  line_index: number;
  display_name: string;
  brand?: string | null;
  category: string;
  normalized_qty: NumericLike;
  base_unit: string;
  unit_price_paise: number;
  line_total_paise: number;
}

export interface DemoPurchase {
  id: string;
  status: string;
  merchant_name: string;
  purchased_on: string;
  currency_code: string;
  receipt_category: string;
  total_amount_paise: number;
  tax_paise: number;
  discount_paise: number;
  item_count: number;
  items: DemoPurchaseItem[];
}

export interface DemoPurchasesResponse {
  demo_mode: true;
  items: DemoPurchase[];
  total: number;
  limit: number;
  offset: number;
}

export interface DemoReceipt {
  id: string;
  merchant_name: string;
  purchased_on: string;
  currency_code: string;
  receipt_category: string;
  document_type: string;
  total_amount_paise: number;
  tax_paise: number;
  discount_paise: number;
  item_count: number;
  items: DemoPurchaseItem[];
}

export interface DemoReceiptsResponse {
  demo_mode: true;
  items: DemoReceipt[];
  total: number;
  limit: number;
  offset: number;
}

export interface DemoIngredient {
  name: string;
  quantity: string;
  base_unit: string;
  essential: boolean;
  status: string;
  available: boolean;
}

export interface DemoRecipeStep {
  step_number: number;
  title: string;
  instruction: string;
  duration_minutes?: number | null;
}

export type DemoMealCategory = 'breakfast' | 'lunch' | 'snacks' | 'dinner';

export interface DemoDailyRecipe {
  id: string;
  meal_category: DemoMealCategory;
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
  structured_steps: DemoRecipeStep[];
  instructions: string[];
  availability_bucket: 'can_make_now' | 'almost_there' | 'explore_more';
  available_ingredients: DemoIngredient[];
  partial_ingredients: DemoIngredient[];
  missing_ingredients: DemoIngredient[];
  optional_ingredients: DemoIngredient[];
}

export interface DemoDailyPlanResponse {
  demo_mode: true;
  status: 'ready' | 'empty';
  local_date: string;
  timezone: string;
  active_meal_category: DemoMealCategory;
  generated_at?: string | null;
  categories: Record<DemoMealCategory, DemoDailyRecipe[]>;
  recipes: DemoDailyRecipe[];
}

export interface DemoCookingPlan {
  id: string;
  recipe_title: string;
  status: string;
  planned_servings: number;
  started_at: string;
}

export interface DemoMealHistory {
  id: string;
  recipe_title: string;
  servings: number;
  cooked_at: string;
  rating?: number | null;
  would_make_again?: boolean | null;
}

export interface DemoRecipesResponse {
  demo_mode: true;
  preferences: {
    diet_type: string;
    preferred_cuisines: string[];
    preferred_meal_types: string[];
    max_prep_minutes?: number | null;
  };
  daily_recipes: DemoDailyRecipe[];
  cooking_plans: DemoCookingPlan[];
  meal_history: DemoMealHistory[];
  total_cooking_plans: number;
  total_meal_history: number;
}

export interface DemoAnalyticsResponse {
  demo_mode: true;
  currency_code: string;
  total_spend_paise: number;
  grocery_spend_paise: number;
  outside_food_spend_paise: number;
  purchase_count: number;
  category_breakdown: { category: string; total_paise: number; item_count: number }[];
  merchant_breakdown: { merchant_name: string; total_paise: number; purchase_count: number }[];
  monthly_trend: {
    year: number;
    month: number;
    total_paise: number;
    grocery_paise: number;
    outside_food_paise: number;
    purchase_count: number;
  }[];
  budgets: {
    id: string;
    period_type: string;
    overall_limit_paise: number;
    grocery_limit_paise?: number | null;
    outside_food_limit_paise?: number | null;
    effective_from: string;
  }[];
}

export interface DemoDashboardResponse {
  demo_mode: true;
  household: { name: string; currency_code: string };
  inventory_item_count: number;
  active_lot_count: number;
  expiring_soon_lots: DemoInventoryLot[];
  recent_purchases: DemoPurchase[];
  daily_plan: DemoDailyPlanResponse;
  analytics: DemoAnalyticsResponse;
}
