import React, { useCallback, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useHousehold } from '../auth/HouseholdContext';
import { useApiClient } from '../auth/useApiClient';
import { formatInrFromPaise } from '../../shared/format/inr';
import {
  householdQueryKeys,
  invalidateHouseholdData,
} from '../../shared/api/queryKeys';
import type {
  BudgetConfig,
  BudgetListResponse,
  BudgetStatus,
  BudgetStatusLevel,
  InsightsSummaryResponse,
  PeriodPurchase,
} from '../../shared/api/types';
import { colors, layout, radii, spacing } from '../../shared/theme/tokens';
import {
  EmptyState,
  ErrorState,
  ScreenContainer,
} from '../../shared/components';
import {
  type BudgetPeriod,
  buildInsightsEndpoint,
  buildTrendBars,
  formatBudgetStatusLabel,
  formatPeriodTitle,
  getCanonicalPeriodStart,
  getKolkataTodayIso,
  isAtCurrentPeriod,
  shiftPeriodAnchor,
  validateBudgetInputs,
} from './budgetPeriodUtils';

const PERIOD_TABS: Array<{ key: BudgetPeriod; label: string }> = [
  { key: 'daily', label: 'Daily' },
  { key: 'weekly', label: 'Weekly' },
  { key: 'monthly', label: 'Monthly' },
  { key: 'yearly', label: 'Yearly' },
];

type CategoryFilter = 'all' | 'grocery' | 'outside_food';

function statusBadgeStyle(level: BudgetStatusLevel) {
  switch (level) {
    case 'exceeded':
      return { bg: colors.errorBg, fg: colors.error, border: colors.errorBorder };
    case 'fully_used':
    case 'approaching_limit':
      return { bg: colors.warningBg, fg: colors.warning, border: '#FCD34D' };
    case 'within_budget':
    default:
      return { bg: colors.primaryLight, fg: colors.primary, border: '#A7F3D0' };
  }
}

function BudgetStatusBlock({
  title,
  evalStatus,
}: {
  title: string;
  evalStatus: BudgetStatus;
}) {
  const badge = statusBadgeStyle(evalStatus.status);
  const clampedPct = Math.min(100, Math.max(0, evalStatus.percentage_used));
  return (
    <View style={styles.budgetStatusBlock}>
      <View style={styles.budgetStatusHeader}>
        <Text style={styles.budgetStatusTitle}>{title}</Text>
        <View
          style={[
            styles.statusPill,
            { backgroundColor: badge.bg, borderColor: badge.border },
          ]}
        >
          <Text style={[styles.statusPillText, { color: badge.fg }]}>
            {formatBudgetStatusLabel(evalStatus.status)} · {evalStatus.percentage_used.toFixed(1)}%
          </Text>
        </View>
      </View>

      <View style={styles.progressTrack}>
        <View
          style={[
            styles.progressFill,
            {
              width: `${clampedPct}%`,
              backgroundColor:
                evalStatus.status === 'exceeded'
                  ? colors.error
                  : evalStatus.status === 'approaching_limit' || evalStatus.status === 'fully_used'
                  ? '#D97706'
                  : colors.primary,
            },
          ]}
        />
      </View>

      <View style={styles.budgetNumbersRow}>
        <Text style={styles.budgetNumberText}>
          Spent: {formatInrFromPaise(evalStatus.actual_spend_paise)} /{' '}
          {formatInrFromPaise(evalStatus.limit_paise)}
        </Text>
        <Text
          style={[
            styles.budgetRemainingText,
            evalStatus.remaining_paise < 0 && { color: colors.error },
          ]}
        >
          {evalStatus.remaining_paise < 0
            ? `Exceeded by ${formatInrFromPaise(evalStatus.exceeded_paise)}`
            : `Remaining: ${formatInrFromPaise(evalStatus.remaining_paise)}`}
        </Text>
      </View>
    </View>
  );
}

