import {
  AiProviderStatus,
  BaseUnit,
  MealHistoryItem,
  RecipeChatExternalLink,
  RecipeChatRequest,
  RecipeChatResponse,
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
  equipment_required?: string[];
  preparation_steps?: string[];
  instructions: string[];
  structured_steps?: RecipeStep[];
  quality_warnings?: string[];
  substitutions: string[];
  cooking_tips: string[];
  troubleshooting_tips?: string[];
  serving_result?: string | null;
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
    equipment_required: rec.equipment_required ?? [],
    preparation_steps: rec.preparation_steps ?? [],
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
    troubleshooting_tips: rec.troubleshooting_tips ?? [],
    serving_result: rec.serving_result ?? null,
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
    equipment_required: sug.equipment_required ?? [],
    preparation_steps: sug.preparation_steps ?? [],
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
    troubleshooting_tips: sug.troubleshooting_tips ?? [],
    serving_result: sug.serving_result ?? null,
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
  const fmt = (val: number) => {
    const rounded = Math.round(val * 100) / 100;
    return Number.isInteger(rounded) ? String(rounded) : String(rounded);
  };
  // Range e.g. "90-100 ml" or "90–100 ml"
  const rangeMatch = qtyText.match(/^(\d+(?:\.\d+)?)\s*([-–])\s*(\d+(?:\.\d+)?)(.*)$/);
  if (rangeMatch) {
    const lo = Number(rangeMatch[1]);
    const hi = Number(rangeMatch[3]);
    if (Number.isFinite(lo) && Number.isFinite(hi)) {
      return `${fmt(lo * factor)}${rangeMatch[2]}${fmt(hi * factor)}${rangeMatch[4]}`;
    }
  }
  // Fraction e.g. "1/2 tsp"
  const fracMatch = qtyText.match(/^(\d+)\s*\/\s*(\d+)(.*)$/);
  if (fracMatch && Number(fracMatch[2]) > 0) {
    const fracVal = Number(fracMatch[1]) / Number(fracMatch[2]);
    return `${fmt(fracVal * factor)}${fracMatch[3]}`;
  }
  // Decimal or integer e.g. "150 g"
  const match = qtyText.match(/^(\d+(?:\.\d+)?)(.*)$/);
  if (!match) {
    return qtyText;
  }
  const num = Number(match[1]);
  if (!Number.isFinite(num)) {
    return qtyText;
  }
  return `${fmt(num * factor)}${match[2]}`;
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

export interface CompletePlanLineState {
  lineId: string;
  canonical_item_id: string;
  name: string;
  productName: string;
  availableQty: number;
  lotBaseUnit: BaseUnit;
  unitCompatible: boolean;
  lotCount: number;
  planned_qty: string;
  base_unit: BaseUnit;
  actualUsedText: string;
  wasteQtyText: string;
  omitted: boolean;
  sessionConfirmed?: boolean;
  sessionConfirmedQty?: number;
  sessionConfirmedUnit?: BaseUnit;
}

export interface ReconcileCompleteLinesInput {
  planId: string;
  planItems: Array<{
    line_id?: string | null;
    canonical_item_id: string;
    name?: string | null;
    planned_qty: string;
    base_unit: BaseUnit;
    available_qty?: string | number | null;
  }>;
  activeLots: Array<{
    id?: string;
    canonical_item_id: string;
    item_name?: string | null;
    status: string;
    remaining_qty: string | number;
    base_unit: string;
    expires_on?: string | null;
  }>;
  sessionReportedAvailability?: Record<
    string,
    {
      status: string;
      persistedToPantry: boolean;
      availableInRequiredUnit: number;
      requiredUnit: string;
    }
  >;
  existingLines?: CompletePlanLineState[];
  todayIso?: string;
}

export function reconcileCompletePlanLines(
  input: ReconcileCompleteLinesInput
): CompletePlanLineState[] {
  const todayIso = input.todayIso ?? new Date().toISOString().slice(0, 10);
  const existingById = new Map((input.existingLines ?? []).map((l) => [l.lineId, l]));
  const sessionMap = input.sessionReportedAvailability ?? {};

  return input.planItems.map((item, idx) => {
    const lineId = item.line_id || `${input.planId}:${item.canonical_item_id}:${idx}`;
    const existing = existingById.get(lineId);

    const matchingLots = input.activeLots
      .filter(
        (lot) =>
          lot.canonical_item_id === item.canonical_item_id &&
          lot.status === 'active' &&
          Number(lot.remaining_qty) > 0 &&
          (!lot.expires_on || lot.expires_on >= todayIso)
      )
      .sort((a, b) => {
        if (!a.expires_on && !b.expires_on) return 0;
        if (!a.expires_on) return 1;
        if (!b.expires_on) return -1;
        return a.expires_on.localeCompare(b.expires_on);
      });

    const compatibleLots = matchingLots.filter((lot) =>
      areUnitsCompatible(lot.base_unit, item.base_unit)
    );
    const lotBaseUnit = (
      compatibleLots[0]?.base_unit ??
      matchingLots[0]?.base_unit ??
      item.base_unit
    ) as BaseUnit;

    // Compatible lots always win over incompatible ones; only incompatible if lots exist and NONE match.
    const unitCompatible = compatibleLots.length > 0 || matchingLots.length === 0;
    const rawLotAvailable = (
      compatibleLots.length > 0 ? compatibleLots : matchingLots
    ).reduce((sum, lot) => sum + (Number(lot.remaining_qty) || 0), 0);

    const availableInPlanUnit =
      matchingLots.length > 0
        ? unitCompatible
          ? convertCompatibleQty(rawLotAvailable, lotBaseUnit, item.base_unit) ?? rawLotAvailable
          : rawLotAvailable
        : Number(item.available_qty) || 0;

    const resolvedName =
      (item.name && item.name.trim()) || matchingLots[0]?.item_name || 'Ingredient';
    const productName = compatibleLots[0]?.item_name || matchingLots[0]?.item_name || resolvedName || 'Pantry Product';
    const plannedNum = Number(item.planned_qty) || 0;

    const sessionRec = sessionMap[lineId];
    const hasSessionOnlyConfirm =
      Boolean(sessionRec) &&
      !sessionRec.persistedToPantry &&
      sessionRec.status !== 'unavailable' &&
      sessionRec.status !== 'skipped' &&
      sessionRec.availableInRequiredUnit > 0;
    const hasSessionOmit =
      Boolean(sessionRec) &&
      (sessionRec.status === 'unavailable' || sessionRec.status === 'skipped');

    const defaultUsed =
      unitCompatible && availableInPlanUnit > 0
        ? Math.min(plannedNum, availableInPlanUnit)
        : plannedNum;

    if (hasSessionOnlyConfirm && sessionRec) {
      return {
        lineId,
        canonical_item_id: item.canonical_item_id,
        name: resolvedName,
        productName,
        availableQty: availableInPlanUnit,
        lotBaseUnit,
        unitCompatible,
        lotCount: compatibleLots.length > 0 ? compatibleLots.length : matchingLots.length,
        planned_qty: item.planned_qty,
        base_unit: item.base_unit,
        actualUsedText: '0',
        wasteQtyText: '0',
        omitted: false,
        sessionConfirmed: true,
        sessionConfirmedQty: sessionRec.availableInRequiredUnit,
        sessionConfirmedUnit: sessionRec.requiredUnit as BaseUnit,
      };
    }

    if (hasSessionOmit) {
      return {
        lineId,
        canonical_item_id: item.canonical_item_id,
        name: resolvedName,
        productName,
        availableQty: availableInPlanUnit,
        lotBaseUnit,
        unitCompatible,
        lotCount: compatibleLots.length > 0 ? compatibleLots.length : matchingLots.length,
        planned_qty: item.planned_qty,
        base_unit: item.base_unit,
        actualUsedText: '0',
        wasteQtyText: '0',
        omitted: true,
        sessionConfirmed: false,
      };
    }

    const becameCompatible = Boolean(existing && !existing.unitCompatible && unitCompatible);
    const keepExistingEdits = Boolean(existing && !becameCompatible);

    return {
      lineId,
      canonical_item_id: item.canonical_item_id,
      name: resolvedName,
      productName,
      availableQty: availableInPlanUnit,
      lotBaseUnit,
      unitCompatible,
      lotCount: compatibleLots.length > 0 ? compatibleLots.length : matchingLots.length,
      planned_qty: item.planned_qty,
      base_unit: item.base_unit,
      actualUsedText: keepExistingEdits ? existing!.actualUsedText : String(defaultUsed),
      wasteQtyText: keepExistingEdits ? existing!.wasteQtyText : '0',
      omitted: keepExistingEdits
        ? existing!.omitted
        : availableInPlanUnit <= 0 && plannedNum > 0,
      sessionConfirmed: false,
    };
  });
}

export function isCompletePlanLineUnresolved(
  line: CompletePlanLineState,
  combinedDeductionByCanonical: Record<string, number>
): boolean {
  if (line.omitted || line.sessionConfirmed) {
    return false;
  }
  if (!line.unitCompatible) {
    return true;
  }
  const comb = combinedDeductionByCanonical[line.canonical_item_id] ?? 0;
  return comb > line.availableQty + 1e-6;
}

export function computeCombinedDeductionByCanonical(
  lines: DeductionLineInput[]
): Record<string, number> {
  const totals: Record<string, number> = {};
  for (const l of lines) {
    if (l.omitted || (l as CompletePlanLineState).sessionConfirmed) continue;
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
  ingredientQuantities: Record<string, string>;
  durationMinutes: number | null;
  heatLevel: string | null;
  technique: string | null;
  equipment: string[];
  completionCue: string | null;
  caution: string | null;
  nextAction: string | null;
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
  targetServings?: number,
): CookingStepDisplay {
  const structured = detail.structured_steps ?? [];
  const plain = detail.instructions.length > 0
    ? detail.instructions
    : ['Prepare and cook ingredients.'];
  const totalSteps = structured.length > 0 ? structured.length : plain.length;
  const safeIdx = Math.max(0, Math.min(totalSteps - 1, stepIndex));
  const baseServings = detail.servings || 2;
  const effectiveServings = targetServings && targetServings >= 1 ? targetServings : baseServings;

  if (structured.length > 0) {
    const st = structured[safeIdx];
    const scaledQuantities: Record<string, string> = {};
    for (const [ingKey, qtyVal] of Object.entries(st.ingredient_quantities ?? {})) {
      scaledQuantities[ingKey] =
        effectiveServings !== baseServings
          ? scaleQuantityText(qtyVal, baseServings, effectiveServings)
          : qtyVal;
    }
    return {
      stepNumber: st.step_number || safeIdx + 1,
      totalSteps,
      title: st.title || `Step ${safeIdx + 1}`,
      instruction: st.instruction,
      ingredientsUsed: st.ingredients_used ?? [],
      ingredientQuantities: scaledQuantities,
      durationMinutes: st.duration_minutes ?? detectStepMinutes(st.instruction),
      heatLevel: st.heat_level ?? null,
      technique: st.technique ?? null,
      equipment: st.equipment ?? [],
      completionCue: st.completion_cue || null,
      caution: st.caution ?? null,
      nextAction: st.next_action ?? null,
    };
  }

  const text = plain[safeIdx];
  return {
    stepNumber: safeIdx + 1,
    totalSteps,
    title: `Step ${safeIdx + 1}`,
    instruction: text,
    ingredientsUsed: [],
    ingredientQuantities: {},
    durationMinutes: detectStepMinutes(text),
    heatLevel: null,
    technique: null,
    equipment: [],
    completionCue: null,
    caution: null,
    nextAction: null,
  };
}


export function buildFallbackCookingDetail(
  plan: {
    recipe_id?: string | null;
    title?: string | null;
    planned_servings?: number;
    meal_type?: string | null;
    items?: Array<{
      canonical_item_id: string;
      name?: string;
      planned_qty: string;
      base_unit: BaseUnit;
    }>;
  },
  activeLots: Array<{ canonical_item_id: string; item_name?: string }>
): RecipeDetailTarget {
  return {
    recipe_id: plan.recipe_id ?? null,
    title: plan.title ?? 'Planned Meal',
    servings: plan.planned_servings || 2,
    meal_type: plan.meal_type ?? null,
    source_type: 'ai_generated',
    source_label: 'Planned meal',
    ingredients: (plan.items ?? []).map((it) => {
      const matchingLot = activeLots.find(
        (lot) => lot.canonical_item_id === it.canonical_item_id
      );
      const resolvedName =
        (it.name && it.name.trim()) || matchingLot?.item_name || 'Ingredient';
      return {
        name: resolvedName,
        canonical_item_id: it.canonical_item_id,
        quantity: it.planned_qty,
        base_unit: it.base_unit,
        available: true,
      };
    }),
    instructions: [
      `Detailed step-by-step instructions were not saved for ${plan.title ?? 'this meal'}.`,
      'Prepare your planned ingredients and cook to taste.',
    ],
    substitutions: [],
    cooking_tips: [],
    food_safety_notes: [],
    uses_expiring_items: [],
    external_links: [],
  };
}

export const AI_CHEF_CHAT_TIMEOUT_MS = 65_000;

export interface AskChefTurnInput {
  role: 'user' | 'assistant';
  text: string;
  suggestions?: RecipeChatSuggestion[];
}

export interface MappedAssistantChatTurn {
  text: string;
  providerStatus: AiProviderStatus;
  providerMessage: string | null;
  followUpQuestions: string[];
  suggestions: RecipeChatSuggestion[];
  externalLinks: RecipeChatExternalLink[];
}

export function buildAskChefPayload(params: {
  message: string;
  servings: number;
  selectedConstraints: string[];
  hungerLevel: string | null;
  cookingTimeMinutes: number | null;
  chatTurns: AskChefTurnInput[];
  recommendationMode?: 'enjoyed' | 'new' | 'balanced';
}): RecipeChatRequest {
  const historyPayload = params.chatTurns.slice(-8).map((t) => ({
    role: t.role,
    content:
      t.role === 'assistant' && t.suggestions && t.suggestions.length > 0
        ? `${t.text} [Suggested: ${t.suggestions.map((s) => s.title).join(', ')}]`
        : t.text,
  }));
  const effectiveMaxMinutes =
    params.cookingTimeMinutes ??
    (params.selectedConstraints.includes('Quick meal') ? 20 : undefined);

  return {
    message: params.message,
    servings: params.servings,
    dietary_constraints:
      params.selectedConstraints.length > 0 ? params.selectedConstraints : undefined,
    preference_tags:
      params.selectedConstraints.length > 0 ? params.selectedConstraints : undefined,
    hunger_level: params.hungerLevel ?? undefined,
    max_prep_minutes: effectiveMaxMinutes ?? undefined,
    recommendation_mode: params.recommendationMode,
    conversation_history: historyPayload.length > 0 ? historyPayload : undefined,
  };
}

export function mapChatResponseToTurn(res: RecipeChatResponse): MappedAssistantChatTurn {
  const fallbackReply = res.empty_inventory
    ? 'Your pantry is currently empty. Add or scan groceries first so I can suggest recipes using what you have.'
    : (res.suggestions?.length ?? 0) > 0
      ? 'Here are recipe suggestions based on your preferences and pantry.'
      : res.provider_message || 'No recipe suggestions were returned. Please try refining your request.';
  const text = (res.reply ?? '').trim() || fallbackReply;
  return {
    text,
    providerStatus: res.provider_status,
    providerMessage: res.provider_message ?? null,
    followUpQuestions: res.follow_up_questions ?? [],
    suggestions: res.suggestions ?? [],
    externalLinks: (res.external_links ?? []).filter((link) => link.verified === true),
  };
}
