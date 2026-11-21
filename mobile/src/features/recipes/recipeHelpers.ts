import {
  BaseUnit,
  MealHistoryItem,
  RecipeChatExternalLink,
  RecipeChatSuggestion,
  RecipeRecommendation,
  RecipeStep,
} from '../../shared/api/types';
import {
  convertCompatibleUnits,
  formatQuantityWithUnit,
} from '../../shared/format/units';

export interface RecipeDetailIngredient {
  name: string;
  canonical_item_id?: string | null;
  quantity: string;
  base_unit: BaseUnit | string;
  available: boolean;
  partial?: boolean;
  status?: 'available' | 'partial' | 'missing' | string;
  essential?: boolean;
  is_optional?: boolean;
  available_qty?: string | null;
  missing_qty?: string | null;
  unit_compatible?: boolean;
}

export interface RecipeDetailTarget {
  recipe_id?: string | null;
  title: string;
  description?: string | null;
  meal_type?: string | null;
  servings: number;
  prep_minutes?: number | null;
  cook_minutes?: number | null;
  difficulty?: string | null;
  source_type: 'ai_generated' | 'external_reference' | string;
  source_label: string;
  recommendation_type?: 'COOK_NOW' | 'COOK_WITH_SUBSTITUTIONS' | 'REQUIRES_SHOPPING' | string;
  ingredients: RecipeDetailIngredient[];
  instructions: string[];
  structured_steps?: RecipeStep[];
  quality_warnings?: string[];
  substitutions: string[];
  cooking_tips: string[];
  food_safety_notes: string[];
  uses_expiring_items: string[];
  external_links: RecipeChatExternalLink[];
}

export function recommendationToDetail(rec: RecipeRecommendation): RecipeDetailTarget {
  const ingredients: RecipeDetailIngredient[] = (rec.ingredients ?? []).map((ing) => {
    const isMissing = rec.missing_ingredients.some(
      (m) => m.toLowerCase() === ing.name.toLowerCase()
    );
    const ingStatus =
      ing.status ?? (ing.is_available === false || isMissing ? 'missing' : 'available');
    const isAvail = ingStatus === 'available';
    const isPartial = ingStatus === 'partial';
    return {
      name: ing.name,
      canonical_item_id: ing.canonical_item_id,
      quantity: ing.required_qty,
      base_unit: ing.base_unit,
      available: isAvail,
      partial: isPartial,
      status: ingStatus,
      essential: ing.essential ?? !ing.is_optional,
      is_optional: ing.is_optional ?? false,
      available_qty: ing.available_qty ?? null,
      missing_qty: ing.missing_qty ?? null,
      unit_compatible: ing.unit_compatible ?? true,
    };
  });

  const verifiedRefs = (rec.references ?? []).filter((r) => r.verified && r.url);
  return {
    recipe_id: rec.recipe_id,
    title: rec.title,
    description: rec.description ?? null,
    meal_type: rec.meal_type ?? null,
    servings: rec.servings || 2,
    prep_minutes: rec.prep_minutes,
    cook_minutes: rec.cook_minutes ?? null,
    difficulty: rec.difficulty ?? null,
    source_type: verifiedRefs.length > 0 ? 'external_reference' : (rec.source_type ?? 'ai_generated'),
    source_label:
      rec.source_label ||
      (verifiedRefs.length > 0
        ? `Externally referenced: ${verifiedRefs[0].source || verifiedRefs[0].title}`
        : 'AI-generated recipe'),
    recommendation_type: rec.recommendation_type ?? 'REQUIRES_SHOPPING',
    ingredients,
    instructions:
      rec.instructions && rec.instructions.length > 0
        ? rec.instructions
        : [
            `Prepare and measure ingredients for ${rec.title}.`,
            'Cook over medium heat until done and serve warm.',
          ],
    structured_steps: rec.structured_steps ?? [],
    quality_warnings: rec.quality_warnings ?? [],
    substitutions: rec.substitutions ?? [],
    cooking_tips: rec.cooking_tips ?? [],
    food_safety_notes: rec.food_safety_notes ?? [],
    uses_expiring_items: rec.uses_expiring_soon ? ['Expiring pantry items'] : [],
    external_links: verifiedRefs.map((r) => ({
      title: r.title,
      url: r.url,
      source: r.source ?? null,
      link_type: r.link_type ?? 'written_recipe',
      verified: r.verified,
    })),
  };
}

