import { BaseUnit } from '../api/types';

export type InputUnit = BaseUnit | 'kg' | 'l' | 'L' | 'pc';

export type PackagingUnit =
  | 'pack'
  | 'bottle'
  | 'packet'
  | 'box'
  | 'jar'
  | 'can'
  | 'loaf'
  | 'slice';

export type PantryUnit =
  | 'g'
  | 'kg'
  | 'ml'
  | 'L'
  | 'l'
  | 'pcs'
  | 'dozen'
  | PackagingUnit;

export type UnitCategory = 'weight' | 'volume' | 'count' | 'packaging';

export const UNIT_CATEGORIES: {
  category: UnitCategory;
  label: string;
  units: PantryUnit[];
}[] = [
  { category: 'weight', label: 'Weight', units: ['g', 'kg'] },
  { category: 'volume', label: 'Volume', units: ['ml', 'L'] },
  { category: 'count', label: 'Count', units: ['pcs', 'dozen'] },
  {
    category: 'packaging',
    label: 'Packaging',
    units: ['pack', 'bottle', 'packet', 'box', 'jar', 'can', 'loaf', 'slice'],
  },
];

export function canonicalUnitToken(unit?: string | null): string {
  const low = (unit ?? '').trim().toLowerCase();
  if (!low) return 'pcs';
  if (low === 'pc' || low === 'piece' || low === 'pieces' || low === 'unit' || low === 'units') return 'pcs';
  if (low === 'dozens' || low === 'dz') return 'dozen';
  if (low === 'gm' || low === 'gms' || low === 'gram' || low === 'grams') return 'g';
  if (low === 'kgs' || low === 'kilogram' || low === 'kilograms') return 'kg';
  if (low === 'ltr' || low === 'lt' || low === 'liter' || low === 'litre' || low === 'liters' || low === 'litres') return 'l';
  if (low === 'packs') return 'pack';
  if (low === 'bottles' || low === 'btl') return 'bottle';
  if (low === 'packets' || low === 'pkt') return 'packet';
  if (low === 'boxes') return 'box';
  if (low === 'jars') return 'jar';
  if (low === 'cans' || low === 'tin') return 'can';
  if (low === 'loaves') return 'loaf';
  if (low === 'slices') return 'slice';
  return low;
}

export function displayUnitLabel(unit?: string | null): PantryUnit {
  const tok = canonicalUnitToken(unit);
  if (tok === 'l') return 'L';
  return tok as PantryUnit;
}

export function getUnitCategory(unit?: string | null): UnitCategory {
  const tok = canonicalUnitToken(unit);
  if (tok === 'g' || tok === 'kg') return 'weight';
  if (tok === 'ml' || tok === 'l') return 'volume';
  if (tok === 'pcs' || tok === 'dozen') return 'count';
  return 'packaging';
}

export function convertCompatibleUnits(
  qty: number,
  fromUnit?: string | null,
  toUnit?: string | null
): number | null {
  if (!Number.isFinite(qty)) return null;
  const f = canonicalUnitToken(fromUnit);
  const t = canonicalUnitToken(toUnit);
  if (f === t) return Number(qty.toFixed(3));
  if (f === 'kg' && t === 'g') return Number((qty * 1000).toFixed(3));
  if (f === 'g' && t === 'kg') return Number((qty / 1000).toFixed(3));
  if (f === 'l' && t === 'ml') return Number((qty * 1000).toFixed(3));
  if (f === 'ml' && t === 'l') return Number((qty / 1000).toFixed(3));
  if (f === 'dozen' && t === 'pcs') return Number((qty * 12).toFixed(3));
  if (f === 'pcs' && t === 'dozen') return Number((qty / 12).toFixed(3));
  return null;
}

export function suggestProductUnits(
  name?: string | null,
  currentUnit?: string | null
): { category: UnitCategory; label: string; units: PantryUnit[] }[] {
  const low = (name ?? '').trim().toLowerCase();
  const curr = displayUnitLabel(currentUnit);
  const currCat = getUnitCategory(curr);
  const suitable = new Set<PantryUnit>([curr]);

  if (/\b(rice|sugar|flour|atta|dal|lentil|salt|spice)\b/.test(low)) {
    suitable.add('g');
    suitable.add('kg');
  } else if (/\b(milk|oil|ghee|vinegar|water|broth)\b/.test(low)) {
    suitable.add('ml');
    suitable.add('L');
  } else if (/\b(juice|soda|drink|beverage|sauce|syrup)\b/.test(low)) {
    suitable.add('ml');
    suitable.add('L');
    suitable.add('bottle');
    suitable.add('pack');
  } else if (/\beggs?\b/.test(low)) {
    suitable.add('pcs');
    suitable.add('dozen');
  } else if (/\b(chocolates?|cand(?:y|ies)|bars?)\b/.test(low)) {
    suitable.add('pcs');
    suitable.add('g');
  } else if (/\b(biscuits?|cookies?|crackers?|chips|snacks?|noodles?)\b/.test(low)) {
    suitable.add('pcs');
    suitable.add('pack');
    suitable.add('packet');
  } else if (/\b(breads?|toasts?|buns?)\b/.test(low)) {
    suitable.add('loaf');
    suitable.add('slice');
  } else if (/\b(curd|yogurt|yoghurt|dahi|cream)\b/.test(low)) {
    suitable.add('g');
    suitable.add('kg');
    suitable.add('ml');
    suitable.add('L');
  } else if (currCat === 'weight') {
    suitable.add('g');
    suitable.add('kg');
  } else if (currCat === 'volume') {
    suitable.add('ml');
    suitable.add('L');
  } else if (currCat === 'count') {
    suitable.add('pcs');
    suitable.add('dozen');
  } else {
    suitable.add('pack');
    suitable.add('packet');
    suitable.add('bottle');
  }

  return UNIT_CATEGORIES.map((group) => ({
    category: group.category,
    label: group.label,
    units: group.units.filter((u) => suitable.has(u)),
  })).filter((g) => g.units.length > 0);
}