function CategoryDonut({
  groceryPaise,
  outsidePaise,
  totalPaise,
}: {
  groceryPaise: number;
  outsidePaise: number;
  totalPaise: number;
}) {
  const sum = groceryPaise + outsidePaise;
  const groceryDeg = sum > 0 ? Math.round((groceryPaise / sum) * 360) : 0;
  const hasBoth = groceryPaise > 0 && outsidePaise > 0;
  const primaryColor =
    sum === 0
      ? colors.surfaceMuted
      : groceryPaise >= outsidePaise
      ? colors.primary
      : '#D97706';

  return (
    <View style={styles.donutContainer}>
      <View
        style={[
          styles.donutRing,
          {
            borderColor: primaryColor,
            borderTopColor: hasBoth && groceryDeg < 270 ? '#D97706' : primaryColor,
            borderRightColor: hasBoth && groceryDeg < 180 ? '#D97706' : primaryColor,
            borderBottomColor: hasBoth && groceryDeg < 90 ? '#D97706' : primaryColor,
          },
        ]}
      >
        <View style={styles.donutHole}>
          <Text style={styles.donutHoleLabel}>Total</Text>
          <Text style={styles.donutHoleValue} numberOfLines={1}>
            {formatInrFromPaise(totalPaise)}
          </Text>
        </View>
      </View>
    </View>
  );
}

