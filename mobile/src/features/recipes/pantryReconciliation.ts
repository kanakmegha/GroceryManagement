import type { BaseUnit } from '../../shared/api/types';
import { areUnitsCompatible, convertCompatibleQty } from './recipeHelpers';

export type ReportedUnit = BaseUnit | 'kg' | 'l' | 'dozen';

export type AvailabilityResolutionStatus =
  | 'sufficient'
  | 'insufficient'
  | 'unavailable'
  | 'skipped';

export interface ReportedAvailabilityEntry {
  key: string;
  name: string;
  canonical_item_id: string | null;
  reportedQty: number;
  reportedUnit: ReportedUnit;
  requiredQty: number;
  requiredUnit: ReportedUnit;
  status: AvailabilityResolutionStatus;
  availableInRequiredUnit: number;
  remainingAfterRecipe: number;
  shortageQty: number;
  persistedToPantry: boolean;
  note?: string;
  recordedAt: string;
}

export type EvaluateReportedResult =
  | {
      ok: true;
      availableInRequiredUnit: number;
      status: 'sufficient' | 'insufficient';
      remainingAfterRecipe: number;
      shortageQty: number;
    }
  | {
      ok: false;
      error: string;
    };

export interface PostCookingReconciliationInput {
  confirmedPantryQty: number;
  sessionReportedQty: number;
  actualUsedQty: number;
  persistedToPantry: boolean;
}

export interface PostCookingReconciliationResult {
  pantryDeductionQty: number;
  sessionConsumedQty: number;
  remainingConfirmedPantry: number;
  remainingSessionReported: number;
  exceedsKnownAvailable: boolean;
  warning: string | null;
}

/**
 * Normalizes a user-entered display unit (kg, l, dozen, etc.) into the
 * backend's canonical BaseUnit ('g' | 'ml' | 'pcs').
 */
export function toCanonicalBaseUnit(
  qty: number,
  unit: ReportedUnit
): { qty: number; unit: BaseUnit } {
  if (unit === 'kg') return { qty: qty * 1000, unit: 'g' };
  if (unit === 'l') return { qty: qty * 1000, unit: 'ml' };
  if (unit === 'dozen') return { qty: qty * 12, unit: 'pcs' };
  return { qty, unit };
}

/**
 * Returns the compatible unit choices for the given recipe base unit without
 * allowing cross-dimensional conversions (e.g. pcs <-> g).
 */
export function getCompatibleUnitsForUnit(unit: ReportedUnit): ReportedUnit[] {
  if (unit === 'g' || unit === 'kg') {
    return ['g', 'kg'];
  }
  if (unit === 'ml' || unit === 'l') {
    return ['ml', 'l'];
  }
  if (unit === 'pcs' || unit === 'dozen') {
    return ['pcs', 'dozen'];
  }
  return [unit];
}

/**
 * Compares a user-reported available quantity against the recipe requirement.
 * Rejects non-positive quantities and cross-dimensional unit mismatches.
 */
export function evaluateReportedAvailability(input: {
  reportedQty: number;
  reportedUnit: ReportedUnit;
  requiredQty: number;
  requiredUnit: ReportedUnit;
}): EvaluateReportedResult {
  const { reportedQty, reportedUnit, requiredQty, requiredUnit } = input;
  if (!Number.isFinite(reportedQty) || reportedQty <= 0) {
    return { ok: false, error: 'Enter an available quantity greater than 0.' };
  }
  if (!areUnitsCompatible(reportedUnit, requiredUnit)) {
    return {
      ok: false,
      error: `Unit mismatch: recipe uses ${requiredUnit}, which is incompatible with ${reportedUnit}.`,
    };
  }

  const converted = convertCompatibleQty(reportedQty, reportedUnit, requiredUnit);
  if (converted === null || !Number.isFinite(converted)) {
    return {
      ok: false,
      error: `Could not convert ${reportedUnit} to ${requiredUnit}.`,
    };
  }

  const available = Number(converted.toFixed(3));
  const required = Math.max(0, Number(requiredQty) || 0);
  const isSufficient = available + 1e-6 >= required;

  return {
    ok: true,
    availableInRequiredUnit: available,
    status: isSufficient ? 'sufficient' : 'insufficient',
    remainingAfterRecipe: Math.max(0, Number((available - required).toFixed(3))),
    shortageQty: Math.max(0, Number((required - available).toFixed(3))),
  };
}

