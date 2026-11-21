import { paiseToRupeesString, rupeesToPaise } from '../../shared/format/inr';

export interface FinancialLineInput {
  localKey: string;
  qtyText: string;
  unitPriceRupeesText: string;
  lineTotalRupeesText: string;
}

export interface FinancialSummaryResult {
  itemsSumPaise: number;
  effectiveSubtotalPaise: number;
  subtotalIsDerived: boolean;
  discountPaise: number;
  chargesPaise: number;
  taxPaise: number;
  roundingPaise: number;
  computedGrandTotalPaise: number;
  headerTotalPaise: number;
  mismatchPaise: number;
  hasMathMismatch: boolean;
  mismatchedLineKeys: string[];
}

export function parseSignedRupees(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  const neg = trimmed.startsWith('-');
  const absPaise = rupeesToPaise(trimmed.replace(/^[+-]\s*/, '')) ?? 0;
  return neg ? -absPaise : absPaise;
}

export function computeLineTotalFromQtyAndRate(
  qtyText: string,
  unitPriceRupeesText: string
): string | null {
  const qty = Number(qtyText);
  const unitPaise = rupeesToPaise(unitPriceRupeesText);
  if (!Number.isFinite(qty) || qty <= 0 || unitPaise === null || unitPaise <= 0) {
    return null;
  }
  return paiseToRupeesString(Math.round(qty * unitPaise));
}

export function computeDraftFinancials(params: {
  lines: FinancialLineInput[];
  subtotalRupees: string;
  discountRupees: string;
  additionalChargesRupees: string;
  cgstRupees: string;
  sgstRupees: string;
  igstRupees: string;
  roundingRupees: string;
  receiptTotalRupees: string;
}): FinancialSummaryResult {
  let itemsSumPaise = 0;
  const mismatchedLineKeys: string[] = [];

  for (const line of params.lines) {
    const linePaise = rupeesToPaise(line.lineTotalRupeesText) ?? 0;
    itemsSumPaise += linePaise;

    const qty = Number(line.qtyText);
    const unitPaise = rupeesToPaise(line.unitPriceRupeesText);
    if (Number.isFinite(qty) && qty > 0 && unitPaise !== null && unitPaise > 0) {
      const expected = Math.round(qty * unitPaise);
      if (Math.abs(expected - linePaise) > Math.max(200, Math.round(linePaise * 0.05))) {
        mismatchedLineKeys.push(line.localKey);
      }
    }
  }

  const parsedSub = params.subtotalRupees.trim()
    ? rupeesToPaise(params.subtotalRupees)
    : null;
  const subtotalIsDerived = parsedSub === null;
  const effectiveSubtotalPaise = parsedSub !== null ? parsedSub : itemsSumPaise;

  const discountPaise = rupeesToPaise(params.discountRupees) ?? 0;
  const chargesPaise = rupeesToPaise(params.additionalChargesRupees) ?? 0;
  const taxPaise =
    (rupeesToPaise(params.cgstRupees) ?? 0) +
    (rupeesToPaise(params.sgstRupees) ?? 0) +
    (rupeesToPaise(params.igstRupees) ?? 0);
  const roundingPaise = parseSignedRupees(params.roundingRupees);

  const computedGrandTotalPaise =
    itemsSumPaise - discountPaise + chargesPaise + taxPaise + roundingPaise;
  const headerTotalPaise = rupeesToPaise(params.receiptTotalRupees) ?? 0;
  const mismatchPaise = Math.abs(computedGrandTotalPaise - headerTotalPaise);

  return {
    itemsSumPaise,
    effectiveSubtotalPaise,
    subtotalIsDerived,
    discountPaise,
    chargesPaise,
    taxPaise,
    roundingPaise,
    computedGrandTotalPaise,
    headerTotalPaise,
    mismatchPaise,
    hasMathMismatch: params.lines.length > 0 && mismatchPaise > 200,
    mismatchedLineKeys,
  };
}

/** Filter out stale static OCR math-mismatch banners so only live draft math is shown. */
export function filterLiveOcrWarnings(warnings: string[]): string[] {
  return warnings.filter((w) => !w.trim().toLowerCase().startsWith('math mismatch:'));
}
