import {
  buildSessionAvailabilityRecord,
  evaluateReportedAvailability,
  getCompatibleUnitsForUnit,
  reconcilePostCookingConsumption,
} from '../pantryReconciliation';
import {
  computeCombinedDeductionByCanonical,
  isCompletePlanLineUnresolved,
  reconcileCompletePlanLines,
  resolveCookingStep,
  scaleQuantityText,
  type RecipeDetailTarget,
} from '../recipeHelpers';

describe('pantryReconciliation', () => {
  it('returns dimension-appropriate units without cross-dimensional mixing', () => {
    expect(getCompatibleUnitsForUnit('g')).toEqual(['g', 'kg']);
    expect(getCompatibleUnitsForUnit('ml')).toEqual(['ml', 'l']);
    expect(getCompatibleUnitsForUnit('pcs')).toEqual(['pcs', 'dozen']);
  });

  it('evaluates sufficient reported quantity with compatible unit conversion', () => {
    const res = evaluateReportedAvailability({
      reportedQty: 1,
      reportedUnit: 'kg',
      requiredQty: 400,
      requiredUnit: 'g',
    });
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.status).toBe('sufficient');
      expect(res.availableInRequiredUnit).toBe(1000);
      expect(res.remainingAfterRecipe).toBe(600);
      expect(res.shortageQty).toBe(0);
    }
  });

  it('identifies insufficient reported quantity and remaining shortage', () => {
    const res = evaluateReportedAvailability({
      reportedQty: 200,
      reportedUnit: 'g',
      requiredQty: 500,
      requiredUnit: 'g',
    });
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.status).toBe('insufficient');
      expect(res.shortageQty).toBe(300);
      expect(res.remainingAfterRecipe).toBe(0);
    }
  });

  it('rejects non-positive quantities and cross-dimensional unit mismatches', () => {
    expect(
      evaluateReportedAvailability({
        reportedQty: 0,
        reportedUnit: 'g',
        requiredQty: 100,
        requiredUnit: 'g',
      }).ok
    ).toBe(false);

    const mismatch = evaluateReportedAvailability({
      reportedQty: 2,
      reportedUnit: 'pcs',
      requiredQty: 200,
      requiredUnit: 'g',
    });
    expect(mismatch.ok).toBe(false);
  });

  it('builds session-reported records without persisting to pantry by default', () => {
    const rec = buildSessionAvailabilityRecord({
      key: 'ing-1',
      name: 'Paneer',
      canonical_item_id: 'cid-1',
      reportedQty: 250,
      reportedUnit: 'g',
      requiredQty: 200,
      requiredUnit: 'g',
      persistedToPantry: false,
      note: 'Half pack in fridge',
    });
    expect(rec.status).toBe('sufficient');
    expect(rec.persistedToPantry).toBe(false);
    expect(rec.remainingAfterRecipe).toBe(50);

    const skipped = buildSessionAvailabilityRecord({
      key: 'ing-2',
      name: 'Curry Leaves',
      canonical_item_id: null,
      reportedQty: 0,
      reportedUnit: 'pcs',
      requiredQty: 8,
      requiredUnit: 'pcs',
      persistedToPantry: false,
      mode: 'skipped',
    });
    expect(skipped.status).toBe('skipped');
    expect(skipped.shortageQty).toBe(8);
  });

  it('reconciles post-cooking consumption without double deduction or negative stock', () => {
    // Session-only reported: confirmed pantry = 0 -> pantry deduction must be 0
    const sessionOnly = reconcilePostCookingConsumption({
      confirmedPantryQty: 0,
      sessionReportedQty: 250,
      actualUsedQty: 200,
      persistedToPantry: false,
    });
    expect(sessionOnly.pantryDeductionQty).toBe(0);
    expect(sessionOnly.sessionConsumedQty).toBe(200);
    expect(sessionOnly.remainingSessionReported).toBe(50);
    expect(sessionOnly.exceedsKnownAvailable).toBe(false);

    // Persisted to pantry: confirmed pantry already includes reported -> no double count
    const persisted = reconcilePostCookingConsumption({
      confirmedPantryQty: 250,
      sessionReportedQty: 250,
      actualUsedQty: 200,
      persistedToPantry: true,
    });
    expect(persisted.pantryDeductionQty).toBe(200);
    expect(persisted.remainingConfirmedPantry).toBe(50);
    expect(persisted.sessionConsumedQty).toBe(0);

    // Actual > known available: caps pantry deduction and emits warning
    const overUsed = reconcilePostCookingConsumption({
      confirmedPantryQty: 100,
      sessionReportedQty: 50,
      actualUsedQty: 220,
      persistedToPantry: false,
    });
    expect(overUsed.pantryDeductionQty).toBe(100);
    expect(overUsed.remainingConfirmedPantry).toBe(0);
    expect(overUsed.exceedsKnownAvailable).toBe(true);
    expect(overUsed.warning).toContain('capped at 100');
  });

  it('scales step ingredient quantities and preserves equipment, caution, and nextAction', () => {
    const detail: RecipeDetailTarget = {
      title: 'Paneer Stir-Fry',
      servings: 2,
      source_type: 'ai_generated',
      source_label: 'AI-generated recipe',
      ingredients: [],
      instructions: [],
      structured_steps: [
        {
          step_number: 1,
          title: 'Sear Paneer',
          instruction: 'Heat oil and sear paneer cubes until golden.',
          ingredients_used: ['paneer', 'olive oil'],
          ingredient_quantities: { paneer: '200 g', 'olive oil': '1 tbsp' },
          heat_level: 'medium',
          duration_minutes: 4,
          equipment: ['wide skillet'],
          completion_cue: 'Paneer edges are golden brown.',
          caution: 'Do not overcrowd the pan.',
          next_action: 'Add spinach next.',
        },
      ],
      substitutions: [],
      cooking_tips: [],
      food_safety_notes: [],
      uses_expiring_items: [],
      external_links: [],
    };

    const step4Servings = resolveCookingStep(detail, 0, 4);
    expect(step4Servings.ingredientQuantities.paneer).toBe('400 g');
    expect(step4Servings.ingredientQuantities['olive oil']).toBe('2 tbsp');
    expect(step4Servings.equipment).toEqual(['wide skillet']);
    expect(step4Servings.caution).toBe('Do not overcrowd the pan.');
    expect(step4Servings.nextAction).toBe('Add spinach next.');

    // Fractions and ranges scale accurately too
    expect(scaleQuantityText('1/2 tsp', 2, 4)).toBe('1 tsp');
    expect(scaleQuantityText('90-100 ml', 2, 4)).toBe('180-200 ml');
    expect(scaleQuantityText('150 g', 2, 3)).toBe('225 g');
  });

  it('resolves Complete Meal unit mismatches via session-only I Have It without converting incompatible pantry lots', () => {
    const planItems = [
      {
        line_id: 'plan-1:banana:0',
        canonical_item_id: 'cid-banana',
        name: 'Organic Bananas',
        planned_qty: '2',
        base_unit: 'pcs' as const,
      },
      {
        line_id: 'plan-1:choc:1',
        canonical_item_id: 'cid-choc',
        name: 'Amul Milk Chocolate',
        planned_qty: '100',
        base_unit: 'g' as const,
      },
      {
        line_id: 'plan-1:milk:2',
        canonical_item_id: 'cid-milk',
        name: 'Milk',
        planned_qty: '200',
        base_unit: 'ml' as const,
      },
      {
        line_id: 'plan-1:oats:3',
        canonical_item_id: 'cid-oats',
        name: 'Oats',
        planned_qty: '100',
        base_unit: 'g' as const,
      },
    ];
    const activeLots = [
      {
        id: 'lot-banana',
        canonical_item_id: 'cid-banana',
        item_name: 'Organic Bananas',
        status: 'active',
        remaining_qty: '1.2',
        base_unit: 'kg' as const,
      },
      {
        id: 'lot-choc',
        canonical_item_id: 'cid-choc',
        item_name: 'Amul Milk Chocolate',
        status: 'active',
        remaining_qty: '500',
        base_unit: 'ml' as const,
      },
      {
        id: 'lot-milk',
        canonical_item_id: 'cid-milk',
        item_name: 'Milk',
        status: 'active',
        remaining_qty: '1',
        base_unit: 'kg' as const,
      },
      {
        id: 'lot-oats',
        canonical_item_id: 'cid-oats',
        item_name: 'Oats',
        status: 'active',
        remaining_qty: '1',
        base_unit: 'kg' as const,
      },
    ];

    // 1. Initial state: compatible Oats (1 kg -> 1000 g) is resolved; 3 mismatched lines are unresolved
    const initial = reconcileCompletePlanLines({
      planId: 'plan-1',
      planItems,
      activeLots,
      todayIso: '2026-11-24',
    });
    expect(initial[3].unitCompatible).toBe(true);
    expect(initial[3].availableQty).toBe(1000);
    const initialCombined = computeCombinedDeductionByCanonical(initial);
    expect(initial.filter((l) => isCompletePlanLineUnresolved(l, initialCombined))).toHaveLength(3);

    // 2. User confirms 2 pcs Bananas and 100 g Chocolate session-only -> only Milk remains unresolved
    const partialSession = {
      'plan-1:banana:0': buildSessionAvailabilityRecord({
        key: 'plan-1:banana:0',
        name: 'Organic Bananas',
        canonical_item_id: 'cid-banana',
        reportedQty: 2,
        reportedUnit: 'pcs',
        requiredQty: 2,
        requiredUnit: 'pcs',
        persistedToPantry: false,
      }),
      'plan-1:choc:1': buildSessionAvailabilityRecord({
        key: 'plan-1:choc:1',
        name: 'Amul Milk Chocolate',
        canonical_item_id: 'cid-choc',
        reportedQty: 100,
        reportedUnit: 'g',
        requiredQty: 100,
        requiredUnit: 'g',
        persistedToPantry: false,
      }),
    };
    const afterTwo = reconcileCompletePlanLines({
      planId: 'plan-1',
      planItems,
      activeLots,
      sessionReportedAvailability: partialSession,
      existingLines: initial,
      todayIso: '2026-11-24',
    });
    expect(afterTwo[0].sessionConfirmed).toBe(true);
    expect(afterTwo[0].sessionConfirmedQty).toBe(2);
    expect(afterTwo[0].sessionConfirmedUnit).toBe('pcs');
    expect(afterTwo[0].unitCompatible).toBe(false); // incompatible 1.2 kg lot not falsely marked compatible
    expect(afterTwo[0].actualUsedText).toBe('0'); // 0 deducted from pantry
    const afterTwoCombined = computeCombinedDeductionByCanonical(afterTwo);
    expect(afterTwo.filter((l) => isCompletePlanLineUnresolved(l, afterTwoCombined))).toHaveLength(1);

    // 3. User confirms 200 ml Milk -> all 4 lines resolved -> Confirm button enabled (0 unresolved)
    const fullSession = {
      ...partialSession,
      'plan-1:milk:2': buildSessionAvailabilityRecord({
        key: 'plan-1:milk:2',
        name: 'Milk',
        canonical_item_id: 'cid-milk',
        reportedQty: 200,
        reportedUnit: 'ml',
        requiredQty: 200,
        requiredUnit: 'ml',
        persistedToPantry: false,
      }),
    };
    const afterAll = reconcileCompletePlanLines({
      planId: 'plan-1',
      planItems,
      activeLots,
      sessionReportedAvailability: fullSession,
      existingLines: afterTwo,
      todayIso: '2026-11-24',
    });
    const afterAllCombined = computeCombinedDeductionByCanonical(afterAll);
    expect(afterAll.filter((l) => isCompletePlanLineUnresolved(l, afterAllCombined))).toHaveLength(0);
    // Compatible Oats lot (in kg) deducts 0.1 kg (= 100 g)
    expect(afterAllCombined['cid-oats']).toBeCloseTo(0.1, 6);
  });
});
