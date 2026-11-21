import {
  computeRecipeAvailability,
  getRecommendationStatusLabel,
} from '../recipeHelpers';
import {
  convertCompatibleUnits,
  getUnitCategory,
  suggestProductUnits,
} from '../../../shared/format/units';

describe('Phase 6.2.1 — Recipe Availability & User Confirmation', () => {
  it('marks Ready to cook only when all essential ingredients are sufficient', () => {
    const res = computeRecipeAvailability([
      { name: 'Rice', quantity: '200', base_unit: 'g', available: true, status: 'available', essential: true },
      { name: 'Dal', quantity: '100', base_unit: 'g', available: true, status: 'available', essential: true },
      { name: 'Coriander', quantity: '5', base_unit: 'g', available: false, status: 'missing', essential: false, is_optional: true },
    ]);
    expect(res.recommendationType).toBe('COOK_NOW');
    expect(res.statusLabel).toBe('Ready to cook');
    expect(res.missingEssential).toEqual([]);
    expect(res.insufficientEssential).toEqual([]);
  });

  it('distinguishes missing vs insufficient and supports temporary reversible "I have this" confirmation', () => {
    const ings = [
      { name: 'Rice', quantity: '200', base_unit: 'g', available: true, status: 'available', essential: true },
      { name: 'Ghee', quantity: '20', base_unit: 'ml', available: false, status: 'partial', essential: true },
      { name: 'Cumin', quantity: '5', base_unit: 'g', available: false, status: 'missing', essential: true },
    ];

    const initial = computeRecipeAvailability(ings, {});
    expect(initial.recommendationType).toBe('REQUIRES_SHOPPING');
    expect(initial.insufficientEssential).toEqual(['Ghee']);
    expect(initial.missingEssential).toEqual(['Cumin']);

    const oneConfirmed = computeRecipeAvailability(ings, { cumin: true });
    expect(oneConfirmed.recommendationType).toBe('COOK_WITH_SUBSTITUTIONS');
    expect(oneConfirmed.statusLabel).toBe('Almost ready');
    expect(oneConfirmed.confirmedCount).toBe(1);

    const bothConfirmed = computeRecipeAvailability(ings, { cumin: true, ghee: true });
    expect(bothConfirmed.recommendationType).toBe('COOK_NOW');
    expect(bothConfirmed.statusLabel).toBe('Ready to cook');

    // Reversing confirmation restores original status
    const reversed = computeRecipeAvailability(ings, { cumin: false, ghee: false });
    expect(reversed.recommendationType).toBe('REQUIRES_SHOPPING');
    expect(getRecommendationStatusLabel('COOK_NOW')).toBe('Ready to cook');
    expect(getRecommendationStatusLabel('COOK_WITH_SUBSTITUTIONS')).toBe('Almost ready');
  });
});

describe('Phase 6.2.2 — Product-Aware Units & Safe Conversions', () => {
  it('converts within compatible dimensions and rejects cross-dimension conversions', () => {
    expect(convertCompatibleUnits(1, 'kg', 'g')).toBe(1000);
    expect(convertCompatibleUnits(1, 'L', 'ml')).toBe(1000);
    expect(convertCompatibleUnits(2, 'dozen', 'pcs')).toBe(24);
    expect(convertCompatibleUnits(12, 'pcs', 'dozen')).toBe(1);

    expect(convertCompatibleUnits(1, 'pcs', 'g')).toBeNull();
    expect(convertCompatibleUnits(1, 'bottle', 'ml')).toBeNull();
    expect(convertCompatibleUnits(1, 'pack', 'g')).toBeNull();
  });

  it('suggests product-aware grouped units across Weight, Volume, Count, and Packaging', () => {
    expect(getUnitCategory('pack')).toBe('packaging');
    expect(getUnitCategory('dozen')).toBe('count');
    expect(getUnitCategory('L')).toBe('volume');
    expect(getUnitCategory('kg')).toBe('weight');

    const milkGroups = suggestProductUnits('Milk', 'L');
    expect(milkGroups.map((g) => g.category)).toEqual(['volume']);
    expect(milkGroups[0].units).toEqual(['ml', 'L']);

    const eggGroups = suggestProductUnits('Eggs', 'pcs');
    expect(eggGroups[0].units).toEqual(['pcs', 'dozen']);

    const biscuitGroups = suggestProductUnits('Biscuits', 'pack');
    expect(biscuitGroups.flatMap((g) => g.units)).toEqual(['pcs', 'pack', 'packet']);

    const juiceGroups = suggestProductUnits('Orange Juice', 'bottle');
    expect(juiceGroups.map((g) => g.category)).toEqual(['volume', 'packaging']);
  });
});
