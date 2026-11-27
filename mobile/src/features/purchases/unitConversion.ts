/**
 * Unit conversion and price-basis helpers for purchase review.
 * Ensures switching kg <-> g or l <-> ml converts both quantity and unit price
 * consistently so line totals are never corrupted.
 */

export interface ConvertedUnitFields<U extends string = string> {
  qtyText: string;
  unit: U;
  unitPriceRupeesText: string;
  lineTotalRupeesText: string;
}

function getScaleFactor(fromUnit: string, toUnit: string): number | null {
  const from = fromUnit.toLowerCase();
  const to = toUnit.toLowerCase();
  if (from === to) return 1;
  if (from === "kg" && to === "g") return 1000;
  if (from === "g" && to === "kg") return 0.001;
  if (from === "l" && to === "ml") return 1000;
  if (from === "ml" && to === "l") return 0.001;
  return null;
}

function formatTrimmedNumber(value: number, maxDecimals: number): string {
  if (!Number.isFinite(value)) return "0";
  return Number(value.toFixed(maxDecimals)).toString();
}

/**
 * Convert quantity and unit price consistently between compatible units
 * (kg <-> g, l <-> ml) while preserving the exact commercial line total.
 */
export function convertQuantityAndUnitPrice<U extends string>(params: {
  qtyText: string;
  unitPriceRupeesText: string;
  lineTotalRupeesText: string;
  fromUnit: U;
  toUnit: U;
}): ConvertedUnitFields<U> {
  const factor = getScaleFactor(params.fromUnit, params.toUnit);
  const qty = Number(params.qtyText);
  const unitPrice = Number(params.unitPriceRupeesText);

  if (
    factor === null ||
    factor === 1 ||
    !Number.isFinite(qty) ||
    qty <= 0 ||
    !Number.isFinite(unitPrice) ||
    unitPrice < 0
  ) {
    return {
      qtyText: params.qtyText,
      unit: params.toUnit,
      unitPriceRupeesText: params.unitPriceRupeesText,
      lineTotalRupeesText: params.lineTotalRupeesText,
    };
  }

  const nextQty = qty * factor;
  const nextUnitPrice = unitPrice / factor;
  const nextTotal = nextQty * nextUnitPrice;

  return {
    qtyText: formatTrimmedNumber(nextQty, 4),
    unit: params.toUnit,
    unitPriceRupeesText: formatTrimmedNumber(nextUnitPrice, 4),
    lineTotalRupeesText: nextTotal.toFixed(2),
  };
}

/**
 * Format a purchase item's quantity while explicitly distinguishing known
 * original purchase quantities from legacy normalized-only records.
 */
export function formatPurchaseQuantityDisplay(item: {
  purchase_qty?: number | null;
  purchase_unit?: string | null;
  normalized_qty: string | number;
  base_unit: string;
}): string {
  if (item.purchase_qty != null && item.purchase_unit) {
    const qtyStr = formatTrimmedNumber(Number(item.purchase_qty), 3);
    return `${qtyStr} ${item.purchase_unit}`;
  }
  const normStr = formatTrimmedNumber(Number(item.normalized_qty), 3);
  return `${normStr} ${item.base_unit} (inventory unit)`;
}
