import {
  buildInsightsEndpoint,
  buildTrendBars,
  formatBudgetStatusLabel,
  formatPeriodTitle,
  getCanonicalPeriodStart,
  getKolkataTodayIso,
  getWeekBounds,
  isAtCurrentPeriod,
  shiftPeriodAnchor,
  validateBudgetInputs,
} from '../budgetPeriodUtils';
import type { InsightsSummaryResponse } from '../../../shared/api/types';

describe('BudgetScreen period selection, navigation, API params, and rendering', () => {
  it('computes current date in Asia/Kolkata across UTC midnight boundary', () => {
    // 2026-11-19T18:45:00Z is 2026-11-20 00:15 IST
    const utcLate = new Date('2026-11-19T18:45:00.000Z');
    expect(getKolkataTodayIso(utcLate)).toBe('2026-11-20');
  });

  it('computes Monday-Sunday week bounds accurately across month and year transitions', () => {
    expect(getWeekBounds('2026-11-18')).toEqual({
      start: '2026-11-16',
      end: '2026-11-22',
    });
    expect(getWeekBounds('2026-01-01')).toEqual({
      start: '2025-12-29',
      end: '2026-01-04',
    });
  });

  it('prevents navigating into future periods across daily, weekly, monthly, and yearly', () => {
    const today = '2026-11-19';
    expect(isAtCurrentPeriod('daily', today, today)).toBe(true);
    expect(shiftPeriodAnchor('daily', today, 1, today)).toBe(today);

    expect(isAtCurrentPeriod('weekly', '2026-11-16', today)).toBe(true);
    expect(shiftPeriodAnchor('weekly', '2026-11-16', 1, today)).toBe('2026-11-16');

    expect(isAtCurrentPeriod('monthly', '2026-11-01', today)).toBe(true);
    expect(shiftPeriodAnchor('monthly', '2026-11-01', 1, today)).toBe('2026-11-01');

    expect(isAtCurrentPeriod('yearly', '2026-01-01', today)).toBe(true);
    expect(shiftPeriodAnchor('yearly', '2026-01-01', 1, today)).toBe('2026-01-01');
  });

  it('shifts backward and forward properly for all four periods', () => {
    const today = '2026-11-19';
    expect(shiftPeriodAnchor('daily', '2026-11-19', -1, today)).toBe('2026-11-18');
    expect(shiftPeriodAnchor('weekly', '2026-11-19', -1, today)).toBe('2026-11-12');
    expect(shiftPeriodAnchor('monthly', '2026-01-15', -1, today)).toBe('2025-12-01');
    expect(shiftPeriodAnchor('yearly', '2026-11-19', -1, today)).toBe('2025-01-01');
  });

  it('builds exact API query parameters for daily, weekly, monthly, and yearly', () => {
    expect(buildInsightsEndpoint('daily', '2026-11-19')).toBe(
      '/api/v1/insights/summary?period=daily&date=2026-11-19'
    );
    expect(buildInsightsEndpoint('weekly', '2026-11-18')).toBe(
      '/api/v1/insights/summary?period=weekly&date=2026-11-18'
    );
    expect(buildInsightsEndpoint('monthly', '2026-11-19')).toBe(
      '/api/v1/insights/summary?period=monthly&year=2026&month=11'
    );
    expect(buildInsightsEndpoint('yearly', '2024-02-29')).toBe(
      '/api/v1/insights/summary?period=yearly&year=2024'
    );
  });

  it('formats period titles clearly for every period', () => {
    expect(formatPeriodTitle('daily', '2026-11-19')).toBe('19 Nov 2026');
    expect(formatPeriodTitle('weekly', '2026-11-18')).toBe('16–22 Nov 2026');
    expect(formatPeriodTitle('monthly', '2026-11-19')).toBe('November 2026');
    expect(formatPeriodTitle('yearly', '2026-11-19')).toBe('2026');
  });

  it('builds trend bars by purchase for daily, Mon-Sun for weekly, and Jan-Dec for yearly', () => {
    const sample: InsightsSummaryResponse = {
      period_days: 7,
      total_spend_paise: 72000,
      total_waste_paise: 0,
      expiring_soon_count: 0,
      spend_by_category: [],
      spending_trends: [
        { date: '2026-11-16', total_paise: 20000, purchase_count: 1 },
        { date: '2026-11-17', total_paise: 0, purchase_count: 0 },
        { date: '2026-11-18', total_paise: 30000, purchase_count: 1 },
        { date: '2026-11-19', total_paise: 10000, purchase_count: 1 },
        { date: '2026-11-20', total_paise: 0, purchase_count: 0 },
        { date: '2026-11-21', total_paise: 0, purchase_count: 0 },
        { date: '2026-11-22', total_paise: 12000, purchase_count: 1 },
      ],
      period_purchases: [
        {
          id: 'p1',
          merchant_name: 'Thu Mixed Hub',
          purchased_on: '2026-11-19',
          receipt_category: 'mixed',
          total_amount_paise: 10000,
          grocery_paise: 6000,
          outside_food_paise: 4000,
          discount_paise: 0,
          tax_paise: 200,
          additional_charges_paise: 0,
          item_count: 2,
        },
      ],
    };

    const dailyBars = buildTrendBars('daily', sample);
    expect(dailyBars).toEqual([
      { key: 'p1', label: 'Thu Mixed…', amountPaise: 10000, subLabel: 'mixed' },
    ]);

    const weeklyBars = buildTrendBars('weekly', sample);
    expect(weeklyBars).toHaveLength(7);
    expect(weeklyBars[0]).toEqual({ key: '2026-11-16', label: 'Mon', amountPaise: 20000 });
    expect(weeklyBars[6]).toEqual({ key: '2026-11-22', label: 'Sun', amountPaise: 12000 });

    const yearlyBars = buildTrendBars(
      'yearly',
      {
        ...sample,
        spending_trends: [
          { date: '2026-01', total_paise: 10000, purchase_count: 1 },
          { date: '2026-11', total_paise: 77000, purchase_count: 4 },
        ],
      },
      '2026-11-19'
    );
    expect(yearlyBars).toHaveLength(12);
    expect(yearlyBars[0]).toEqual({ key: '2026-01', label: 'Jan', amountPaise: 10000 });
    expect(yearlyBars[1]).toEqual({ key: '2026-02', label: 'Feb', amountPaise: 0 });
    expect(yearlyBars[10]).toEqual({ key: '2026-11', label: 'Nov', amountPaise: 77000 });
    expect(yearlyBars[11]).toEqual({ key: '2026-12', label: 'Dec', amountPaise: 0 });
  });

  it('validates overall and optional category budget inputs without silently modifying limits', () => {
    expect(validateBudgetInputs('10000', '', '')).toEqual({
      ok: true,
      overallPaise: 1000000,
      groceryPaise: null,
      outsidePaise: null,
    });
    expect(validateBudgetInputs('10000', '7000', '3000')).toEqual({
      ok: true,
      overallPaise: 1000000,
      groceryPaise: 700000,
      outsidePaise: 300000,
    });
    expect(validateBudgetInputs('0', '', '').ok).toBe(false);
    expect(validateBudgetInputs('1000', '1200', '').ok).toBe(false);
    expect(validateBudgetInputs('1000', '', '1200').ok).toBe(false);
    expect(validateBudgetInputs('1000', '700', '400').ok).toBe(false);
  });

  it('formats budget status labels and canonical effective_from dates', () => {
    expect(formatBudgetStatusLabel('within_budget')).toBe('Within budget');
    expect(formatBudgetStatusLabel('approaching_limit')).toBe('Approaching limit');
    expect(formatBudgetStatusLabel('fully_used')).toBe('Fully used');
    expect(formatBudgetStatusLabel('exceeded')).toBe('Exceeded');

    expect(getCanonicalPeriodStart('daily', '2026-11-19')).toBe('2026-11-19');
    expect(getCanonicalPeriodStart('weekly', '2026-11-19')).toBe('2026-11-16');
    expect(getCanonicalPeriodStart('monthly', '2026-11-19')).toBe('2026-11-01');
    expect(getCanonicalPeriodStart('yearly', '2026-11-19')).toBe('2026-01-01');
  });
});
