export interface FormatInrOptions {
  /** When true, omits `.00` for whole-rupee amounts (default: false). */
  omitZeroDecimals?: boolean;
}

/**
 * Formats integer digits using the Indian numbering system (e.g., `125000` -> `1,25,000`).
 */
function formatIndianIntegerGroup(integerDigits: string): string {
  if (integerDigits.length <= 3) {
    return integerDigits;
  }
  const lastThree = integerDigits.slice(-3);
  const rest = integerDigits.slice(0, -3);
  const groupedRest = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',');
  return `${groupedRest},${lastThree}`;
}

/**
 * Formats an integer paise amount into a deterministic INR currency string (e.g. `5400` -> `₹54.00`).
 */
export function formatInrFromPaise(
  paise: number,
  options: FormatInrOptions = {}
): string {
  if (!Number.isFinite(paise)) {
    return '₹0.00';
  }

  const roundedPaise = Math.round(paise);
  const isNegative = roundedPaise < 0;
  const absPaise = Math.abs(roundedPaise);

  const wholeRupees = Math.floor(absPaise / 100);
  const remainingPaise = absPaise % 100;
  const formattedWhole = formatIndianIntegerGroup(String(wholeRupees));
  const prefix = isNegative ? '-₹' : '₹';

  if (options.omitZeroDecimals && remainingPaise === 0) {
    return `${prefix}${formattedWhole}`;
  }

  const decimalPart = String(remainingPaise).padStart(2, '0');
  return `${prefix}${formattedWhole}.${decimalPart}`;
}

/**
 * Converts a rupee input string or number (e.g., `"54.50"` or `54`) into integer paise (`5450`).
 * Returns `null` if the input cannot be parsed as a valid non-negative finite number.
 */
export function rupeesToPaise(input: string | number): number | null {
  const cleaned =
    typeof input === 'number'
      ? String(input)
      : input.replace(/[₹,\s]/g, '').trim();

  if (cleaned.length === 0) {
    return null;
  }

  const parsed = Number(cleaned);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return null;
  }

  return Math.round(parsed * 100);
}

/**
 * Converts integer paise to a plain decimal rupee string suitable for text inputs (e.g. `5400` -> `"54.00"`).
 */
export function paiseToRupeesString(paise: number): string {
  if (!Number.isFinite(paise)) {
    return '0.00';
  }
  return (Math.round(paise) / 100).toFixed(2);
}