export function BudgetScreen() {
  const router = useRouter();
  const { activeHouseholdId, household } = useHousehold();
  const apiClient = useApiClient();
  const queryClient = useQueryClient();

  const todayIso = useMemo(() => getKolkataTodayIso(), []);
  const [period, setPeriod] = useState<BudgetPeriod>('monthly');
  const [anchorIso, setAnchorIso] = useState<string>(todayIso);
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>('all');

  // Budget configuration modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [configPeriod, setConfigPeriod] = useState<BudgetPeriod>('monthly');
  const [overallInput, setOverallInput] = useState('');
  const [groceryInput, setGroceryInput] = useState('');
  const [outsideInput, setOutsideInput] = useState('');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const atCurrent = isAtCurrentPeriod(period, anchorIso, todayIso);
  const endpoint = useMemo(
    () => buildInsightsEndpoint(period, anchorIso),
    [period, anchorIso]
  );

  const insightsQuery = useQuery({
    queryKey: householdQueryKeys.insights(activeHouseholdId, `${period}:${anchorIso}`),
    queryFn: () => apiClient.get<InsightsSummaryResponse>(endpoint),
    enabled: Boolean(activeHouseholdId),
  });

  const budgetsQuery = useQuery({
    queryKey: [...householdQueryKeys.all(activeHouseholdId), 'budgets'] as const,
    queryFn: () => apiClient.get<BudgetListResponse>('/api/v1/insights/budgets'),
    enabled: Boolean(activeHouseholdId),
  });

  const handleRefresh = useCallback(async () => {
    await invalidateHouseholdData(queryClient, activeHouseholdId);
  }, [activeHouseholdId, queryClient]);

  const handleSelectPeriod = useCallback(
    (nextPeriod: BudgetPeriod) => {
      setPeriod(nextPeriod);
      setAnchorIso(todayIso);
      setCategoryFilter('all');
    },
    [todayIso]
  );

  const handleShift = useCallback(
    (delta: -1 | 1) => {
      setAnchorIso((prev) => shiftPeriodAnchor(period, prev, delta, todayIso));
      setCategoryFilter('all');
    },
    [period, todayIso]
  );

  const prefillModalForPeriod = useCallback(
    (targetPeriod: BudgetPeriod, currentMap?: Record<string, BudgetConfig>) => {
      setConfigPeriod(targetPeriod);
      setSaveError(null);
      const existing = currentMap?.[targetPeriod];
      if (existing) {
        setOverallInput((existing.overall_limit_paise / 100).toString());
        setGroceryInput(
          existing.grocery_limit_paise != null
            ? (existing.grocery_limit_paise / 100).toString()
            : ''
        );
        setOutsideInput(
          existing.outside_food_limit_paise != null
            ? (existing.outside_food_limit_paise / 100).toString()
            : ''
        );
      } else {
        setOverallInput('');
        setGroceryInput('');
        setOutsideInput('');
      }
    },
    []
  );

  const openBudgetModal = useCallback(() => {
    prefillModalForPeriod(period, budgetsQuery.data?.current_by_period);
    setModalOpen(true);
  }, [budgetsQuery.data?.current_by_period, period, prefillModalForPeriod]);

  const handleSaveBudget = useCallback(async () => {
    const check = validateBudgetInputs(overallInput, groceryInput, outsideInput);
    if (!check.ok) {
      setSaveError(check.error);
      return;
    }
    setSaveError(null);
    setIsSaving(true);
    try {
      const effectiveFrom = getCanonicalPeriodStart(
        configPeriod,
        configPeriod === period ? anchorIso : todayIso
      );
      await apiClient.post('/api/v1/insights/budgets', {
        period_type: configPeriod,
        overall_limit_paise: check.overallPaise,
        grocery_limit_paise: check.groceryPaise,
        outside_food_limit_paise: check.outsidePaise,
        effective_from: effectiveFrom,
      });
      await invalidateHouseholdData(queryClient, activeHouseholdId);
      setModalOpen(false);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : 'Failed to save budget configuration.';
      setSaveError(msg);
    } finally {
      setIsSaving(false);
    }
  }, [
    activeHouseholdId,
    anchorIso,
    apiClient,
    configPeriod,
    groceryInput,
    outsideInput,
    overallInput,
    period,
    queryClient,
    todayIso,
  ]);

  const insights = insightsQuery.data ?? null;
  const totalSpend = insights?.total_spend_paise ?? 0;
  const grocerySpend = insights?.grocery_spend_paise ?? 0;
  const outsideSpend = insights?.outside_food_spend_paise ?? 0;
  const unclassifiedSpend = insights?.unclassified_spend_paise ?? 0;
  const discrepancyPaise = insights?.accounting_discrepancy_paise ?? 0;

  const pctBase = totalSpend > 0 ? totalSpend : 1;
  const groceryPct = totalSpend > 0 ? Math.round((grocerySpend / pctBase) * 100) : 0;
  const outsidePct = totalSpend > 0 ? Math.round((outsideSpend / pctBase) * 100) : 0;

  const avgDailyPaise = insights?.average_daily_spend_paise ?? 0;
  const prevTotal = insights?.previous_period_spend_paise;
  const deltaPaise =
    prevTotal !== null && prevTotal !== undefined ? totalSpend - prevTotal : null;

  const periodTitle = formatPeriodTitle(period, anchorIso);
  const trendBars = useMemo(
    () => buildTrendBars(period, insights, anchorIso),
    [anchorIso, insights, period]
  );
  const maxBarPaise = useMemo(
    () => Math.max(1, ...trendBars.map((b) => b.amountPaise)),
    [trendBars]
  );

  const allPurchases: PeriodPurchase[] = insights?.period_purchases ?? [];
  const filteredPurchases = useMemo(() => {
    if (categoryFilter === 'grocery') {
      return allPurchases.filter((p) => p.grocery_paise > 0);
    }
    if (categoryFilter === 'outside_food') {
      return allPurchases.filter((p) => p.outside_food_paise > 0);
    }
    return allPurchases;
  }, [allPurchases, categoryFilter]);

  const budgetEval = insights?.budget ?? null;

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={insightsQuery.isRefetching}
            onRefresh={() => void handleRefresh()}
          />
        }
      >
        <View style={styles.header}>
          <View style={styles.headerRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>Budget & Spending</Text>
              <Text style={styles.subtitle}>
                {household?.name ?? 'My Household'} · Asia/Kolkata
              </Text>
            </View>
            <Pressable
              onPress={openBudgetModal}
              style={styles.headerActionBtn}
              accessibilityRole="button"
              accessibilityLabel={budgetEval ? 'Edit Budget' : 'Set Budget'}
            >
              <Ionicons name="options-outline" size={16} color={colors.textOnPrimary} />
              <Text style={styles.headerActionText}>
                {budgetEval ? 'Edit Budget' : 'Set Budget'}
              </Text>
            </Pressable>
          </View>
        </View>

        {/* Section A — Period selector */}
        <View style={styles.tabRow}>
          {PERIOD_TABS.map((tab) => {
            const active = period === tab.key;
            return (
              <Pressable
                key={tab.key}
                onPress={() => handleSelectPeriod(tab.key)}
                style={[styles.tabChip, active && styles.tabChipActive]}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`${tab.label} period`}
              >
                <Text style={[styles.tabText, active && styles.tabTextActive]}>
                  {tab.label}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {/* Section B — Period navigation */}
        <View style={styles.navBar}>
          <Pressable
            onPress={() => handleShift(-1)}
            style={styles.navBtn}
            accessibilityRole="button"
            accessibilityLabel="Previous period"
          >
            <Ionicons name="chevron-back" size={18} color={colors.textPrimary} />
          </Pressable>
          <View style={styles.navCenter}>
            <Text style={styles.navTitle}>{periodTitle}</Text>
            {insights?.period_start && insights?.period_end ? (
              <Text style={styles.navRange}>
                {insights.period_start === insights.period_end
                  ? insights.period_start
                  : `${insights.period_start} to ${insights.period_end}`}
              </Text>
            ) : null}
          </View>
          <Pressable
            onPress={() => handleShift(1)}
            disabled={atCurrent}
            style={[styles.navBtn, atCurrent && styles.navBtnDisabled]}
            accessibilityRole="button"
            accessibilityLabel="Next period"
          >
            <Ionicons name="chevron-forward" size={18} color={colors.textPrimary} />
          </Pressable>
        </View>

        {insightsQuery.isLoading ? (
          <View style={styles.loadingBox}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.subtitle}>Loading spending analytics...</Text>
          </View>
        ) : insightsQuery.isError ? (
          <ErrorState
            message="Could not load budget analytics."
            onRetry={() => void insightsQuery.refetch()}
          />
        ) : (
          <>
            {/* Section C — Overall spending and budget status */}
            <View style={styles.card}>
              <Text style={styles.caption}>
                Total Spent · {insights?.purchase_count ?? 0} confirmed{' '}
                {(insights?.purchase_count ?? 0) === 1 ? 'purchase' : 'purchases'}
              </Text>
              <Text style={styles.heroAmount}>{formatInrFromPaise(totalSpend)}</Text>

              <View style={styles.subMetricsRow}>
                <Text style={styles.subMetricText}>
                  Avg / day: {formatInrFromPaise(avgDailyPaise)}
                </Text>
                {deltaPaise !== null ? (
                  <Text style={styles.subMetricText}>
                    {deltaPaise >= 0 ? '+' : ''}
                    {formatInrFromPaise(deltaPaise)} vs. prev
                  </Text>
                ) : null}
              </View>

              {budgetEval ? (
                <BudgetStatusBlock
                  title={`${period.charAt(0).toUpperCase() + period.slice(1)} Overall Budget`}
                  evalStatus={budgetEval.overall}
                />
              ) : (
                <View style={styles.noBudgetBanner}>
                  <Text style={styles.noBudgetText}>
                    No {period} budget configured yet.
                  </Text>
                  <Pressable
                    onPress={openBudgetModal}
                    style={styles.setBudgetInlineBtn}
                    accessibilityRole="button"
                    accessibilityLabel="Set Budget"
                  >
                    <Text style={styles.setBudgetInlineText}>Set Budget</Text>
                  </Pressable>
                </View>
              )}

              {/* Discounts, Taxes & Charges */}
              <View style={styles.accountingGrid}>
                <View style={styles.accountingCell}>
                  <Text style={styles.accountingLabel}>Discounts</Text>
                  <Text style={styles.accountingValue}>
                    -{formatInrFromPaise(insights?.total_discounts_paise ?? 0)}
                  </Text>
                </View>
                <View style={styles.accountingCell}>
                  <Text style={styles.accountingLabel}>Taxes</Text>
                  <Text style={styles.accountingValue}>
                    {formatInrFromPaise(insights?.total_tax_paise ?? 0)}
                  </Text>
                </View>
                <View style={styles.accountingCell}>
                  <Text style={styles.accountingLabel}>Charges</Text>
                  <Text style={styles.accountingValue}>
                    {formatInrFromPaise(insights?.total_additional_charges_paise ?? 0)}
                  </Text>
                </View>
              </View>
            </View>

            {/* Section D — Grocery and outside-food spending + Donut chart */}
            <View style={styles.card}>
              <View style={styles.sectionHeaderRow}>
                <Text style={styles.sectionTitle}>Category Split</Text>
                {categoryFilter !== 'all' ? (
                  <Pressable onPress={() => setCategoryFilter('all')}>
                    <Text style={styles.clearFilterText}>Show All</Text>
                  </Pressable>
                ) : null}
              </View>

              <View style={styles.donutRow}>
                <CategoryDonut
                  groceryPaise={grocerySpend}
                  outsidePaise={outsideSpend}
                  totalPaise={totalSpend}
                />
                <View style={{ flex: 1, gap: spacing.sm }}>
                  <Pressable
                    onPress={() =>
                      setCategoryFilter((prev) =>
                        prev === 'grocery' ? 'all' : 'grocery'
                      )
                    }
                    style={[
                      styles.categoryChipCard,
                      categoryFilter === 'grocery' && styles.categoryChipSelected,
                    ]}
                    accessibilityRole="button"
                    accessibilityLabel="Filter by Grocery"
                  >
                    <View style={styles.legendRow}>
                      <View
                        style={[styles.legendDot, { backgroundColor: colors.primary }]}
                      />
                      <Text style={styles.legendLabel}>
                        Grocery ({groceryPct}%)
                      </Text>
                    </View>
                    <Text style={styles.legendValue}>
                      {formatInrFromPaise(grocerySpend)}
                    </Text>
                  </Pressable>

                  <Pressable
                    onPress={() =>
                      setCategoryFilter((prev) =>
                        prev === 'outside_food' ? 'all' : 'outside_food'
                      )
                    }
                    style={[
                      styles.categoryChipCard,
                      categoryFilter === 'outside_food' && styles.categoryChipSelected,
                    ]}
                    accessibilityRole="button"
                    accessibilityLabel="Filter by Outside Food"
                  >
                    <View style={styles.legendRow}>
                      <View
                        style={[styles.legendDot, { backgroundColor: '#D97706' }]}
                      />
                      <Text style={styles.legendLabel}>
                        Outside Food ({outsidePct}%)
                      </Text>
                    </View>
                    <Text style={styles.legendValue}>
                      {formatInrFromPaise(outsideSpend)}
                    </Text>
                  </Pressable>
                </View>
              </View>

              {discrepancyPaise > 0 || unclassifiedSpend > 0 ? (
                <View style={styles.discrepancyBanner}>
                  <Text style={styles.discrepancyText}>
                    Unclassified / accounting discrepancy:{' '}
                    {formatInrFromPaise(Math.max(discrepancyPaise, unclassifiedSpend))}
                  </Text>
                </View>
              ) : null}
            </View>

            {/* Section E — Optional category budgets */}
            <View style={styles.card}>
              <Text style={styles.sectionTitle}>Category Budgets</Text>
              {budgetEval?.grocery ? (
                <BudgetStatusBlock title="Grocery Limit" evalStatus={budgetEval.grocery} />
              ) : (
                <View style={styles.unconfiguredCategoryRow}>
                  <Text style={styles.unconfiguredLabel}>Grocery (no limit set)</Text>
                  <Text style={styles.unconfiguredValue}>
                    {formatInrFromPaise(grocerySpend)}
                  </Text>
                </View>
              )}

              {budgetEval?.outside_food ? (
                <BudgetStatusBlock
                  title="Outside Food Limit"
                  evalStatus={budgetEval.outside_food}
                />
              ) : (
                <View style={styles.unconfiguredCategoryRow}>
                  <Text style={styles.unconfiguredLabel}>Outside Food (no limit set)</Text>
                  <Text style={styles.unconfiguredValue}>
                    {formatInrFromPaise(outsideSpend)}
                  </Text>
                </View>
              )}
            </View>

            {/* Section F — Spending trends */}
            <View style={styles.card}>
              <Text style={styles.sectionTitle}>
                {period === 'daily'
                  ? 'Spending by Purchase'
                  : period === 'weekly'
                  ? 'Daily Totals (Mon–Sun)'
                  : period === 'monthly'
                  ? 'Daily Spending Trend'
                  : 'Monthly Totals (Jan–Dec)'}
              </Text>
              {trendBars.length === 0 || (period === 'daily' && totalSpend === 0) ? (
                <Text style={styles.emptyChartText}>
                  No confirmed purchases in this period.
                </Text>
              ) : (
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={styles.chartRow}
                >
                  {trendBars.map((bar) => {
                    const heightPct =
                      bar.amountPaise > 0
                        ? Math.max(6, Math.round((bar.amountPaise / maxBarPaise) * 100))
                        : 0;
                    return (
                      <View key={bar.key} style={styles.barCol}>
                        <Text style={styles.barAmount}>
                          {bar.amountPaise > 0
                            ? `₹${Math.round(bar.amountPaise / 100)}`
                            : '—'}
                        </Text>
                        <View style={styles.barTrack}>
                          <View
                            style={[styles.barFill, { height: `${heightPct}%` }]}
                          />
                        </View>
                        <Text style={styles.barLabel} numberOfLines={1}>
                          {bar.label}
                        </Text>
                      </View>
                    );
                  })}
                </ScrollView>
              )}
            </View>

            {/* Section G — Purchase breakdown */}
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>
                Purchase Breakdown
                {categoryFilter === 'grocery'
                  ? ' · Grocery'
                  : categoryFilter === 'outside_food'
                  ? ' · Outside Food'
                  : ''}
              </Text>
              {filteredPurchases.length === 0 ? (
                <EmptyState
                  title="No confirmed purchases"
                  body="Confirmed grocery and restaurant receipts for this period will appear here."
                />
              ) : (
                filteredPurchases.map((p) => (
                  <Pressable
                    key={p.id}
                    onPress={() =>
                      router.push({
                        pathname: '/(tabs)/purchases',
                        params: { purchaseId: p.id },
                      })
                    }
                    style={styles.purchaseCard}
                    accessibilityRole="button"
                    accessibilityLabel={`View purchase from ${p.merchant_name}`}
                  >
                    <View style={styles.purchaseTop}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.purchaseMerchant}>{p.merchant_name}</Text>
                        <Text style={styles.purchaseMeta}>
                          {p.purchased_on} · {p.item_count}{' '}
                          {p.item_count === 1 ? 'item' : 'items'} · {p.receipt_category}
                        </Text>
                      </View>
                      <Text style={styles.purchaseTotal}>
                        {formatInrFromPaise(p.total_amount_paise)}
                      </Text>
                    </View>
                    <View style={styles.purchaseDetailRow}>
                      <Text style={styles.purchaseDetailText}>
                        Grocery {formatInrFromPaise(p.grocery_paise)}
                      </Text>
                      {p.outside_food_paise > 0 ? (
                        <Text style={styles.purchaseDetailText}>
                          Outside {formatInrFromPaise(p.outside_food_paise)}
                        </Text>
                      ) : null}
                      {p.discount_paise > 0 ? (
                        <Text style={styles.purchaseDetailText}>
                          Disc -{formatInrFromPaise(p.discount_paise)}
                        </Text>
                      ) : null}
                      {p.tax_paise > 0 ? (
                        <Text style={styles.purchaseDetailText}>
                          Tax {formatInrFromPaise(p.tax_paise)}
                        </Text>
                      ) : null}
                      {p.additional_charges_paise > 0 ? (
                        <Text style={styles.purchaseDetailText}>
                          Charges {formatInrFromPaise(p.additional_charges_paise)}
                        </Text>
                      ) : null}
                    </View>
                  </Pressable>
                ))
              )}
            </View>
          </>
        )}
      </ScrollView>

      {/* Budget Configuration Modal */}
      <Modal
        visible={modalOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setModalOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Configure Budget</Text>
              <Pressable onPress={() => setModalOpen(false)} accessibilityRole="button">
                <Ionicons name="close" size={22} color={colors.textPrimary} />
              </Pressable>
            </View>

            <Text style={styles.inputLabel}>Budget Period</Text>
            <View style={styles.tabRow}>
              {PERIOD_TABS.map((tab) => {
                const active = configPeriod === tab.key;
                return (
                  <Pressable
                    key={tab.key}
                    onPress={() =>
                      prefillModalForPeriod(
                        tab.key,
                        budgetsQuery.data?.current_by_period
                      )
                    }
                    style={[styles.tabChip, active && styles.tabChipActive]}
                  >
                    <Text style={[styles.tabText, active && styles.tabTextActive]}>
                      {tab.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            <Text style={styles.inputLabel}>Overall Limit (₹) *</Text>
            <TextInput
              value={overallInput}
              onChangeText={setOverallInput}
              placeholder="Enter overall limit in ₹"
              keyboardType="decimal-pad"
              style={styles.input}
            />

            <Text style={styles.inputLabel}>Grocery Limit (₹, optional)</Text>
            <TextInput
              value={groceryInput}
              onChangeText={setGroceryInput}
              placeholder="Leave blank for no separate limit"
              keyboardType="decimal-pad"
              style={styles.input}
            />

            <Text style={styles.inputLabel}>Outside-Food Limit (₹, optional)</Text>
            <TextInput
              value={outsideInput}
              onChangeText={setOutsideInput}
              placeholder="Leave blank for no separate limit"
              keyboardType="decimal-pad"
              style={styles.input}
            />

            {saveError ? <Text style={styles.modalError}>{saveError}</Text> : null}

            <View style={styles.modalActions}>
              <Pressable
                onPress={() => setModalOpen(false)}
                style={styles.cancelBtn}
                disabled={isSaving}
              >
                <Text style={styles.cancelBtnText}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={() => void handleSaveBudget()}
                style={styles.saveBtn}
                disabled={isSaving}
              >
                <Text style={styles.saveBtnText}>
                  {isSaving ? 'Saving...' : 'Save Budget'}
                </Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  scrollContent: {
    padding: spacing.lg,
    gap: spacing.lg,
    paddingBottom: 96,
  },
  loadingBox: {
    paddingVertical: spacing.xxl,
    alignItems: 'center',
    gap: spacing.sm,
  },
  header: {
    gap: 2,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  headerActionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: colors.primary,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radii.md,
    minHeight: 40,
  },
  headerActionText: {
    color: colors.textOnPrimary,
    fontSize: 13,
    fontWeight: '600',
  },
  title: {
    fontSize: 22,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  subtitle: {
    fontSize: 13,
    color: colors.textMuted,
  },
  tabRow: {
    flexDirection: 'row',
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.md,
    padding: 4,
    gap: 4,
  },
  tabChip: {
    flex: 1,
    minHeight: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.sm,
  },
  tabChipActive: {
    backgroundColor: colors.primary,
  },
  tabText: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  tabTextActive: {
    color: colors.textOnPrimary,
  },
  navBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
  },
  navBtn: {
    width: layout.minTapTarget,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.sm,
    backgroundColor: colors.surfaceMuted,
  },
  navBtnDisabled: {
    opacity: 0.35,
  },
  navCenter: {
    alignItems: 'center',
    flex: 1,
  },
  navTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  navRange: {
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 2,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.md,
  },
  caption: {
    fontSize: 13,
    color: colors.textMuted,
  },
  heroAmount: {
    fontSize: 30,
    fontWeight: '800',
    color: colors.textPrimary,
  },
  subMetricsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  subMetricText: {
    fontSize: 13,
    color: colors.textSecondary,
    fontWeight: '500',
  },
  budgetStatusBlock: {
    gap: spacing.xs,
    paddingTop: spacing.xs,
  },
  budgetStatusHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  budgetStatusTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textPrimary,
  },
  statusPill: {
    borderWidth: 1,
    borderRadius: radii.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  statusPillText: {
    fontSize: 12,
    fontWeight: '700',
  },
  progressTrack: {
    height: 10,
    borderRadius: radii.full,
    backgroundColor: colors.surfaceMuted,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: radii.full,
  },
  budgetNumbersRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  budgetNumberText: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  budgetRemainingText: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.primary,
  },
  noBudgetBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.md,
    padding: spacing.md,
    gap: spacing.sm,
  },
  noBudgetText: {
    fontSize: 13,
    color: colors.textSecondary,
    flex: 1,
  },
  setBudgetInlineBtn: {
    backgroundColor: colors.primary,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radii.sm,
  },
  setBudgetInlineText: {
    color: colors.textOnPrimary,
    fontSize: 13,
    fontWeight: '600',
  },
  accountingGrid: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: spacing.md,
    gap: spacing.sm,
  },
  accountingCell: {
    flex: 1,
    gap: 2,
  },
  accountingLabel: {
    fontSize: 12,
    color: colors.textMuted,
  },
  accountingValue: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textPrimary,
  },
  sectionHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  clearFilterText: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.primary,
  },
  donutRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  donutContainer: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  donutRing: {
    width: 112,
    height: 112,
    borderRadius: 56,
    borderWidth: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  donutHole: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 4,
  },
  donutHoleLabel: {
    fontSize: 10,
    color: colors.textMuted,
  },
  donutHoleValue: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  categoryChipCard: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    padding: spacing.sm,
    gap: 2,
  },
  categoryChipSelected: {
    borderColor: colors.primary,
    backgroundColor: colors.primaryLight,
  },
  legendRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  legendDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  legendLabel: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  legendValue: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  discrepancyBanner: {
    backgroundColor: colors.warningBg,
    borderRadius: radii.sm,
    padding: spacing.sm,
  },
  discrepancyText: {
    fontSize: 12,
    color: colors.warning,
    fontWeight: '600',
  },
  unconfiguredCategoryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.xs,
  },
  unconfiguredLabel: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  unconfiguredValue: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textPrimary,
  },
  section: {
    gap: spacing.sm,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  emptyChartText: {
    fontSize: 13,
    color: colors.textMuted,
    paddingVertical: spacing.md,
  },
  chartRow: {
    alignItems: 'flex-end',
    gap: spacing.md,
    paddingTop: spacing.sm,
    paddingBottom: spacing.xs,
  },
  barCol: {
    alignItems: 'center',
    width: 44,
    gap: 4,
  },
  barAmount: {
    fontSize: 11,
    color: colors.textSecondary,
    fontWeight: '600',
  },
  barTrack: {
    width: 22,
    height: 96,
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.sm,
    justifyContent: 'flex-end',
    overflow: 'hidden',
  },
  barFill: {
    width: '100%',
    backgroundColor: colors.primary,
    borderRadius: radii.sm,
  },
  barLabel: {
    fontSize: 11,
    color: colors.textMuted,
  },
  purchaseCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: 6,
  },
  purchaseTop: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  purchaseMerchant: {
    fontSize: 15,
    fontWeight: '600',
    color: colors.textPrimary,
  },
  purchaseMeta: {
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 2,
  },
  purchaseTotal: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  purchaseDetailRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
  },
  purchaseDetailText: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'flex-end',
  },
  modalCard: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radii.lg,
    borderTopRightRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.xs,
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  inputLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.textSecondary,
    marginTop: spacing.xs,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: 15,
    color: colors.textPrimary,
    backgroundColor: colors.background,
  },
  modalError: {
    fontSize: 13,
    color: colors.error,
    marginTop: spacing.xs,
  },
  modalActions: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.md,
  },
  cancelBtn: {
    flex: 1,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cancelBtnText: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  saveBtn: {
    flex: 1,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.md,
    backgroundColor: colors.primary,
  },
  saveBtnText: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.textOnPrimary,
  },
});