export interface NormalizedQuantity {
  qty: string;
  baseUnit: BaseUnit;
}

export interface ExpiryBadgeInfo {
  label: string;
  tone: 'expired' | 'soon' | 'ok' | 'none';
}

/**
 * Strips unnecessary trailing zeros from a decimal string (e.g. `"250.000"` -> `"250"`, `"1.500"` -> `"1.5"`).
 */
export function formatDecimalNumber(value: string | number, maxDecimals = 2): string {
  const num = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(num)) {
    return '0';
  }
  const fixed = num.toFixed(maxDecimals);
  return fixed.replace(/\.?0+$/, '');
}

/**
 * Formats a metric quantity + base unit for display.
 * Automatically scales `>= 1000 g` to `kg` and `>= 1000 ml` to `L` unless `autoScale` is false.
 */
export function formatQuantityWithUnit(
  qty: string | number,
  unit: BaseUnit | string,
  options: { autoScale?: boolean } = {}
): string {
  const autoScale = options.autoScale ?? true;
  const numericQty = typeof qty === 'number' ? qty : Number(qty);

  if (!Number.isFinite(numericQty)) {
    return `0 ${unit}`;
  }

  if (autoScale && unit === 'g' && Math.abs(numericQty) >= 1000) {
    return `${formatDecimalNumber(numericQty / 1000, 2)} kg`;
  }

  if (autoScale && unit === 'ml' && Math.abs(numericQty) >= 1000) {
    return `${formatDecimalNumber(numericQty / 1000, 2)} L`;
  }

  if (unit === 'pcs') {
    const label = Math.abs(numericQty) === 1 ? 'pc' : 'pcs';
    return `${formatDecimalNumber(numericQty, 2)} ${label}`;
  }

  return `${formatDecimalNumber(numericQty, 2)} ${unit}`;
}

/**
 * Normalizes a user-entered quantity and unit (`g`, `kg`, `ml`, `l`, `L`, `pc`, `pcs`)
 * into canonical metric base units (`g`, `ml`, `pcs`) with 3-decimal string precision.
 */
export function normalizeToBaseUnit(
  value: number,
  unit: InputUnit
): NormalizedQuantity {
  if (!Number.isFinite(value) || value < 0) {
    const fallbackUnit: BaseUnit =
      unit === 'ml' || unit === 'l' || unit === 'L'
        ? 'ml'
        : unit === 'pcs' || unit === 'pc'
          ? 'pcs'
          : 'g';
    return { qty: '0.000', baseUnit: fallbackUnit };
  }

  switch (unit) {
    case 'kg':
      return { qty: (value * 1000).toFixed(3), baseUnit: 'g' };
    case 'g':
      return { qty: value.toFixed(3), baseUnit: 'g' };
    case 'l':
    case 'L':
      return { qty: (value * 1000).toFixed(3), baseUnit: 'ml' };
    case 'ml':
      return { qty: value.toFixed(3), baseUnit: 'ml' };
    case 'pc':
    case 'pcs':
      return { qty: value.toFixed(3), baseUnit: 'pcs' };
  }
}

/**
 * Computes an accessible expiry status badge from a `YYYY-MM-DD` date string.
 */
export function formatExpiryBadge(
  expiresOn: string | null | undefined,
  referenceDate: Date = new Date()
): ExpiryBadgeInfo {
  if (!expiresOn || !/^\d{4}-\d{2}-\d{2}$/.test(expiresOn)) {
    return { label: 'No expiry', tone: 'none' };
  }

  const [yearStr, monthStr, dayStr] = expiresOn.split('-');
  const expiryUtc = Date.UTC(
    Number(yearStr),
    Number(monthStr) - 1,
    Number(dayStr)
  );
  const todayUtc = Date.UTC(
    referenceDate.getFullYear(),
    referenceDate.getMonth(),
    referenceDate.getDate()
  );

  const diffDays = Math.round((expiryUtc - todayUtc) / (1000 * 60 * 60 * 24));

  if (diffDays < 0) {
    return { label: 'Expired', tone: 'expired' };
  }
  if (diffDays === 0) {
    return { label: 'Expires today', tone: 'soon' };
  }
  if (diffDays <= 3) {
    return {
      label: diffDays === 1 ? '1 day left' : `${diffDays} days left`,
      tone: 'soon',
    };
  }
  return { label: `Exp ${expiresOn}`, tone: 'ok' };
}

/**
 * Generates a lightweight client-side idempotency key for mutating API requests.
 */
export function createIdempotencyKey(prefix = 'mob'): string {
  const randomPart = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now()}-${randomPart}`;
}
