import {
  computeCookingProgress,
  resolveCookingStep,
  stepCookingIndex,
  type RecipeDetailTarget,
} from '../recipeHelpers';

describe('Phase 7.6 Dedicated Cooking Mode & Structured Steps', () => {
  const rotiDalRecipe: RecipeDetailTarget = {
    recipe_id: 'cat-roti-urad-dal',
    title: 'Whole Wheat Roti & Urad Dal Tadka',
    servings: 2,
    source_type: 'ai_generated',
    source_label: 'Catalogue',
    recommendation_type: 'COOK_NOW',
    ingredients: [
      { name: 'Whole Wheat Flour (Atta)', quantity: '200', base_unit: 'g', available: true },
      { name: 'Urad Dal', quantity: '150', base_unit: 'g', available: true },
      { name: 'Oil', quantity: '15', base_unit: 'ml', available: true },
    ],
    instructions: [
      'Rinse and pressure-cook the urad dal',
      'Prepare the spiced tadka',
      'Combine tadka and simmer the dal',
      'Knead and rest the roti dough',
      'Roll and cook rotis on a hot tawa',
      'Finish and serve hot',
    ],
    structured_steps: [
      {
        step_number: 1,
        title: 'Rinse and pressure-cook the urad dal',
        instruction: 'Rinse Urad Dal and pressure-cook for 3-4 whistles until soft.',
        ingredients_used: ['Urad Dal'],
        duration_minutes: 15,
        heat_level: 'medium',
        technique: 'pressure-cooking',
        completion_cue: 'Dal grains mash effortlessly.',
      },
      {
        step_number: 2,
        title: 'Prepare the spiced tadka',
        instruction: 'Heat Oil in a tadka pan and temper spices without burning.',
        ingredients_used: ['Oil'],
        duration_minutes: 3,
        heat_level: 'medium-low',
        technique: 'tempering',
        completion_cue: 'Oil is fragrant.',
      },
      {
        step_number: 3,
        title: 'Knead and rest the roti dough',
        instruction: 'Knead Whole Wheat Flour (Atta) into soft dough and rest 10 minutes.',
        ingredients_used: ['Whole Wheat Flour (Atta)'],
        duration_minutes: 10,
        heat_level: null,
        technique: 'kneading',
        completion_cue: 'Dough springs back lightly.',
      },
      {
        step_number: 4,
        title: 'Finish and serve hot',
        instruction: 'Serve hot.',
        ingredients_used: ['Urad Dal', 'Whole Wheat Flour (Atta)'],
        duration_minutes: null,
        heat_level: null,
        technique: 'plating',
        completion_cue: 'Served steaming hot.',
      },
    ],
    substitutions: [],
    cooking_tips: [],
    food_safety_notes: [],
    uses_expiring_items: [],
    external_links: [],
  };

  it('resolves structured step metadata including duration, heat level, ingredients, and cue', () => {
    const s1 = resolveCookingStep(rotiDalRecipe, 0);
    expect(s1.stepNumber).toBe(1);
    expect(s1.totalSteps).toBe(4);
    expect(s1.title).toBe('Rinse and pressure-cook the urad dal');
    expect(s1.ingredientsUsed).toEqual(['Urad Dal']);
    expect(s1.durationMinutes).toBe(15);
    expect(s1.heatLevel).toBe('medium');
    expect(s1.completionCue).toBe('Dal grains mash effortlessly.');

    const s4 = resolveCookingStep(rotiDalRecipe, 3);
    expect(s4.durationMinutes).toBeNull();
  });

  it('computes progress percentage and clamps Previous / Next navigation cleanly', () => {
    expect(computeCookingProgress(0, 4)).toBe(25);
    expect(computeCookingProgress(1, 4)).toBe(50);
    expect(computeCookingProgress(3, 4)).toBe(100);

    expect(stepCookingIndex(0, -1, 4)).toBe(0);
    expect(stepCookingIndex(0, 1, 4)).toBe(1);
    expect(stepCookingIndex(3, 1, 4)).toBe(3);
  });

  it('falls back gracefully to plain instructions when structured_steps is empty', () => {
    const plainOnly: RecipeDetailTarget = {
      ...rotiDalRecipe,
      structured_steps: [],
      instructions: ['Simmer milk for 5 minutes until warm.', 'Serve immediately.'],
    };
    const s0 = resolveCookingStep(plainOnly, 0);
    expect(s0.title).toBe('Step 1');
    expect(s0.durationMinutes).toBe(5);
    expect(resolveCookingStep(plainOnly, 1).durationMinutes).toBeNull();
  });
});
