declare const require: (id: string) => {
  readFileSync: (filePath: string, encoding: string) => string;
  resolve: (...paths: string[]) => string;
};
declare const __dirname: string;
const fs = require('fs');
const path = require('path');
import { ApiClientError } from '../../../shared/api/client';
import { householdQueryKeys } from '../../../shared/api/queryKeys';
import type {
  DailyMealPlanRecipeOut,
  DailyMealPlanResponse,
} from '../../../shared/api/types';
import {
  DAILY_MEAL_CATEGORIES,
  dailyRecipeToRecommendation,
  fetchDailyMealPlan,
  formatDailyPlanRegenerationError,
  getDailyMealCategoryLabel,
  getHouseholdMealCategory,
  isDailyPlanRegenerationLimitReached,
  refreshDailyPlanAvailability,
  regenerateDailyMealPlan,
  retryOrRegenerateDailyMealPlan,
} from '../dailyMealPlan';

describe('Home dailyMealPlan integration', () => {
  it('selects the right meal category from household-local time', () => {
    expect(
      getHouseholdMealCategory('Asia/Kolkata', new Date('2026-11-22T22:00:00Z'))
    ).toBe('breakfast');
    expect(
      getHouseholdMealCategory('Asia/Kolkata', new Date('2026-11-23T07:00:00Z'))
    ).toBe('lunch');
    expect(
      getHouseholdMealCategory('Asia/Kolkata', new Date('2026-11-23T11:00:00Z'))
    ).toBe('snacks');
    expect(
      getHouseholdMealCategory('Asia/Kolkata', new Date('2026-11-23T15:30:00Z'))
    ).toBe('dinner');
  });

  it('provides all 4 categories and labels', () => {
    expect(DAILY_MEAL_CATEGORIES).toEqual(['breakfast', 'lunch', 'snacks', 'dinner']);
    expect(DAILY_MEAL_CATEGORIES.map(getDailyMealCategoryLabel)).toEqual([
      'Breakfast',
      'Lunch',
      'Snacks',
      'Dinner',
    ]);
  });

  it('scopes daily meal plan query key by householdId', () => {
    expect(householdQueryKeys.dailyMealPlan('hh-1')).toEqual([
      'household',
      'hh-1',
      'daily-meal-plan',
    ]);
    expect(householdQueryKeys.dailyMealPlan('hh-2')).not.toEqual(
      householdQueryKeys.dailyMealPlan('hh-1')
    );
  });

  it('calls GET daily-plan and POST refresh-availability without LLM endpoints', async () => {
    const getMock = jest.fn().mockResolvedValue({ status: 'pending' });
    const postMock = jest.fn().mockResolvedValue({ status: 'ready' });
    const apiClient = { get: getMock, post: postMock } as unknown as Parameters<
      typeof fetchDailyMealPlan
    >[0];

    await fetchDailyMealPlan(apiClient);
    expect(getMock).toHaveBeenCalledWith('/api/v1/discover/daily-plan');

    await fetchDailyMealPlan(apiClient, 'lunch');
    expect(getMock).toHaveBeenCalledWith('/api/v1/discover/daily-plan?category=lunch');

    await refreshDailyPlanAvailability(apiClient);
    expect(postMock).toHaveBeenCalledWith(
      '/api/v1/discover/daily-plan/refresh-availability',
      {}
    );
  });

  it('maps a DailyMealPlanRecipeOut to RecipeRecommendation for Home detail modal', () => {
    const sample: DailyMealPlanRecipeOut = {
      id: 'rec-1',
      meal_category: 'breakfast',
      position: 1,
      slug: 'puffed-phulka-breakfast-1',
      title: 'Puffed Phulka',
      description: 'Light puffed whole wheat flatbread.',
      servings: 2,
      prep_minutes: 20,
      cook_minutes: 10,
      difficulty: 'easy',
      cuisine: 'Indian',
      dietary_tags: ['vegetarian'],
      equipment_required: ['tawa', 'tongs'],
      preparation_steps: ['Measure 150 g flour.'],
      structured_steps: [],
      instructions: ['Knead dough.', 'Roll thin.', 'Puff on flame.'],
      substitutions: [],
      cooking_tips: [],
      troubleshooting_tips: [],
      serving_result: 'Soft puffed phulkas.',
      food_safety_notes: [],
      availability_bucket: 'can_make_now',
      available_ingredients: [
        {
          name: 'Whole wheat flour',
          quantity: '150',
          base_unit: 'g',
          available: true,
        },
      ],
      partial_ingredients: [],
      missing_ingredients: [],
      optional_ingredients: [],
      quality_status: 'valid',
      quality_warnings: [],
    };
    const mapped = dailyRecipeToRecommendation(sample);
    expect(mapped.recipe_id).toBe('rec-1');
    expect(mapped.recommendation_type).toBe('COOK_NOW');
    expect(mapped.ingredients?.[0].name).toBe('Whole wheat flour');
    expect(mapped.equipment_required).toEqual(['tawa', 'tongs']);
  });

  it('does not include automatic legacy recommendation queries in HomeScreen', () => {
    const homeSource = fs.readFileSync(
      path.resolve(__dirname, '../HomeScreen.tsx'),
      'utf8'
    );
    expect(homeSource).not.toContain('/api/v1/recipes/recommendations');
    expect(homeSource).not.toContain('homeRecommendations');
    expect(homeSource).toContain('setSelectedDailyCategory(null)');
    expect(homeSource).toContain('handleRetryDailyPlan');
  });

  describe('Daily meal plan regeneration flow', () => {
    const basePlan = (
      overrides: Partial<DailyMealPlanResponse> = {}
    ): DailyMealPlanResponse => ({
      status: 'failed',
      local_date: '2026-11-25',
      timezone: 'Asia/Kolkata',
      active_meal_category: 'breakfast',
      auto_attempted: true,
      manual_regeneration_count: 0,
      manual_regeneration_limit: 1,
      is_stale_fallback: false,
      provider_metadata: {},
      error_code: 'MISSING_TOKEN',
      error_message: 'HF_TOKEN is not configured.',
      categories: { breakfast: [], lunch: [], snacks: [], dinner: [] },
      recipes: [],
      ...overrides,
    });

    it('invokes POST /api/v1/discover/daily-plan/regenerate when stored plan is failed and refreshes cache on success', async () => {
      const readyPlan = basePlan({
        status: 'ready',
        manual_regeneration_count: 1,
        error_code: null,
        error_message: null,
      });
      const postMock = jest.fn().mockResolvedValue(readyPlan);
      const getMock = jest.fn();
      const onPlanUpdated = jest.fn();
      const refetchPlan = jest.fn();
      const apiClient = { get: getMock, post: postMock } as unknown as Parameters<
        typeof regenerateDailyMealPlan
      >[0];

      const res = await retryOrRegenerateDailyMealPlan({
        apiClient,
        currentPlan: basePlan(),
        isRegenerating: false,
        refetchPlan,
        onPlanUpdated,
      });

      expect(postMock).toHaveBeenCalledTimes(1);
      expect(postMock).toHaveBeenCalledWith(
        '/api/v1/discover/daily-plan/regenerate',
        {},
        { timeoutMs: 90_000 }
      );
      expect(getMock).not.toHaveBeenCalled();
      expect(onPlanUpdated).toHaveBeenCalledWith(readyPlan);
      expect(res.action).toBe('regenerated');
      expect(res.plan?.status).toBe('ready');
      expect(res.errorMessage).toBeUndefined();
    });

    it('prevents duplicate submissions while regeneration is in flight', async () => {
      const postMock = jest.fn();
      const refetchPlan = jest.fn();
      const apiClient = { post: postMock } as unknown as Parameters<
        typeof regenerateDailyMealPlan
      >[0];

      const res = await retryOrRegenerateDailyMealPlan({
        apiClient,
        currentPlan: basePlan(),
        isRegenerating: true,
        refetchPlan,
      });

      expect(res.action).toBe('skipped_in_flight');
      expect(postMock).not.toHaveBeenCalled();
      expect(refetchPlan).not.toHaveBeenCalled();
    });

    it('does not trigger regeneration for ready plans', async () => {
      const postMock = jest.fn();
      const refetchPlan = jest.fn();
      const apiClient = { post: postMock } as unknown as Parameters<
        typeof regenerateDailyMealPlan
      >[0];

      const res = await retryOrRegenerateDailyMealPlan({
        apiClient,
        currentPlan: basePlan({ status: 'ready', error_code: null, error_message: null }),
        isRegenerating: false,
        refetchPlan,
      });

      expect(res.action).toBe('skipped_ready');
      expect(postMock).not.toHaveBeenCalled();
      expect(refetchPlan).not.toHaveBeenCalled();
    });

    it('surfaces useful error when regeneration returns a failed plan (never silently succeeds)', async () => {
      const failedAgain = basePlan({
        status: 'failed',
        manual_regeneration_count: 1,
        error_code: 'PROVIDER_ERROR',
        error_message: 'Hugging Face Router returned 503.',
      });
      const postMock = jest.fn().mockResolvedValue(failedAgain);
      const onPlanUpdated = jest.fn();
      const apiClient = { post: postMock } as unknown as Parameters<
        typeof regenerateDailyMealPlan
      >[0];

      const res = await retryOrRegenerateDailyMealPlan({
        apiClient,
        currentPlan: basePlan(),
        isRegenerating: false,
        refetchPlan: jest.fn(),
        onPlanUpdated,
      });

      expect(onPlanUpdated).toHaveBeenCalledWith(failedAgain);
      expect(res.action).toBe('regenerated');
      expect(res.plan?.status).toBe('failed');
      expect(res.errorMessage).toContain('Hugging Face Router returned 503');
    });

    it('handles 429 rate-limit / in-progress response and pre-checks exhausted daily allowance', async () => {
      const postMock = jest.fn().mockRejectedValue(
        new ApiClientError({
          status: 429,
          code: 'HTTP_429',
          message: 'Daily manual regeneration limit (1/day) reached or generation is currently in progress.',
        })
      );
      const refetchPlan = jest.fn().mockResolvedValue(
        basePlan({ manual_regeneration_count: 1 })
      );
      const apiClient = { post: postMock } as unknown as Parameters<
        typeof regenerateDailyMealPlan
      >[0];

      const res = await retryOrRegenerateDailyMealPlan({
        apiClient,
        currentPlan: basePlan(),
        isRegenerating: false,
        refetchPlan,
      });

      expect(res.errorMessage).toMatch(/limit reached|in progress/i);
      expect(refetchPlan).toHaveBeenCalledTimes(1);

      // Once allowance is exhausted, skips POST and communicates limit clearly
      const postMock2 = jest.fn();
      const exhausted = basePlan({ manual_regeneration_count: 1, manual_regeneration_limit: 1 });
      expect(isDailyPlanRegenerationLimitReached(exhausted)).toBe(true);
      const res2 = await retryOrRegenerateDailyMealPlan({
        apiClient: { post: postMock2 } as unknown as Parameters<
          typeof regenerateDailyMealPlan
        >[0],
        currentPlan: exhausted,
        isRegenerating: false,
        refetchPlan: jest.fn(),
      });
      expect(res2.action).toBe('limit_reached');
      expect(res2.errorMessage).toMatch(/limit reached/i);
      expect(postMock2).not.toHaveBeenCalled();
    });

    it('formats auth (401/403) and timeout errors clearly', () => {
      const authErr = new ApiClientError({
        status: 401,
        code: 'UNAUTHORIZED',
        message: 'Invalid token',
      });
      expect(formatDailyPlanRegenerationError(authErr)).toMatch(/Authentication failed/i);

      const timeoutErr = new ApiClientError({
        status: 0,
        code: 'REQUEST_TIMEOUT',
        message: 'Request timed out',
        isNetworkError: true,
        isTimeout: true,
      });
      expect(formatDailyPlanRegenerationError(timeoutErr)).toMatch(/still in progress|timed out/i);
    });
  });
});
