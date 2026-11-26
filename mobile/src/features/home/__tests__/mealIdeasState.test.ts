import { ApiClientError } from '../../../shared/api/client';
import {
  formatDailyPlanRegenerationError,
  isPreferencesRequiredError,
} from '../dailyMealPlan';
import {
  DAILY_PLAN_POLL_INTERVAL_MS,
  getDailyPlanPollIntervalMs,
  resolveHomeMealIdeasState,
} from '../mealIdeasState';

describe('resolveHomeMealIdeasState', () => {
  it('returns loading while fetching', () => {
    expect(
      resolveHomeMealIdeasState({
        isLoading: true,
        isError: false,
        dailyPlanStatus: undefined,
        itemCount: 0,
      })
    ).toBe('loading');
  });

  it('returns error on API failure or failed daily plan even when itemCount is 0', () => {
    expect(
      resolveHomeMealIdeasState({
        isLoading: false,
        isError: true,
        dailyPlanStatus: undefined,
        itemCount: 0,
      })
    ).toBe('error');
    expect(
      resolveHomeMealIdeasState({
        isLoading: false,
        isError: false,
        dailyPlanStatus: 'failed',
        itemCount: 0,
      })
    ).toBe('error');
  });

  it('returns onboarding instead of pending when onboardingRequired is true and no ready plan exists', () => {
    expect(
      resolveHomeMealIdeasState({
        isLoading: false,
        isError: false,
        dailyPlanStatus: 'pending',
        onboardingRequired: true,
        itemCount: 0,
      })
    ).toBe('onboarding');
  });

  it('preserves existing ready plans even if onboardingRequired is true', () => {
    expect(
      resolveHomeMealIdeasState({
        isLoading: false,
        isError: false,
        dailyPlanStatus: 'ready',
        onboardingRequired: true,
        itemCount: 2,
      })
    ).toBe('ready');
  });

  it('returns pending when preferences are completed and daily plan is pending or generating', () => {
    expect(
      resolveHomeMealIdeasState({
        isLoading: false,
        isError: false,
        dailyPlanStatus: 'pending',
        onboardingRequired: false,
        itemCount: 0,
      })
    ).toBe('pending');
    expect(
      resolveHomeMealIdeasState({
        isLoading: false,
        isError: false,
        dailyPlanStatus: 'generating',
        onboardingRequired: false,
        itemCount: 0,
      })
    ).toBe('pending');
  });

  it('returns empty only when ready/settled with no error, not pending, and itemCount is 0', () => {
    expect(
      resolveHomeMealIdeasState({
        isLoading: false,
        isError: false,
        dailyPlanStatus: 'ready',
        itemCount: 0,
      })
    ).toBe('empty');
  });

  it('returns ready when daily plan is ready and recipes are present', () => {
    expect(
      resolveHomeMealIdeasState({
        isLoading: false,
        isError: false,
        dailyPlanStatus: 'ready',
        itemCount: 2,
      })
    ).toBe('ready');
  });
});

describe('getDailyPlanPollIntervalMs', () => {
  it('disables polling when onboarding_required is true', () => {
    expect(
      getDailyPlanPollIntervalMs({ status: 'pending', onboarding_required: true })
    ).toBe(false);
  });

  it('polls every 10s when pending or generating and onboarding is complete', () => {
    expect(
      getDailyPlanPollIntervalMs({ status: 'pending', onboarding_required: false })
    ).toBe(DAILY_PLAN_POLL_INTERVAL_MS);
    expect(
      getDailyPlanPollIntervalMs({ status: 'generating', onboarding_required: false })
    ).toBe(DAILY_PLAN_POLL_INTERVAL_MS);
  });

  it('stops polling when ready or failed', () => {
    expect(
      getDailyPlanPollIntervalMs({ status: 'ready', onboarding_required: false })
    ).toBe(false);
    expect(
      getDailyPlanPollIntervalMs({ status: 'failed', onboarding_required: false })
    ).toBe(false);
  });
});

describe('409 PREFERENCES_REQUIRED error handling', () => {
  it('detects and formats 409 PREFERENCES_REQUIRED gracefully', () => {
    const err = new ApiClientError({
      status: 409,
      code: 'PREFERENCES_REQUIRED',
      message: 'Save your household food preferences before generating a daily meal plan.',
    });
    expect(isPreferencesRequiredError(err)).toBe(true);
    expect(formatDailyPlanRegenerationError(err)).toBe(
      'Save your food preferences first to generate your daily meal plan.'
    );
  });
});