export function suggestionToDetail(sug: RecipeChatSuggestion): RecipeDetailTarget {
  const ingredients: RecipeDetailIngredient[] = [
    ...sug.available_ingredients.map((i) => ({ ...i, available: true })),
    ...(sug.partial_ingredients ?? []).map((i) => ({ ...i, available: true, partial: true })),
    ...sug.missing_ingredients.map((i) => ({ ...i, available: false })),
  ];
  const verifiedLinks = (sug.external_links ?? []).filter((l) => l.verified && l.url);

  return {
    recipe_id: sug.recipe_id ?? sug.id ?? null,
    title: sug.title,
    description: sug.summary ?? null,
    meal_type: sug.meal_type ?? null,
    servings: sug.servings || 2,
    prep_minutes: sug.prep_minutes ?? null,
    cook_minutes: sug.cook_minutes ?? null,
    difficulty: sug.difficulty ?? null,
    source_type: verifiedLinks.length > 0 ? 'external_reference' : (sug.source_type ?? 'ai_generated'),
    source_label:
      sug.source_label ||
      (verifiedLinks.length > 0
        ? `Externally referenced: ${verifiedLinks[0].source || verifiedLinks[0].title}`
        : 'AI-generated recipe'),
    recommendation_type: sug.recommendation_type ?? 'REQUIRES_SHOPPING',
    ingredients,
    instructions:
      sug.instructions && sug.instructions.length > 0
        ? sug.instructions
        : [
            `Prepare and measure ingredients for ${sug.title}.`,
            'Cook over medium heat until done and serve warm.',
          ],
    structured_steps: sug.structured_steps ?? [],
    quality_warnings: sug.quality_warnings ?? [],
    substitutions: sug.substitutions ?? [],
    cooking_tips: sug.cooking_tips ?? [],
    food_safety_notes: sug.food_safety_notes ?? [],
    uses_expiring_items: sug.uses_expiring_items ?? [],
    external_links: verifiedLinks,
  };
}

export function scaleQuantityText(
  qtyText: string,
  baseServings: number,
  targetServings: number
): string {
  const safeBase = baseServings > 0 ? baseServings : 2;
  const factor = targetServings / safeBase;
  if (Math.abs(factor - 1) < 0.001) {
    return qtyText;
  }
  const match = qtyText.match(/^(\d+(?:\.\d+)?)(.*)$/);
  if (!match) {
    return qtyText;
  }
  const num = Number(match[1]);
  if (!Number.isFinite(num)) {
    return qtyText;
  }
  const scaled = Math.round(num * factor * 100) / 100;
  const formatted = Number.isInteger(scaled) ? String(scaled) : String(scaled);
  return `${formatted}${match[2]}`;
}

export function detectStepMinutes(stepText: string): number | null {
  const m = stepText.match(/(\d+)\s*(?:-\s*\d+\s*)?(?:minutes?|mins?|min)\b/i);
  if (!m) {
    return null;
  }
  const mins = Number(m[1]);
  return Number.isFinite(mins) && mins > 0 && mins <= 240 ? mins : null;
}

const MEAL_TYPE_ORDER: Record<string, number> = {
  breakfast: 0,
  lunch: 1,
  snacks: 2,
  snack: 2,
  dinner: 3,
  other: 4,
};

export interface MealHistoryDateGroup {
  dateKey: string;
  dateLabel: string;
  meals: MealHistoryItem[];
}

