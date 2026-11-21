import type { DiningMode, MealType, PurchaseDraftItem } from '../../shared/api/types';

export interface ClassificationLineState {
  localKey: string;
  track_inventory: boolean;
  budgetOnly: boolean;
  classification_status?: string;
  food_state?: string | null;
  budget_treatment?: string | null;
}

/**
 * Determine whether an incoming draft item is explicitly outside food (prepared meal / restaurant).
 * Needs Review (ambiguous_review) and non-food items MUST return false.
 */
export function deriveInitialBudgetOnly(item: Partial<PurchaseDraftItem>): boolean {
  if (item.classification_status === 'non_food' || item.classification_status === 'ambiguous_review') {
    return false;
  }
  return (
    item.budget_treatment === 'outside_food_expenditure' ||
    item.food_state === 'cooked' ||
    item.classification_status === 'prepared_meal'
  );
}

/**
 * Only explicitly Budget-Only (non-nonfood) lines trigger outside-food meal tracking.
 * Ambiguous "Needs Review" lines with track_inventory=false and budgetOnly=false do NOT.
 */
export function isExplicitOutsideFoodLine(line: ClassificationLineState): boolean {
  return line.budgetOnly === true && line.classification_status !== 'non_food';
}

export function toggleBudgetOnlyForLine<T extends ClassificationLineState>(
  lines: T[],
  localKey: string,
  nextBudgetOnly: boolean
): T[] {
  return lines.map((line) => {
    if (line.localKey !== localKey) {
      return line;
    }
    if (nextBudgetOnly) {
      return {
        ...line,
        budgetOnly: true,
        track_inventory: false,
        classification_status: 'prepared_meal',
        food_state: 'cooked',
        budget_treatment: 'outside_food_expenditure',
      };
    }
    return {
      ...line,
      budgetOnly: false,
      track_inventory: line.classification_status === 'non_food' ? false : true,
      classification_status:
        line.classification_status === 'prepared_meal'
          ? 'pantry_eligible'
          : line.classification_status,
      food_state: line.food_state === 'cooked' ? 'raw' : line.food_state,
      budget_treatment:
        line.budget_treatment === 'outside_food_expenditure'
          ? 'grocery_inventory'
          : line.budget_treatment,
    };
  });
}

export type ConfirmOutsideFoodResult =
  | { ok: true; body: Record<string, unknown> }
  | { ok: false; error: string };

export function buildConfirmOutsideFoodPayload(
  lines: ClassificationLineState[],
  mealType: MealType | null,
  diningMode: DiningMode | null,
  consumedOn: string,
  peopleCountText: string
): ConfirmOutsideFoodResult {
  const hasOutsideFood = lines.some(isExplicitOutsideFoodLine);
  if (!hasOutsideFood) {
    return { ok: true, body: {} };
  }
  if (!mealType || !diningMode) {
    return {
      ok: false,
      error: 'Select a meal type and dining mode for the outside-food items before confirming.',
    };
  }
  const peopleParsed = Number.parseInt(peopleCountText.trim(), 10);
  return {
    ok: true,
    body: {
      meal_type: mealType,
      dining_mode: diningMode,
      consumed_on: consumedOn,
      people_count: Number.isFinite(peopleParsed) && peopleParsed > 0 ? peopleParsed : null,
    },
  };
}
