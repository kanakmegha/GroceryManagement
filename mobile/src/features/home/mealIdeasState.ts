import type { DailyMealPlanResponse } from '../../shared/api/types';

export type HomeMealIdeasState =
  | 'loading'
  | 'onboarding'
  | 'pending'
  | 'error'
  | 'empty'
  | 'ready';

export const DAILY_PLAN_POLL_INTERVAL_MS = 10_000;

export interface HomeMealIdeasStateInput {
  isLoading: boolean;
  isError: boolean;
  dailyPlanStatus?: string | null;
  onboardingRequired?: boolean;
  itemCount: number;
}

export function resolveHomeMealIdeasState({
  isLoading,
  isError,
  dailyPlanStatus,
  onboardingRequired = false,
  itemCount,
}: HomeMealIdeasStateInput): HomeMealIdeasState {
  if (isLoading) {
    return 'loading';
  }
  if (isError || dailyPlanStatus === 'failed') {
    return 'error';
  }
  if (dailyPlanStatus === 'ready') {
    return itemCount > 0 ? 'ready' : 'empty';
  }
  if (onboardingRequired) {
    return 'onboarding';
  }
  if (dailyPlanStatus === 'pending' || dailyPlanStatus === 'generating') {
    return 'pending';
  }
  if (itemCount <= 0) {
    return 'empty';
  }
  return 'ready';
}

/**
 * Lightweight polling interval for daily meal plans.
 * Polls every 10s ONLY when preferences are complete and the plan is pending/generating.
 */
export function getDailyPlanPollIntervalMs(
  plan: Pick<DailyMealPlanResponse, 'status' | 'onboarding_required'> | null | undefined
): number | false {
  if (!plan || plan.onboarding_required) {
    return false;
  }
  if (plan.status === 'pending' || plan.status === 'generating') {
    return DAILY_PLAN_POLL_INTERVAL_MS;
  }
  return false;
}
