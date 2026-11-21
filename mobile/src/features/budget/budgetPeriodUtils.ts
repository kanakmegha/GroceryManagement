import type { BudgetStatusLevel, InsightsSummaryResponse } from '../../shared/api/types';

export type BudgetPeriod = 'daily' | 'weekly' | 'monthly' | 'yearly';

const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MONTH_NUMBERS = ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12'];

export interface TrendBar {
  key: string;
  label: string;
  amountPaise: number;
  subLabel?: string;
}

export function buildTrendBars(
  period: BudgetPeriod,
  insights: InsightsSummaryResponse | null,
  anchorIso?: string
): TrendBar[] {
  if (!insights) return [];

  if (period === 'daily') {
    const purchases = insights.period_purchases ?? [];
    return purchases.map((p) => ({
      key: p.id,
      label: p.merchant_name.length > 10 ? `${p.merchant_name.slice(0, 9)}…` : p.merchant_name,
      amountPaise: p.total_amount_paise,
      subLabel: p.receipt_category,
    }));
  }

  const trends = insights.spending_trends ?? [];
  if (period === 'weekly') {
    if (trends.length === 7) {
      return trends.map((pt, idx) => ({
        key: pt.date,
        label: WEEKDAY_LABELS[idx] ?? pt.date.slice(8, 10),
        amountPaise: pt.total_paise,
      }));
    }
    const { start } = getWeekBounds(anchorIso || insights.period_start || getKolkataTodayIso());
    const byDate = new Map(trends.map((t) => [t.date, t.total_paise]));
    return WEEKDAY_LABELS.map((lbl, idx) => {
      const dIso = addDaysIso(start, idx);
      return {
        key: dIso,
        label: lbl,
        amountPaise: byDate.get(dIso) ?? 0,
      };
    });
  }

  if (period === 'yearly') {
    const yearStr =
      (anchorIso || insights.period_start || '').slice(0, 4) ||
      String(insights.selected_year ?? getKolkataTodayIso().slice(0, 4));
    const byMonth = new Map(trends.map((t) => [t.date, t.total_paise]));
    return MONTH_NUMBERS.map((mm, idx) => {
      const key = `${yearStr}-${mm}`;
      return {
        key,
        label: SHORT_MONTHS[idx],
        amountPaise: byMonth.get(key) ?? 0,
      };
    });
  }

  return trends.map((pt) => ({
    key: pt.date,
    label: pt.date.slice(8, 10),
    amountPaise: pt.total_paise,
  }));
}

export type BudgetValidationResult =
  | {
      ok: true;
      overallPaise: number;
      groceryPaise: number | null;
      outsidePaise: number | null;
    }
  | { ok: false; error: string };

function parsePositiveRupees(raw: string): number | null {
  const cleaned = raw.replace(/,/g, '').trim();
  if (!cleaned) return null;
  const num = Number(cleaned);
  if (!Number.isFinite(num) || num <= 0) return NaN;
  return Math.round(num * 100);
}

export function validateBudgetInputs(
  overallRaw: string,
  groceryRaw: string,
  outsideRaw: string
): BudgetValidationResult {
  const overallPaise = parsePositiveRupees(overallRaw);
  if (overallPaise === null || Number.isNaN(overallPaise) || overallPaise <= 0) {
    return { ok: false, error: 'Overall budget limit must be a positive rupee amount.' };
  }

  const groceryPaise = parsePositiveRupees(groceryRaw);
  if (groceryPaise !== null && (Number.isNaN(groceryPaise) || groceryPaise <= 0)) {
    return { ok: false, error: 'Grocery limit must be a positive rupee amount when provided.' };
  }

  const outsidePaise = parsePositiveRupees(outsideRaw);
  if (outsidePaise !== null && (Number.isNaN(outsidePaise) || outsidePaise <= 0)) {
    return { ok: false, error: 'Outside-food limit must be a positive rupee amount when provided.' };
  }

  if (groceryPaise !== null && groceryPaise > overallPaise) {
    return { ok: false, error: 'Grocery limit cannot exceed the overall budget limit.' };
  }
  if (outsidePaise !== null && outsidePaise > overallPaise) {
    return { ok: false, error: 'Outside-food limit cannot exceed the overall budget limit.' };
  }
  if (
    groceryPaise !== null &&
    outsidePaise !== null &&
    groceryPaise + outsidePaise > overallPaise
  ) {
    return {
      ok: false,
      error: 'Combined grocery and outside-food limits cannot exceed the overall budget limit.',
    };
  }

  return { ok: true, overallPaise, groceryPaise, outsidePaise };
}

export function formatBudgetStatusLabel(status: BudgetStatusLevel): string {
  switch (status) {
    case 'within_budget':
      return 'Within budget';
    case 'approaching_limit':
      return 'Approaching limit';
    case 'fully_used':
      return 'Fully used';
    case 'exceeded':
      return 'Exceeded';
  }
}

export function getCanonicalPeriodStart(period: BudgetPeriod, anchorIso: string): string {
  if (period === 'daily') return anchorIso;
  if (period === 'weekly') return getWeekBounds(anchorIso).start;
  if (period === 'monthly') return `${anchorIso.slice(0, 7)}-01`;
  return `${anchorIso.slice(0, 4)}-01-01`;
}

