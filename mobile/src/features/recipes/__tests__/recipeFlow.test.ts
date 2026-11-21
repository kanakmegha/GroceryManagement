import {
  areUnitsCompatible,
  buildCompleteLineId,
  buildSelectionOnlyPrompt,
  computeCombinedDeductionByCanonical,
  convertCompatibleQty,
  detectStepMinutes,
  groupMealHistoryByDate,
  recommendationToDetail,
  scaleQuantityText,
  suggestionToDetail,
} from '../recipeHelpers';
import type {
  MealHistoryItem,
  RecipeChatSuggestion,
  RecipeRecommendation,
} from '../../../shared/api/types';

describe('AI Chef recipe detail & guided cooking helpers', () => {
  it('converts a recommendation into a complete AI-generated recipe detail with missing indicators', () => {
    const rec: RecipeRecommendation = {
      recipe_id: 'rec-1',
      title: 'Palak Paneer',
      description: 'Creamy spinach and paneer curry.',
      meal_type: 'dinner',
      servings: 2,
      prep_minutes: 15,
      cook_minutes: 20,
      difficulty: 'easy',
      source_type: 'ai_generated',
      source_label: 'AI-generated recipe',
      match_score: 0.75,
      matched_ingredients_count: 1,
      total_required_ingredients_count: 2,
      uses_expiring_soon: true,
      missing_ingredients: ['Spinach'],
      ingredients: [
        { canonical_item_id: 'cid-1', name: 'Paneer', required_qty: '200', base_unit: 'g', is_available: true },
        { canonical_item_id: null, name: 'Spinach', required_qty: '250', base_unit: 'g', is_available: false },
      ],
      instructions: [
        'Blanch spinach for 3 minutes and blend.',
        'Sear paneer cubes and simmer for 10 minutes.',
      ],
      substitutions: ['Replace paneer with firm tofu.'],
      cooking_tips: ['Add a pinch of nutmeg at the end.'],
      references: [],
    };

    const detail = recommendationToDetail(rec);
    expect(detail.source_type).toBe('ai_generated');
    expect(detail.source_label).toBe('AI-generated recipe');
    expect(detail.instructions.length).toBe(2);
    expect(detail.ingredients[0].available).toBe(true);
    expect(detail.ingredients[1].available).toBe(false);
    expect(detail.substitutions).toEqual(['Replace paneer with firm tofu.']);
  });

  it('filters out unverified external links and labels verified video vs written recipe links', () => {
    const sug: RecipeChatSuggestion = {
      title: 'Sambar',
      servings: 2,
      available_ingredients: [{ name: 'Toor Dal', quantity: '150', base_unit: 'g', available: true }],
      missing_ingredients: [],
      instructions: ['Pressure cook dal for 12 minutes.', 'Simmer with tamarind and sambar powder.'],
      external_links: [
        { title: 'Unverified Blog', url: 'https://fake.example.com', verified: false },
        {
          title: 'Hotel Sambar Video',
          url: 'https://www.youtube.com/watch?v=abc123',
          source: 'YouTube',
          link_type: 'video',
          verified: true,
        },
      ],
    };

    const detail = suggestionToDetail(sug);
    expect(detail.source_type).toBe('external_reference');
    expect(detail.external_links).toHaveLength(1);
    expect(detail.external_links[0].link_type).toBe('video');
  });

  it('scales ingredient quantities for serving adjustments and detects step timer minutes', () => {
    expect(scaleQuantityText('200', 2, 4)).toBe('400');
    expect(scaleQuantityText('1.5 cups', 2, 4)).toBe('3 cups');
    expect(detectStepMinutes('Simmer on low heat for 12 minutes until thickened.')).toBe(12);
    expect(detectStepMinutes('Chop onions finely.')).toBeNull();
  });

  it('groups meal history by date (newest first) and orders meals by meal type', () => {
    const base = {
      household_id: 'hh-1',
      cooking_plan_id: 'cp-1',
      cooked_by: 'u-1',
      servings: 2,
      ingredients_used: [],
      substitutions: [],
      feedback_tags: [],
      cook_count: 1,
    };
    const items: MealHistoryItem[] = [
      { ...base, id: 'm1', recipe_title: 'Paneer Curry', meal_type: 'dinner', cooked_at: '2026-10-27T14:00:00Z' },
      { ...base, id: 'm2', recipe_title: 'Vegetable Poha', meal_type: 'breakfast', cooked_at: '2026-10-27T08:30:00Z' },
      { ...base, id: 'm3', recipe_title: 'Idli Sambar', meal_type: 'breakfast', cooked_at: '2026-10-26T08:30:00Z' },
    ];
    const groups = groupMealHistoryByDate(items);
    expect(groups).toHaveLength(2);
    expect(groups[0].dateKey).toBe('2026-10-27');
    expect(groups[0].meals.map((m) => m.recipe_title)).toEqual(['Vegetable Poha', 'Paneer Curry']);
    expect(groups[1].dateKey).toBe('2026-10-26');
  });

  it('builds a valid AI Chef prompt from quick preference selections without typed input', () => {
    const prompt = buildSelectionOnlyPrompt({
      customText: '',
      preferences: ['High protein', 'South Indian', 'Spicy'],
      hungerLevel: 'Very hungry',
      maxMinutes: 30,
      servings: 2,
    });
    expect(prompt).toContain('High protein, South Indian, Spicy');
    expect(prompt).toContain('very hungry');
    expect(prompt).toContain('30 minutes');
  });

  it('buildCompleteLineId produces unique keys even when canonical_item_id is duplicated or missing line_id', () => {
    const k1 = buildCompleteLineId('plan-1', { canonical_item_id: 'cid-spinach' }, 0);
    const k2 = buildCompleteLineId('plan-1', { canonical_item_id: 'cid-spinach' }, 1);
    const k3 = buildCompleteLineId('plan-1', { line_id: 'persisted-1', canonical_item_id: 'cid-spinach' }, 2);
    expect(k1).not.toBe(k2);
    expect(k3).toBe('persisted-1');
  });

  it('computeCombinedDeductionByCanonical sums actual+waste across duplicate lines and excludes omitted lines', () => {
    const combined = computeCombinedDeductionByCanonical([
      { canonical_item_id: 'cid-spinach', actualUsedText: '60', wasteQtyText: '10', omitted: false },
      { canonical_item_id: 'cid-spinach', actualUsedText: '40', wasteQtyText: '5', omitted: false },
      { canonical_item_id: 'cid-oil', actualUsedText: '100', wasteQtyText: '0', omitted: true },
    ]);
    expect(combined['cid-spinach']).toBe(115);
    expect(combined['cid-oil']).toBeUndefined();
  });

  it('areUnitsCompatible and convertCompatibleQty never silently convert pcs to g or ml', () => {
    expect(areUnitsCompatible('g', 'kg')).toBe(true);
    expect(areUnitsCompatible('ml', 'l')).toBe(true);
    expect(areUnitsCompatible('pcs', 'g')).toBe(false);
    expect(areUnitsCompatible('pcs', 'ml')).toBe(false);
    expect(convertCompatibleQty(1, 'l', 'ml')).toBe(1000);
    expect(convertCompatibleQty(1, 'pcs', 'g')).toBeNull();

    const mixed = computeCombinedDeductionByCanonical([
      { canonical_item_id: 'cid-choc', base_unit: 'pcs', lotBaseUnit: 'pcs', actualUsedText: '1', wasteQtyText: '0', omitted: false },
      { canonical_item_id: 'cid-choc', base_unit: 'ml', lotBaseUnit: 'pcs', actualUsedText: '500', wasteQtyText: '0', omitted: false },
    ]);
    expect(mixed['cid-choc']).toBe(1);
  });
});
