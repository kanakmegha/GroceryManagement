import type { QueryClient } from '@tanstack/react-query';

export const householdQueryKeys = {
  all: (householdId: string | null) => ['household', householdId ?? 'default'] as const,
  inventorySummary: (householdId: string | null) =>
    ['household', householdId ?? 'default', 'inventory-summary'] as const,
  expiringLots: (householdId: string | null) =>
    ['household', householdId ?? 'default', 'expiring-lots'] as const,
  recentPurchases: (householdId: string | null) =>
    ['household', householdId ?? 'default', 'recent-purchases'] as const,
  insights: (householdId: string | null, periodKey: string | number) =>
    ['household', householdId ?? 'default', 'insights', periodKey] as const,
  homeRecommendations: (householdId: string | null) =>
    ['household', householdId ?? 'default', 'home-recommendations'] as const,
  cookingPlans: (householdId: string | null) =>
    ['household', householdId ?? 'default', 'cooking-plans'] as const,
  mealHistory: (householdId: string | null, filterKey: string) =>
    ['household', householdId ?? 'default', 'meal-history', filterKey] as const,
  replenishment: (householdId: string | null) =>
    ['household', householdId ?? 'default', 'replenishment'] as const,
  shoppingList: (householdId: string | null) =>
    ['household', householdId ?? 'default', 'shopping-list'] as const,
  dailyMealPlan: (householdId: string | null) =>
    ['household', householdId ?? 'default', 'daily-meal-plan'] as const,
};

/**
 * Invalidate cached pantry, insights, meal history, and Home recommendations
 * after purchases, inventory adjustments, cooking completion, or feedback.
 */
export async function invalidateHouseholdData(
  queryClient: QueryClient,
  householdId: string | null
): Promise<void> {
  await queryClient.invalidateQueries({
    queryKey: householdQueryKeys.all(householdId),
  });
}
