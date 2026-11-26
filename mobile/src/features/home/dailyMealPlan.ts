import { ApiClientError, type ApiClient } from '../../shared/api/client';
import type {
  DailyMealCategory,
  DailyMealPlanRecipeOut,
  DailyMealPlanResponse,
  RecipeRecommendation,
} from '../../shared/api/types';

export const DAILY_MEAL_CATEGORIES: DailyMealCategory[] = [
  'breakfast',
  'lunch',
  'snacks',
  'dinner',
];

export function getDailyMealCategoryLabel(cat: DailyMealCategory): string {
  switch (cat) {
    case 'breakfast':
      return 'Breakfast';
    case 'lunch':
      return 'Lunch';
    case 'snacks':
      return 'Snacks';
    case 'dinner':
      return 'Dinner';
  }
}

/**
 * Derive current meal category from household IANA timezone:
 * Breakfast 03:00-10:59, Lunch 11:00-14:59, Snacks 15:00-17:59, Dinner 18:00-02:59.
 */
export function getHouseholdMealCategory(
  timezone: string,
  now: Date = new Date()
): DailyMealCategory {
  let hour = now.getHours();
  try {
    const formatted = new Intl.DateTimeFormat('en-US', {
      timeZone: timezone || 'Asia/Kolkata',
      hour: 'numeric',
      hour12: false,
    }).format(now);
    const parsed = Number.parseInt(formatted, 10);
    if (Number.isFinite(parsed)) {
      hour = parsed % 24;
    }
  } catch {
    const formatted = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Kolkata',
      hour: 'numeric',
      hour12: false,
    }).format(now);
    const parsed = Number.parseInt(formatted, 10);
    if (Number.isFinite(parsed)) {
      hour = parsed % 24;
    }
  }

  if (hour >= 3 && hour <= 10) return 'breakfast';
  if (hour >= 11 && hour <= 14) return 'lunch';
  if (hour >= 15 && hour <= 17) return 'snacks';
  return 'dinner';
}

export async function fetchDailyMealPlan(
  apiClient: ApiClient,
  category?: DailyMealCategory
): Promise<DailyMealPlanResponse> {
  const path = category
    ? `/api/v1/discover/daily-plan?category=${encodeURIComponent(category)}`
    : '/api/v1/discover/daily-plan';
  return apiClient.get<DailyMealPlanResponse>(path);
}

export async function refreshDailyPlanAvailability(
  apiClient: ApiClient
): Promise<DailyMealPlanResponse> {
  return apiClient.post<DailyMealPlanResponse>(
    '/api/v1/discover/daily-plan/refresh-availability',
    {}
  );
}

export async function regenerateDailyMealPlan(
  apiClient: ApiClient
): Promise<DailyMealPlanResponse> {
  return apiClient.post<DailyMealPlanResponse>(
    '/api/v1/discover/daily-plan/regenerate',
    {},
    { timeoutMs: 90_000 }
  );
}

export function isDailyPlanRegenerationLimitReached(
  plan: DailyMealPlanResponse | null | undefined
): boolean {
  if (!plan) return false;
  const limit = plan.manual_regeneration_limit;
  if (typeof limit !== 'number' || limit <= 0) return false;
  return (plan.manual_regeneration_count ?? 0) >= limit;
}

export function isPreferencesRequiredError(err: unknown): boolean {
  return err instanceof ApiClientError && err.status === 409 && err.code === 'PREFERENCES_REQUIRED';
}

export function formatDailyPlanRegenerationError(err: unknown): string {
  if (err instanceof ApiClientError) {
    if (err.status === 409 && err.code === 'PREFERENCES_REQUIRED') {
      return 'Save your food preferences first to generate your daily meal plan.';
    }
    if (err.status === 401 || err.status === 403) {
      return 'Authentication failed. Please sign in again and retry.';
    }
    if (err.status === 429) {
      return 'Daily regeneration limit reached (1 per day), or generation is already in progress. Wait a moment and refresh, or try again tomorrow.';
    }
    if (err.isTimeout || err.status === 504) {
      return 'Meal plan generation is still in progress or timed out. Please wait a moment and refresh.';
    }
    if (err.status >= 500 || err.isNetworkError) {
      return err.message || 'The recipe provider is temporarily unavailable. Please try again later.';
    }
    return err.message || 'Unable to regenerate today\'s meal plan right now.';
  }
  if (err instanceof Error && err.message) {
    return err.message;
  }
  return 'Unable to regenerate today\'s meal plan right now.';
}