/**
 * Builds a structured session-reported availability entry (never mutating
 * persistent InventoryLot or StockLedger on its own).
 */
export function buildSessionAvailabilityRecord(input: {
  key: string;
  name: string;
  canonical_item_id: string | null;
  reportedQty: number;
  reportedUnit: ReportedUnit;
  requiredQty: number;
  requiredUnit: ReportedUnit;
  persistedToPantry: boolean;
  note?: string;
  mode?: 'reported' | 'unavailable' | 'skipped';
}): ReportedAvailabilityEntry {
  const nowIso = new Date().toISOString();
  if (input.mode === 'unavailable' || input.mode === 'skipped') {
    return {
      key: input.key,
      name: input.name,
      canonical_item_id: input.canonical_item_id,
      reportedQty: 0,
      reportedUnit: input.reportedUnit,
      requiredQty: input.requiredQty,
      requiredUnit: input.requiredUnit,
      status: input.mode,
      availableInRequiredUnit: 0,
      remainingAfterRecipe: 0,
      shortageQty: Math.max(0, Number(input.requiredQty) || 0),
      persistedToPantry: false,
      note: input.note?.trim() || undefined,
      recordedAt: nowIso,
    };
  }

  const evalRes = evaluateReportedAvailability({
    reportedQty: input.reportedQty,
    reportedUnit: input.reportedUnit,
    requiredQty: input.requiredQty,
    requiredUnit: input.requiredUnit,
  });
  if (!evalRes.ok) {
    throw new Error(evalRes.error);
  }

  return {
    key: input.key,
    name: input.name,
    canonical_item_id: input.canonical_item_id,
    reportedQty: input.reportedQty,
    reportedUnit: input.reportedUnit,
    requiredQty: input.requiredQty,
    requiredUnit: input.requiredUnit,
    status: evalRes.status,
    availableInRequiredUnit: evalRes.availableInRequiredUnit,
    remainingAfterRecipe: evalRes.remainingAfterRecipe,
    shortageQty: evalRes.shortageQty,
    persistedToPantry: input.persistedToPantry,
    note: input.note?.trim() || undefined,
    recordedAt: nowIso,
  };
}

/**
 * Reconciles actual post-cooking usage against confirmed pantry inventory and
 * session-reported availability without double-deducting or producing negative stock.
 */
export function reconcilePostCookingConsumption(
  input: PostCookingReconciliationInput
): PostCookingReconciliationResult {
  const confirmed = Math.max(0, Number(input.confirmedPantryQty) || 0);
  const sessionRep = input.persistedToPantry
    ? 0
    : Math.max(0, Number(input.sessionReportedQty) || 0);
  const actual = Math.max(0, Number(input.actualUsedQty) || 0);

  const totalKnown = confirmed + sessionRep;
  const exceedsKnownAvailable = actual > totalKnown + 1e-6;

  // Deduct from confirmed pantry first, capped so stock never goes negative.
  const pantryDeductionQty = Number(Math.min(confirmed, actual).toFixed(3));
  const remainingNeededFromSession = Math.max(0, actual - pantryDeductionQty);
  const sessionConsumedQty = Number(
    Math.min(sessionRep, remainingNeededFromSession).toFixed(3)
  );

  return {
    pantryDeductionQty,
    sessionConsumedQty,
    remainingConfirmedPantry: Number(Math.max(0, confirmed - pantryDeductionQty).toFixed(3)),
    remainingSessionReported: Number(Math.max(0, sessionRep - sessionConsumedQty).toFixed(3)),
    exceedsKnownAvailable,
    warning: exceedsKnownAvailable
      ? `Actual used (${actual}) exceeds total known available (${totalKnown}). Pantry deduction capped at ${pantryDeductionQty} to avoid negative stock.`
      : null,
  };
}