const MONTH_NAMES = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

const SHORT_MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/** Return YYYY-MM-DD for the current date in Asia/Kolkata. */
export function getKolkataTodayIso(now: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(now);
    const y = parts.find((p) => p.type === 'year')?.value;
    const m = parts.find((p) => p.type === 'month')?.value;
    const d = parts.find((p) => p.type === 'day')?.value;
    if (y && m && d) {
      return `${y}-${m}-${d}`;
    }
  } catch {
    // Fallback below
  }
  const utcMs = now.getTime() + 330 * 60 * 1000;
  return new Date(utcMs).toISOString().slice(0, 10);
}

function parseIsoUtc(iso: string): Date {
  const [y, m, d] = iso.split('-').map((n) => parseInt(n, 10));
  return new Date(Date.UTC(y, (m || 1) - 1, d || 1));
}

function formatUtcIso(dt: Date): string {
  return dt.toISOString().slice(0, 10);
}

export function addDaysIso(iso: string, days: number): string {
  const dt = parseIsoUtc(iso);
  dt.setUTCDate(dt.getUTCDate() + days);
  return formatUtcIso(dt);
}

/** Return Monday..Sunday bounds (inclusive) for the week containing isoDate. */
export function getWeekBounds(isoDate: string): { start: string; end: string } {
  const dt = parseIsoUtc(isoDate);
  const day = dt.getUTCDay(); // 0 = Sun, 1 = Mon, ...
  const diffToMon = day === 0 ? -6 : 1 - day;
  const mon = new Date(dt);
  mon.setUTCDate(dt.getUTCDate() + diffToMon);
  const sun = new Date(mon);
  sun.setUTCDate(mon.getUTCDate() + 6);
  return { start: formatUtcIso(mon), end: formatUtcIso(sun) };
}

export function isAtCurrentPeriod(
  period: BudgetPeriod,
  anchorIso: string,
  todayIso: string
): boolean {
  if (period === 'daily') {
    return anchorIso >= todayIso;
  }
  if (period === 'weekly') {
    return getWeekBounds(anchorIso).start >= getWeekBounds(todayIso).start;
  }
  if (period === 'monthly') {
    return anchorIso.slice(0, 7) >= todayIso.slice(0, 7);
  }
  return anchorIso.slice(0, 4) >= todayIso.slice(0, 4);
}

export function shiftPeriodAnchor(
  period: BudgetPeriod,
  anchorIso: string,
  delta: -1 | 1,
  todayIso: string
): string {
  if (delta > 0 && isAtCurrentPeriod(period, anchorIso, todayIso)) {
    return anchorIso;
  }

  let nextIso = anchorIso;
  if (period === 'daily') {
    nextIso = addDaysIso(anchorIso, delta);
  } else if (period === 'weekly') {
    nextIso = addDaysIso(anchorIso, delta * 7);
  } else if (period === 'monthly') {
    const [y, m] = anchorIso.split('-').map((n) => parseInt(n, 10));
    const dt = new Date(Date.UTC(y, (m || 1) - 1 + delta, 1));
    nextIso = formatUtcIso(dt);
  } else {
    const y = parseInt(anchorIso.slice(0, 4), 10) + delta;
    nextIso = `${y}-01-01`;
  }

  if (delta > 0 && isAtCurrentPeriod(period, nextIso, todayIso)) {
    return todayIso;
  }
  return nextIso;
}

export function formatShortDate(iso: string): string {
  const [y, m, d] = iso.split('-').map((n) => parseInt(n, 10));
  if (!y || !m || !d) return iso;
  return `${d} ${SHORT_MONTHS[m - 1]} ${y}`;
}

export function formatPeriodTitle(period: BudgetPeriod, anchorIso: string): string {
  const [y, m] = anchorIso.split('-').map((n) => parseInt(n, 10));
  if (period === 'daily') {
    return formatShortDate(anchorIso);
  }
  if (period === 'weekly') {
    const { start, end } = getWeekBounds(anchorIso);
    const [sy, sm, sd] = start.split('-').map((n) => parseInt(n, 10));
    const [ey, em, ed] = end.split('-').map((n) => parseInt(n, 10));
    if (sy === ey && sm === em) {
      return `${sd}–${ed} ${SHORT_MONTHS[sm - 1]} ${sy}`;
    }
    return `${sd} ${SHORT_MONTHS[sm - 1]} – ${ed} ${SHORT_MONTHS[em - 1]} ${ey}`;
  }
  if (period === 'monthly') {
    return `${MONTH_NAMES[(m || 1) - 1]} ${y}`;
  }
  return `${y}`;
}

export function buildInsightsEndpoint(period: BudgetPeriod, anchorIso: string): string {
  const [y, m] = anchorIso.split('-').map((n) => parseInt(n, 10));
  if (period === 'daily' || period === 'weekly') {
    return `/api/v1/insights/summary?period=${period}&date=${anchorIso}`;
  }
  if (period === 'monthly') {
    return `/api/v1/insights/summary?period=monthly&year=${y}&month=${m}`;
  }
  return `/api/v1/insights/summary?period=yearly&year=${y}`;
}
