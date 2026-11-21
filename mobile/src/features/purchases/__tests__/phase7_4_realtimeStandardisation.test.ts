import {
  findUnresolvedIngredientLines,
  getIngredientConfidenceLabel,
} from '../ingredientReviewHelpers';
import { computeLineTotalFromQtyAndRate } from '../purchaseFinancials';

describe('Phase 7.4 Real-time Ingredient Standardisation review helpers', () => {
  it('formats confidence labels for HIGH, MEDIUM, LOW, UNMATCHED, NON_PANTRY, and Approved', () => {
    expect(
      getIngredientConfidenceLabel({
        display_name: 'Toned Milk',
        track_inventory: true,
        budgetOnly: false,
        ingredient_confidence_tier: 'HIGH',
        ingredient_confidence_score: 96,
      }),
    ).toBe('High (96%)');

    expect(
      getIngredientConfidenceLabel({
        display_name: 'Atta',
        track_inventory: true,
        budgetOnly: false,
        ingredient_confidence_tier: 'MEDIUM',
        ingredient_confidence_score: 78,
      }),
    ).toBe('Verify (78%)');

    expect(
      getIngredientConfidenceLabel({
        display_name: 'Unknown Pack',
        track_inventory: true,
        budgetOnly: false,
        ingredient_confidence_tier: 'LOW',
      }),
    ).toBe('Low match');

    expect(
      getIngredientConfidenceLabel({
        display_name: 'Mystery Item',
        track_inventory: true,
        budgetOnly: false,
        ingredient_confidence_tier: 'UNMATCHED',
      }),
    ).toBe('Unmatched');

    expect(
      getIngredientConfidenceLabel({
        display_name: 'Mystery Item',
        track_inventory: true,
        budgetOnly: false,
        ingredient_confidence_tier: 'UNMATCHED',
        user_approved_ingredient: true,
      }),
    ).toBe('Approved');
  });

  it('blocks confirmation only for unapproved LOW/UNMATCHED tracked pantry items', () => {
    const lines = [
      {
        display_name: 'Toned Milk',
        raw_text: 'AMUL TAAZA TONED MILK 1L',
        track_inventory: true,
        budgetOnly: false,
        ingredient_confidence_tier: 'HIGH',
        user_approved_ingredient: false,
      },
      {
        display_name: 'Dish Soap',
        raw_text: 'VIM LIQUID',
        track_inventory: false,
        budgetOnly: true,
        ingredient_confidence_tier: 'UNMATCHED',
        user_approved_ingredient: false,
      },
      {
        display_name: 'XYZZZ MYSTERY',
        raw_text: 'XYZZZ MYSTERY',
        track_inventory: true,
        budgetOnly: false,
        ingredient_confidence_tier: 'UNMATCHED',
        user_approved_ingredient: false,
      },
    ];

    const unresolved = findUnresolvedIngredientLines(lines);
    expect(unresolved).toHaveLength(1);
    expect(unresolved[0].raw_text).toBe('XYZZZ MYSTERY');

    const afterApproval = lines.map((l) =>
      l.raw_text === 'XYZZZ MYSTERY' ? { ...l, user_approved_ingredient: true } : l,
    );
    expect(findUnresolvedIngredientLines(afterApproval)).toHaveLength(0);
  });

  it('does not wipe lineTotalRupeesText when qty changes and unit_price_paise is 0.00', () => {
    expect(computeLineTotalFromQtyAndRate('2', '0.00')).toBeNull();
    expect(computeLineTotalFromQtyAndRate('2', '0')).toBeNull();
    expect(computeLineTotalFromQtyAndRate('2', '136.00')).toBe('272.00');
  });
});