export function groupMealHistoryByDate(items: MealHistoryItem[]): MealHistoryDateGroup[] {
  const groups = new Map<string, MealHistoryItem[]>();
  const sorted = [...items].sort(
    (a, b) => new Date(b.cooked_at).getTime() - new Date(a.cooked_at).getTime()
  );

  for (const item of sorted) {
    const dt = new Date(item.cooked_at);
    const key = Number.isNaN(dt.getTime())
      ? 'unknown'
      : `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
    const list = groups.get(key) ?? [];
    list.push(item);
    groups.set(key, list);
  }

  const today = new Date();
  const yesterday = new Date(today.getTime() - 86400000);
  const fmtKey = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const todayKey = fmtKey(today);
  const yestKey = fmtKey(yesterday);

  const out: MealHistoryDateGroup[] = [];
  for (const [dateKey, meals] of groups.entries()) {
    meals.sort((a, b) => {
      const oa = MEAL_TYPE_ORDER[(a.meal_type || 'other').toLowerCase()] ?? 4;
      const ob = MEAL_TYPE_ORDER[(b.meal_type || 'other').toLowerCase()] ?? 4;
      if (oa !== ob) {
        return oa - ob;
      }
      return new Date(a.cooked_at).getTime() - new Date(b.cooked_at).getTime();
    });

    let dateLabel = dateKey;
    if (dateKey === todayKey) {
      dateLabel = 'Today';
    } else if (dateKey === yestKey) {
      dateLabel = 'Yesterday';
    } else if (dateKey !== 'unknown') {
      const parsed = new Date(`${dateKey}T12:00:00`);
      dateLabel = parsed.toLocaleDateString('en-IN', {
        day: 'numeric',
        month: 'long',
      });
    }
    out.push({ dateKey, dateLabel, meals });
  }
  return out;
}

export function buildSelectionOnlyPrompt(params: {
  customText: string;
  preferences: string[];
  hungerLevel: string | null;
  maxMinutes: number | null;
  servings: number;
}): string {
  const trimmed = params.customText.trim();
  if (trimmed) {
    return trimmed;
  }
  const parts: string[] = [];
  if (params.preferences.length > 0) {
    parts.push(params.preferences.join(', '));
  }
  if (params.hungerLevel) {
    parts.push(`for ${params.hungerLevel.toLowerCase()}`);
  }
  if (params.maxMinutes) {
    parts.push(`within ${params.maxMinutes} minutes`);
  }
  if (parts.length === 0) {
    return `What can I cook right now for ${params.servings} servings?`;
  }
  return `Suggest a recipe (${parts.join(', ')}) for ${params.servings} servings using my pantry.`;
}

export function buildCompleteLineId(
  planId: string,
  item: { line_id?: string; id?: string; canonical_item_id: string },
  idx: number
): string {
  return item.line_id ?? item.id ?? `${planId}:${item.canonical_item_id}:${idx}`;
}

export interface DeductionLineInput {
  canonical_item_id: string;
  base_unit?: string;
  lotBaseUnit?: string;
  actualUsedText: string;
  wasteQtyText: string;
  omitted: boolean;
}

export function normalizeUnitToken(unit?: string | null): string {
  const u = (unit ?? '').trim().toLowerCase();
  if (u === 'gm' || u === 'gms' || u === 'gram' || u === 'grams') return 'g';
  if (u === 'kgs' || u === 'kilogram' || u === 'kilograms') return 'kg';
  if (u === 'ltr' || u === 'litre' || u === 'litres' || u === 'liter' || u === 'liters') return 'l';
  if (u === 'pc' || u === 'piece' || u === 'pieces') return 'pcs';
  return u || 'pcs';
}

export function getRecommendationStatusLabel(recType?: string | null): string {
  if (recType === 'COOK_NOW') return 'Ready to cook';
  if (recType === 'COOK_WITH_SUBSTITUTIONS') return 'Almost ready';
  return 'Requires shopping';
}

export interface RecipeAvailabilitySummary {
  recommendationType: 'COOK_NOW' | 'COOK_WITH_SUBSTITUTIONS' | 'REQUIRES_SHOPPING';
  statusLabel: string;
  missingEssential: string[];
  insufficientEssential: string[];
  confirmedCount: number;
}

export function computeRecipeAvailability(
  ingredients: RecipeDetailIngredient[],
  confirmedKeys: Record<string, boolean> = {}
): RecipeAvailabilitySummary {
  const essential = ingredients.filter((i) => i.essential !== false && !i.is_optional);
  const missingEssential: string[] = [];
  const insufficientEssential: string[] = [];
  let confirmedCount = 0;

  for (const ing of essential) {
    const key = ing.name.trim().toLowerCase();
    const isUserConfirmed = Boolean(confirmedKeys[key]);
    if (isUserConfirmed) {
      confirmedCount += 1;
      continue;
    }
    const status = ing.status ?? (ing.partial ? 'partial' : ing.available ? 'available' : 'missing');
    if (status === 'partial') {
      insufficientEssential.push(ing.name);
    } else if (status !== 'available') {
      missingEssential.push(ing.name);
    }
  }

  const unresolvedCount = missingEssential.length + insufficientEssential.length;
  let recommendationType: 'COOK_NOW' | 'COOK_WITH_SUBSTITUTIONS' | 'REQUIRES_SHOPPING' = 'REQUIRES_SHOPPING';
  if (essential.length > 0 && unresolvedCount === 0) {
    recommendationType = 'COOK_NOW';
  } else if (essential.length >= 2 && unresolvedCount === 1) {
    recommendationType = 'COOK_WITH_SUBSTITUTIONS';
  }

  return {
    recommendationType,
    statusLabel: getRecommendationStatusLabel(recommendationType),
    missingEssential,
    insufficientEssential,
    confirmedCount,
  };
}

export function areUnitsCompatible(unitA?: string | null, unitB?: string | null): boolean {
  return convertCompatibleUnits(1, unitA, unitB) !== null;
}

export function convertCompatibleQty(
  qty: number,
  fromUnit?: string | null,
  toUnit?: string | null
): number | null {
  return convertCompatibleUnits(qty, fromUnit, toUnit);
}

export function computeCombinedDeductionByCanonical(
  lines: DeductionLineInput[]
): Record<string, number> {
  const totals: Record<string, number> = {};
  for (const l of lines) {
    if (l.omitted) continue;
    if (l.base_unit && l.lotBaseUnit && !areUnitsCompatible(l.base_unit, l.lotBaseUnit)) {
      continue;
    }
    const used = Math.max(0, Number(l.actualUsedText || '0') || 0);
    const waste = Math.max(0, Number(l.wasteQtyText || '0') || 0);
    const rawTotal = used + waste;
    const converted =
      l.base_unit && l.lotBaseUnit
        ? convertCompatibleQty(rawTotal, l.base_unit, l.lotBaseUnit) ?? rawTotal
        : rawTotal;
    totals[l.canonical_item_id] = (totals[l.canonical_item_id] ?? 0) + converted;
  }
  return totals;
}

export interface IngredientDetailPresentation {
  requiredText: string;
  availabilityDetail: string;
  statusLabel: 'Confirmed by you' | 'In Pantry' | 'Insufficient' | 'Missing';
  isReady: boolean;
}

export function formatIngredientDetailPresentation(
  ing: RecipeDetailIngredient,
  baseServings: number,
  targetServings: number,
  userConfirmed: boolean,
): IngredientDetailPresentation {
  const scaled = scaleQuantityText(ing.quantity, baseServings || 2, targetServings);
  const scaledNum = Number(scaled);
  const requiredFormatted = Number.isFinite(scaledNum)
    ? formatQuantityWithUnit(scaledNum, ing.base_unit, { autoScale: true })
    : `${scaled} ${ing.base_unit}`.trim();
  const requiredText = `${requiredFormatted} required`;

  const status =
    ing.status ?? (ing.partial ? 'partial' : ing.available ? 'available' : 'missing');

  if (userConfirmed) {
    return {
      requiredText,
      availabilityDetail: 'Confirmed by you',
      statusLabel: 'Confirmed by you',
      isReady: true,
    };
  }

  const availNum = ing.available_qty != null ? Number(ing.available_qty) : NaN;
  const availFormatted =
    ing.available_qty != null && Number.isFinite(availNum)
      ? formatQuantityWithUnit(availNum, ing.base_unit, { autoScale: true })
      : null;

  if (status === 'available') {
    return {
      requiredText,
      availabilityDetail: availFormatted ? `Available: ${availFormatted}` : 'In pantry',
      statusLabel: 'In Pantry',
      isReady: true,
    };
  }

  if (status === 'partial') {
    const missNum = ing.missing_qty != null ? Number(ing.missing_qty) : NaN;
    const missFormatted =
      ing.missing_qty != null && Number.isFinite(missNum)
        ? formatQuantityWithUnit(missNum, ing.base_unit, { autoScale: true })
        : null;
    const parts = [
      availFormatted ? `Available: ${availFormatted}` : 'Partial stock',
      missFormatted ? `Need ${missFormatted} more` : null,
    ].filter(Boolean);
    return {
      requiredText,
      availabilityDetail: parts.join(' · '),
      statusLabel: 'Insufficient',
      isReady: false,
    };
  }

  return {
    requiredText,
    availabilityDetail: 'Not in pantry',
    statusLabel: 'Missing',
    isReady: false,
  };
}

export interface CookingStepDisplay {
  stepNumber: number;
  totalSteps: number;
  title: string;
  instruction: string;
  ingredientsUsed: string[];
  durationMinutes: number | null;
  heatLevel: string | null;
  technique: string | null;
  completionCue: string | null;
}

export function computeCookingProgress(stepIndex: number, totalSteps: number): number {
  if (totalSteps <= 0) return 0;
  const clamped = Math.max(0, Math.min(totalSteps - 1, stepIndex));
  return Math.round(((clamped + 1) / totalSteps) * 100);
}

export function stepCookingIndex(
  currentIndex: number,
  delta: number,
  totalSteps: number,
): number {
  if (totalSteps <= 0) return 0;
  return Math.max(0, Math.min(totalSteps - 1, currentIndex + delta));
}

export function resolveCookingStep(
  detail: RecipeDetailTarget,
  stepIndex: number,
): CookingStepDisplay {
  const structured = detail.structured_steps ?? [];
  const plain = detail.instructions.length > 0
    ? detail.instructions
    : ['Prepare and cook ingredients.'];
  const totalSteps = structured.length > 0 ? structured.length : plain.length;
  const safeIdx = Math.max(0, Math.min(totalSteps - 1, stepIndex));

  if (structured.length > 0) {
    const st = structured[safeIdx];
    return {
      stepNumber: st.step_number || safeIdx + 1,
      totalSteps,
      title: st.title || `Step ${safeIdx + 1}`,
      instruction: st.instruction,
      ingredientsUsed: st.ingredients_used ?? [],
      durationMinutes: st.duration_minutes ?? detectStepMinutes(st.instruction),
      heatLevel: st.heat_level ?? null,
      technique: st.technique ?? null,
      completionCue: st.completion_cue || null,
    };
  }

  const text = plain[safeIdx];
  return {
    stepNumber: safeIdx + 1,
    totalSteps,
    title: `Step ${safeIdx + 1}`,
    instruction: text,
    ingredientsUsed: [],
    durationMinutes: detectStepMinutes(text),
    heatLevel: null,
    technique: null,
    completionCue: null,
  };
}
