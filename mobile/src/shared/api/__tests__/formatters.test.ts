import {
  formatInrFromPaise,
  paiseToRupeesString,
  rupeesToPaise,
} from '../../format/inr';
import {
  formatDecimalNumber,
  formatExpiryBadge,
  formatQuantityWithUnit,
  normalizeToBaseUnit,
} from '../../format/units';

describe('INR currency formatters', () => {
  it('formats paise into INR with Indian grouping and 2 decimals', () => {
    expect(formatInrFromPaise(0)).toBe('₹0.00');
    expect(formatInrFromPaise(5400)).toBe('₹54.00');
    expect(formatInrFromPaise(9450)).toBe('₹94.50');
    expect(formatInrFromPaise(12500050)).toBe('₹1,25,000.50');
    expect(formatInrFromPaise(-12500)).toBe('-₹125.00');
  });

  it('omits zero decimals when omitZeroDecimals is enabled', () => {
    expect(formatInrFromPaise(5400, { omitZeroDecimals: true })).toBe('₹54');
    expect(formatInrFromPaise(5450, { omitZeroDecimals: true })).toBe('₹54.50');
  });

  it('parses rupee inputs to integer paise safely', () => {
    expect(rupeesToPaise('54')).toBe(5400);
    expect(rupeesToPaise('54.50')).toBe(5450);
    expect(rupeesToPaise('₹1,250.25')).toBe(125025);
    expect(rupeesToPaise(19.99)).toBe(1999);
    expect(rupeesToPaise('')).toBeNull();
    expect(rupeesToPaise('-10')).toBeNull();
    expect(rupeesToPaise('abc')).toBeNull();
  });

  it('converts paise to fixed 2-decimal rupee input strings', () => {
    expect(paiseToRupeesString(5400)).toBe('54.00');
    expect(paiseToRupeesString(99)).toBe('0.99');
  });
});

describe('Metric unit formatters', () => {
  it('formats decimal numbers without trailing zeros', () => {
    expect(formatDecimalNumber('250.000')).toBe('250');
    expect(formatDecimalNumber('1.500')).toBe('1.5');
    expect(formatDecimalNumber('0.000')).toBe('0');
  });

  it('formats quantities with auto-scaling for g/kg and ml/L', () => {
    expect(formatQuantityWithUnit('500.000', 'g')).toBe('500 g');
    expect(formatQuantityWithUnit('1000.000', 'g')).toBe('1 kg');
    expect(formatQuantityWithUnit('1500.000', 'ml')).toBe('1.5 L');
    expect(formatQuantityWithUnit('250.000', 'ml')).toBe('250 ml');
    expect(formatQuantityWithUnit('1.000', 'pcs')).toBe('1 pc');
    expect(formatQuantityWithUnit('6.000', 'pcs')).toBe('6 pcs');
  });

  it('supports disabling autoScale', () => {
    expect(formatQuantityWithUnit('1500.000', 'g', { autoScale: false })).toBe(
      '1500 g'
    );
  });

  it('normalizes user-entered units to canonical metric base units', () => {
    expect(normalizeToBaseUnit(1.5, 'kg')).toEqual({
      qty: '1500.000',
      baseUnit: 'g',
    });
    expect(normalizeToBaseUnit(2, 'L')).toEqual({
      qty: '2000.000',
      baseUnit: 'ml',
    });
    expect(normalizeToBaseUnit(250, 'ml')).toEqual({
      qty: '250.000',
      baseUnit: 'ml',
    });
    expect(normalizeToBaseUnit(3, 'pc')).toEqual({
      qty: '3.000',
      baseUnit: 'pcs',
    });
  });

  it('computes expiry badges relative to reference date', () => {
    const today = new Date(2026, 10, 17); // 2026-11-17
    expect(formatExpiryBadge(null, today)).toEqual({
      label: 'No expiry',
      tone: 'none',
    });
    expect(formatExpiryBadge('2026-11-16', today)).toEqual({
      label: 'Expired',
      tone: 'expired',
    });
    expect(formatExpiryBadge('2026-11-17', today)).toEqual({
      label: 'Expires today',
      tone: 'soon',
    });
    expect(formatExpiryBadge('2026-11-19', today)).toEqual({
      label: '2 days left',
      tone: 'soon',
    });
    expect(formatExpiryBadge('2026-11-25', today)).toEqual({
      label: 'Exp 2026-11-25',
      tone: 'ok',
    });
  });
});
