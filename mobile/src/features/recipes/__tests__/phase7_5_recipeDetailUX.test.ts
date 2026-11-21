import {
  computeRecipeAvailability,
  formatIngredientDetailPresentation,
  type RecipeDetailIngredient,
} from '../recipeHelpers';

describe('Phase 7.5 Recipe Detail Ingredient Availability & Stock Formatting', () => {
  const badamMilkIngredients: RecipeDetailIngredient[] = [
    {
      name: 'Milk',
      quantity: '400',
      base_unit: 'ml',
      available: true,
      status: 'available',
      available_qty: '1000',
      essential: true,
      is_optional: false,
    },
    {
      name: 'Almonds',
      quantity: '15',
      base_unit: 'g',
      available: true,
      status: 'available',
      available_qty: '3750',
      essential: true,
      is_optional: false,
    },
    {
      name: 'Turmeric Powder',
      quantity: '2',
      base_unit: 'g',
      available: false,
      status: 'missing',
      available_qty: null,
      essential: true,
      is_optional: false,
    },
    {
      name: 'Saffron',
      quantity: '1',
      base_unit: 'pcs',
      available: false,
      status: 'missing',
      available_qty: null,
      essential: false,
      is_optional: true,
    },
  ];

  it('formats tracked stock in readable compatible units relative to required quantity', () => {
    const almonds = formatIngredientDetailPresentation(badamMilkIngredients[1], 2, 2, false);
    expect(almonds.requiredText).toBe('15 g required');
    expect(almonds.availabilityDetail).toBe('Available: 3.75 kg');
    expect(almonds.statusLabel).toBe('In Pantry');
    expect(almonds.isReady).toBe(true);
  });

  it('scales required quantity when servings change', () => {
    const almondsDouble = formatIngredientDetailPresentation(badamMilkIngredients[1], 2, 4, false);
    expect(almondsDouble.requiredText).toBe('30 g required');
    expect(almondsDouble.availabilityDetail).toBe('Available: 3.75 kg');
  });

  it('updates availability on "I have this" and restores original state on Undo without mutating ingredients', () => {
    const before = computeRecipeAvailability(badamMilkIngredients, {});
    expect(before.recommendationType).toBe('COOK_WITH_SUBSTITUTIONS');
    expect(before.missingEssential).toEqual(['Turmeric Powder']);

    const turmericMissing = formatIngredientDetailPresentation(badamMilkIngredients[2], 2, 2, false);
    expect(turmericMissing.statusLabel).toBe('Missing');
    expect(turmericMissing.isReady).toBe(false);

    // User taps "I have this"
    const confirmedMap = { 'turmeric powder': true };
    const turmericConfirmed = formatIngredientDetailPresentation(badamMilkIngredients[2], 2, 2, true);
    expect(turmericConfirmed.statusLabel).toBe('Confirmed by you');
    expect(turmericConfirmed.availabilityDetail).toBe('Confirmed by you');
    expect(turmericConfirmed.isReady).toBe(true);

    const afterConfirm = computeRecipeAvailability(badamMilkIngredients, confirmedMap);
    expect(afterConfirm.recommendationType).toBe('COOK_NOW');
    expect(afterConfirm.missingEssential).toEqual([]);
    expect(afterConfirm.confirmedCount).toBe(1);

    // Optional missing ingredient (Saffron) does not block cooking
    expect(badamMilkIngredients[3].is_optional).toBe(true);

    // User taps "Undo" -> restores original missing state
    const afterUndo = computeRecipeAvailability(badamMilkIngredients, {});
    expect(afterUndo.missingEssential).toEqual(['Turmeric Powder']);
    expect(badamMilkIngredients[2].available_qty).toBeNull();
  });

  it('formats partial stock with both available and needed quantities', () => {
    const partialIng: RecipeDetailIngredient = {
      name: 'Sugar',
      quantity: '150',
      base_unit: 'g',
      available: false,
      partial: true,
      status: 'partial',
      available_qty: '100',
      missing_qty: '50',
      essential: true,
    };
    const res = formatIngredientDetailPresentation(partialIng, 2, 2, false);
    expect(res.requiredText).toBe('150 g required');
    expect(res.availabilityDetail).toBe('Available: 100 g · Need 50 g more');
    expect(res.statusLabel).toBe('Insufficient');
    expect(res.isReady).toBe(false);
  });
});
