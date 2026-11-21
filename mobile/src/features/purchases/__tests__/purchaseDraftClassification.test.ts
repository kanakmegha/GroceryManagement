import {
  buildConfirmOutsideFoodPayload,
  type ClassificationLineState,
  deriveInitialBudgetOnly,
  isExplicitOutsideFoodLine,
  toggleBudgetOnlyForLine,
} from '../purchaseDraftClassification';

describe('Purchase review outside-food classification & defaults', () => {
  it('1. Grocery receipt with all pantry items hides outside-food UI and sends empty confirm body', () => {
    const lines: ClassificationLineState[] = [
      { localKey: 'l1', track_inventory: true, budgetOnly: false, classification_status: 'pantry_eligible' },
      { localKey: 'l2', track_inventory: true, budgetOnly: false, classification_status: 'pantry_eligible' },
    ];
    expect(lines.some(isExplicitOutsideFoodLine)).toBe(false);
    expect(buildConfirmOutsideFoodPayload(lines, null, null, '2026-10-08', '')).toEqual({
      ok: true,
      body: {},
    });
  });

  it('2. Grocery receipt with an ambiguous Needs Review item does NOT trigger outside-food UI or defaults', () => {
    const budgetOnly = deriveInitialBudgetOnly({
      track_inventory: false,
      classification_status: 'ambiguous_review',
      budget_treatment: 'requires_user_review',
    });
    expect(budgetOnly).toBe(false);

    const lines: ClassificationLineState[] = [
      { localKey: 'l1', track_inventory: true, budgetOnly: false, classification_status: 'pantry_eligible' },
      {
        localKey: 'l2',
        track_inventory: false,
        budgetOnly,
        classification_status: 'ambiguous_review',
        budget_treatment: 'requires_user_review',
      },
    ];
    expect(lines.some(isExplicitOutsideFoodLine)).toBe(false);
    expect(buildConfirmOutsideFoodPayload(lines, null, null, '2026-10-08', '')).toEqual({
      ok: true,
      body: {},
    });
  });

  it('3. Selecting Budget Only on one line leaves other lines independent and requires explicit meal/dining selection', () => {
    const initial: ClassificationLineState[] = [
      { localKey: 'l1', track_inventory: true, budgetOnly: false, classification_status: 'pantry_eligible' },
      { localKey: 'l2', track_inventory: true, budgetOnly: false, classification_status: 'pantry_eligible' },
    ];
    const updated = toggleBudgetOnlyForLine(initial, 'l2', true);
    expect(updated[0]).toEqual(initial[0]);
    expect(updated[1].budgetOnly).toBe(true);
    expect(updated[1].track_inventory).toBe(false);
    expect(updated[1].budget_treatment).toBe('outside_food_expenditure');
    expect(updated.some(isExplicitOutsideFoodLine)).toBe(true);

    // Confirming without explicit mealType/diningMode must fail instead of defaulting to dinner/restaurant
    const missingSelection = buildConfirmOutsideFoodPayload(updated, null, null, '2026-10-08', '');
    expect(missingSelection.ok).toBe(false);
  });

  it('4. Unchecking Budget Only clears outside-food metadata and disables meal payload', () => {
    const initialLines: ClassificationLineState[] = [
      { localKey: 'l1', track_inventory: true, budgetOnly: false, classification_status: 'pantry_eligible' },
    ];
    const checked = toggleBudgetOnlyForLine(initialLines, 'l1', true);
    const unchecked = toggleBudgetOnlyForLine(checked, 'l1', false);
    expect(unchecked[0].budgetOnly).toBe(false);
    expect(unchecked[0].track_inventory).toBe(true);
    expect(unchecked[0].budget_treatment).toBe('grocery_inventory');
    expect(unchecked.some(isExplicitOutsideFoodLine)).toBe(false);
    expect(buildConfirmOutsideFoodPayload(unchecked, null, null, '2026-10-08', '')).toEqual({
      ok: true,
      body: {},
    });
  });

  it('5. Reopening saved drafts preserves Needs Review vs Prepared Meal accurately', () => {
    expect(
      deriveInitialBudgetOnly({
        classification_status: 'prepared_meal',
        budget_treatment: 'outside_food_expenditure',
        food_state: 'cooked',
        track_inventory: false,
      })
    ).toBe(true);
    expect(
      deriveInitialBudgetOnly({
        classification_status: 'non_food',
        budget_treatment: 'household_non_food',
        track_inventory: false,
      })
    ).toBe(false);
  });

  it('6 & 7. Restaurant receipt with explicit meal details confirms with chosen mealType and diningMode', () => {
    const restLines: ClassificationLineState[] = [
      {
        localKey: 'r1',
        track_inventory: false,
        budgetOnly: true,
        classification_status: 'prepared_meal',
        budget_treatment: 'outside_food_expenditure',
      },
    ];
    const res = buildConfirmOutsideFoodPayload(restLines, 'lunch', 'takeaway', '2026-10-08', '2');
    expect(res).toEqual({
      ok: true,
      body: {
        meal_type: 'lunch',
        dining_mode: 'takeaway',
        consumed_on: '2026-10-08',
        people_count: 2,
      },
    });
  });
});