export interface DailyPlanRetryResult {
  action:
    | 'regenerated'
    | 'refetched'
    | 'skipped_in_flight'
    | 'skipped_ready'
    | 'limit_reached';
  plan?: DailyMealPlanResponse;
  errorMessage?: string;
}

export async function retryOrRegenerateDailyMealPlan(params: {
  apiClient: ApiClient;
  currentPlan: DailyMealPlanResponse | null | undefined;
  isRegenerating: boolean;
  refetchPlan: () => Promise<DailyMealPlanResponse | undefined>;
  onPlanUpdated?: (plan: DailyMealPlanResponse) => void;
}): Promise<DailyPlanRetryResult> {
  const { apiClient, currentPlan, isRegenerating, refetchPlan, onPlanUpdated } = params;

  if (isRegenerating) {
    return { action: 'skipped_in_flight' };
  }

  if (currentPlan?.status === 'ready') {
    return { action: 'skipped_ready', plan: currentPlan };
  }

  if (currentPlan?.status === 'failed') {
    if (isDailyPlanRegenerationLimitReached(currentPlan)) {
      return {
        action: 'limit_reached',
        plan: currentPlan,
        errorMessage:
          'Daily regeneration limit reached (1 per day). Try again tomorrow or ask AI Chef.',
      };
    }

    try {
      const updated = await regenerateDailyMealPlan(apiClient);
      onPlanUpdated?.(updated);

      if (updated.status === 'failed') {
        return {
          action: 'regenerated',
          plan: updated,
          errorMessage:
            updated.error_message ||
            'Meal plan regeneration failed. Please try again later.',
        };
      }

      return { action: 'regenerated', plan: updated };
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 429) {
        await refetchPlan().catch(() => undefined);
      }
      return {
        action: 'regenerated',
        errorMessage: formatDailyPlanRegenerationError(err),
      };
    }
  }

  const refetched = await refetchPlan();
  return { action: 'refetched', plan: refetched };
}

/**
 * Map a stored DailyMealPlanRecipeOut into RecipeRecommendation so Home
 * reuses the existing Recipe Detail modal and Cooking Mode flow without extra LLM calls.
 */
export function dailyRecipeToRecommendation(
  item: DailyMealPlanRecipeOut
): RecipeRecommendation {
  const allIngs = [
    ...item.available_ingredients,
    ...item.partial_ingredients,
    ...item.missing_ingredients,
    ...item.optional_ingredients,
  ];
  const essentialCount =
    item.available_ingredients.length +
    item.partial_ingredients.length +
    item.missing_ingredients.length;
  const recType =
    item.availability_bucket === 'can_make_now'
      ? 'COOK_NOW'
      : item.availability_bucket === 'almost_there'
        ? 'COOK_WITH_SUBSTITUTIONS'
        : 'REQUIRES_SHOPPING';

  return {
    recipe_id: item.id,
    title: item.title,
    description: item.description,
    meal_type: item.meal_category,
    servings: item.servings || 2,
    prep_minutes: item.prep_minutes || 15,
    cook_minutes: item.cook_minutes ?? null,
    difficulty: item.difficulty ?? null,
    source_type: 'ai_generated',
    source_label: 'Daily AI Meal Plan',
    recommendation_type: recType,
    match_score:
      essentialCount > 0 ? item.available_ingredients.length / essentialCount : 0,
    matched_ingredients_count: item.available_ingredients.length,
    total_required_ingredients_count: essentialCount,
    uses_expiring_soon: false,
    missing_ingredients: item.missing_ingredients.map((m) => m.name),
    insufficient_ingredients: item.partial_ingredients.map((p) => p.name),
    ingredients: allIngs.map((ing) => ({
      canonical_item_id: ing.canonical_item_id ?? null,
      name: ing.name,
      required_qty: ing.quantity,
      base_unit: ing.base_unit,
      is_optional: item.optional_ingredients.some((o) => o.name === ing.name),
      essential: !item.optional_ingredients.some((o) => o.name === ing.name),
      status: ing.available ? 'available' : 'missing',
      available_qty: ing.available_qty ?? null,
      is_available: ing.available,
    })),
    equipment_required: item.equipment_required,
    preparation_steps: item.preparation_steps,
    instructions: item.instructions,
    structured_steps: item.structured_steps,
    quality_warnings: item.quality_warnings,
    substitutions: item.substitutions,
    cooking_tips: item.cooking_tips,
    troubleshooting_tips: item.troubleshooting_tips,
    serving_result: item.serving_result ?? null,
    food_safety_notes: item.food_safety_notes,
    references: [],
  };
}
