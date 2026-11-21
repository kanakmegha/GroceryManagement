import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useApiClient } from '../auth/useApiClient';
import { useHousehold } from '../auth/HouseholdContext';
import {
  HouseholdRecipePreferences,
  InsightsSummaryResponse,
  InventoryLotsResponse,
  InventorySummaryResponse,
  PurchasesListResponse,
  RecipeRecommendation,
  RecipeRecommendationsResponse,
  ReplenishmentRecommendationsResponse,
} from '../../shared/api/types';
import { householdQueryKeys, invalidateHouseholdData } from '../../shared/api/queryKeys';
import {
  EmptyState,
  ErrorState,
  ScreenContainer,
} from '../../shared/components';
import { formatInrFromPaise } from '../../shared/format/inr';
import {
  formatExpiryBadge,
  formatQuantityWithUnit,
} from '../../shared/format/units';
import { colors, layout, radii, spacing } from '../../shared/theme/tokens';
import {
  traceMount,
  traceRender,
  traceUnmount,
} from '../../shared/diagnostics/perfTrace';
import {
  computeRecipeAvailability,
  formatIngredientDetailPresentation,
  getRecommendationStatusLabel,
  recommendationToDetail,
  RecipeDetailTarget,
  scaleQuantityText,
} from '../recipes/recipeHelpers';

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
] as const;

function getCurrentKolkataYearMonth(): { year: number; month: number } {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
    }).formatToParts(new Date());
    const y = Number(parts.find((p) => p.type === 'year')?.value);
    const m = Number(parts.find((p) => p.type === 'month')?.value);
    if (Number.isFinite(y) && Number.isFinite(m) && m >= 1 && m <= 12) {
      return { year: y, month: m };
    }
  } catch {
    // Fallback to device local date if Intl timezone is unavailable
  }
  const d = new Date();
  return { year: d.getFullYear(), month: d.getMonth() + 1 };
}

export function HomeScreen() {
  traceRender('HomeScreen');
  const router = useRouter();
  const apiClient = useApiClient();
  const queryClient = useQueryClient();
  const { household, user, activeHouseholdId } = useHousehold();

  const currentMonth = getCurrentKolkataYearMonth();
  const [selectedYM, setSelectedYM] = useState<{ year: number; month: number }>(
    currentMonth
  );
  const [selectedDetail, setSelectedDetail] = useState<RecipeDetailTarget | null>(null);
  const [detailServings, setDetailServings] = useState<number>(2);
  const [confirmedByRecipeId, setConfirmedByRecipeId] = useState<
    Record<string, Record<string, boolean>>
  >({});

  const toggleUserIngredientConfirmed = useCallback((recipeKey: string, ingName: string) => {
    const normIng = ingName.trim().toLowerCase();
    setConfirmedByRecipeId((prev) => {
      const currentMap = prev[recipeKey] ?? {};
      const nextVal = !currentMap[normIng];
      return {
        ...prev,
        [recipeKey]: {
          ...currentMap,
          [normIng]: nextVal,
        },
      };
    });
  }, []);
  const [prefsModalVisible, setPrefsModalVisible] = useState(false);
  const [prefDietType, setPrefDietType] = useState<HouseholdRecipePreferences['diet_type']>('any');
  const [prefCuisines, setPrefCuisines] = useState<string[]>([]);
  const [prefMaxPrep, setPrefMaxPrep] = useState<number | null>(null);
  const [prefsSaving, setPrefsSaving] = useState(false);

  const isCurrentMonth =
    selectedYM.year === currentMonth.year && selectedYM.month === currentMonth.month;

  const handlePrevMonth = useCallback(() => {
    setSelectedYM((prev) =>
      prev.month === 1
        ? { year: prev.year - 1, month: 12 }
        : { year: prev.year, month: prev.month - 1 }
    );
  }, []);

  const handleNextMonth = useCallback(() => {
    setSelectedYM((prev) => {
      if (
        prev.year > currentMonth.year ||
        (prev.year === currentMonth.year && prev.month >= currentMonth.month)
      ) {
        return prev;
      }
      return prev.month === 12
        ? { year: prev.year + 1, month: 1 }
        : { year: prev.year, month: prev.month + 1 };
    });
  }, [currentMonth.month, currentMonth.year]);

  useEffect(() => {
    traceMount('HomeScreen');
    return () => traceUnmount('HomeScreen');
  }, []);

  // Independent, non-blocking section queries (Home renders immediately without waiting for LLM)
  const summaryQuery = useQuery({
    queryKey: householdQueryKeys.inventorySummary(activeHouseholdId),
    queryFn: () => apiClient.get<InventorySummaryResponse>('/api/v1/inventory/summary'),
    staleTime: 60_000,
  });

  const expiringQuery = useQuery({
    queryKey: householdQueryKeys.expiringLots(activeHouseholdId),
    queryFn: () =>
      apiClient.get<InventoryLotsResponse>(
        '/api/v1/inventory/lots?status=active&expiring_within_days=5'
      ),
    staleTime: 60_000,
  });

  const monthKey = `${selectedYM.year}-${String(selectedYM.month).padStart(2, '0')}`;
  const insightsQuery = useQuery({
    queryKey: householdQueryKeys.insights(activeHouseholdId, monthKey),
    queryFn: () =>
      apiClient.get<InsightsSummaryResponse>(
        `/api/v1/insights/summary?year=${selectedYM.year}&month=${selectedYM.month}`
      ),
    staleTime: 60_000,
  });

  const purchasesQuery = useQuery({
    queryKey: householdQueryKeys.recentPurchases(activeHouseholdId),
    queryFn: () => apiClient.get<PurchasesListResponse>('/api/v1/purchases?limit=5'),
    staleTime: 60_000,
  });

  const recsQuery = useQuery({
    queryKey: householdQueryKeys.homeRecommendations(activeHouseholdId),
    queryFn: () =>
      apiClient.get<RecipeRecommendationsResponse>(
        '/api/v1/recipes/recommendations?limit=5&include_references=false&mode=progressive'
      ),
    staleTime: 300_000,
  });

  const prefsQuery = useQuery({
    queryKey: ['recipe-preferences', activeHouseholdId],
    queryFn: () => apiClient.get<HouseholdRecipePreferences>('/api/v1/recipes/preferences'),
    enabled: Boolean(activeHouseholdId),
    staleTime: 300_000,
  });

  useEffect(() => {
    if (prefsQuery.data) {
      setPrefDietType(prefsQuery.data.diet_type);
      setPrefCuisines(prefsQuery.data.preferred_cuisines ?? []);
      setPrefMaxPrep(prefsQuery.data.max_prep_minutes ?? null);
    }
  }, [prefsQuery.data]);

  const replenishmentQuery = useQuery({
    queryKey: householdQueryKeys.replenishment(activeHouseholdId),
    queryFn: () =>
      apiClient.get<ReplenishmentRecommendationsResponse>(
        '/api/v1/replenishment/recommendations'
      ),
    staleTime: 60_000,
  });

  const isRefreshing =
    summaryQuery.isRefetching ||
    insightsQuery.isRefetching ||
    purchasesQuery.isRefetching ||
    recsQuery.isRefetching;

  const handleRefresh = useCallback(async () => {
    await invalidateHouseholdData(queryClient, activeHouseholdId);
  }, [activeHouseholdId, queryClient]);

  const inventoryItems = summaryQuery.data?.items ?? [];
  const expiringLots = expiringQuery.data?.items ?? [];
  const insights = insightsQuery.data ?? null;
  const recentPurchases = purchasesQuery.data?.items ?? [];
  const allRecs = recsQuery.data?.items ?? [];

  // Show 3 to 5 context-aware recommendations (Cook Now / Almost Ready / Discover)
  const displayRecs: RecipeRecommendation[] = allRecs.slice(0, 5);

  const handleSavePrefs = useCallback(async () => {
    if (prefsSaving) return;
    setPrefsSaving(true);
    try {
      await apiClient.request('/api/v1/recipes/preferences', {
        method: 'PUT',
        body: {
          timezone: prefsQuery.data?.timezone ?? 'Asia/Kolkata',
          location_name: prefsQuery.data?.location_name ?? null,
          diet_type: prefDietType,
          dietary_restrictions: prefsQuery.data?.dietary_restrictions ?? [],
          preferred_cuisines: prefCuisines,
          preferred_meal_types: prefsQuery.data?.preferred_meal_types ?? [],
          max_prep_minutes: prefMaxPrep,
        },
      });
      await invalidateHouseholdData(queryClient, activeHouseholdId);
      await prefsQuery.refetch();
      await recsQuery.refetch();
      setPrefsModalVisible(false);
    } finally {
      setPrefsSaving(false);
    }
  }, [activeHouseholdId, apiClient, prefCuisines, prefDietType, prefMaxPrep, prefsQuery, prefsSaving, queryClient, recsQuery]);

  const grocerySpend = insights?.grocery_spend_paise ?? 0;
  const outsideSpend = insights?.outside_food_spend_paise ?? 0;
  const totalSpend = insights?.total_spend_paise ?? grocerySpend + outsideSpend;
  const splitBase = Math.max(1, grocerySpend + outsideSpend);
  const groceryPct = Math.round((grocerySpend / splitBase) * 100);
  const outsidePct = totalSpend > 0 ? Math.max(0, 100 - groceryPct) : 0;

  const prevTotal = insights?.previous_period_spend_paise;
  const deltaPaise =
    prevTotal !== null && prevTotal !== undefined ? totalSpend - prevTotal : null;

  const periodLabel = `${MONTH_NAMES[selectedYM.month - 1]} ${selectedYM.year}`;

  const openRecDetail = useCallback((rec: RecipeRecommendation) => {
    const d = recommendationToDetail(rec);
    setSelectedDetail(d);
    setDetailServings(d.servings || 2);
  }, []);

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={isRefreshing}
            onRefresh={() => void handleRefresh()}
          />
        }
      >
        {/* Header */}
        <View style={styles.headerCard}>
          <Text style={styles.greeting}>
            Hello, {user?.email ?? 'Chef'}
          </Text>
          <Text style={styles.householdName}>
            {household?.name ?? 'My Household'}
          </Text>
        </View>

        {/* 1. Calendar-Month Spending Summary & Month Navigation */}
        <View style={styles.section}>
          <View style={styles.sectionHeaderRow}>
            <Text style={styles.sectionTitle}>Monthly Spending</Text>
            <View style={styles.periodRow}>
              <Pressable
                onPress={handlePrevMonth}
                style={styles.periodChip}
                accessibilityRole="button"
                accessibilityLabel="Previous month"
              >
                <Ionicons name="chevron-back" size={16} color={colors.textPrimary} />
              </Pressable>
              <View style={[styles.periodChip, styles.periodChipActive]}>
                <Text style={styles.periodChipTextActive}>{periodLabel}</Text>
              </View>
              <Pressable
                onPress={handleNextMonth}
                disabled={isCurrentMonth}
                style={[styles.periodChip, isCurrentMonth && { opacity: 0.4 }]}
                accessibilityRole="button"
                accessibilityLabel="Next month"
              >
                <Ionicons name="chevron-forward" size={16} color={colors.textPrimary} />
              </Pressable>
            </View>
          </View>

          {insightsQuery.isError ? (
            <ErrorState
              message="Could not load spending summary."
              onRetry={() => void insightsQuery.refetch()}
            />
          ) : (
            <View style={styles.budgetCard}>
              <Text style={styles.budgetPeriodCaption}>
                {periodLabel}
                {insights?.period_start && insights?.period_end
                  ? ` (${insights.period_start} to ${insights.period_end})`
                  : ''}
                {` · ${insights?.purchase_count ?? 0} confirmed`}
              </Text>
              <Text style={styles.budgetHeroAmount}>
                {formatInrFromPaise(totalSpend)}
              </Text>
              {deltaPaise !== null ? (
                <Text style={styles.prevPeriodText}>
                  {deltaPaise >= 0 ? '+' : ''}
                  {formatInrFromPaise(deltaPaise)} vs. previous month (
                  {formatInrFromPaise(prevTotal ?? 0)})
                </Text>
              ) : null}

              {/* Proportional Grocery vs. Outside-Food Bar */}
              {totalSpend > 0 ? (
                <View style={styles.proportionBarTrack}>
                  <View
                    style={[
                      styles.proportionGrocery,
                      { width: `${groceryPct}%` },
                    ]}
                  />
                  <View
                    style={[
                      styles.proportionOutside,
                      { width: `${outsidePct}%` },
                    ]}
                  />
                </View>
              ) : null}

              {/* Readable Legend & Separate Totals */}
              <View style={styles.legendGrid}>
                <View style={styles.legendRow}>
                  <View style={[styles.legendDot, { backgroundColor: colors.primary }]} />
                  <Text style={styles.legendLabel}>
                    Grocery ({groceryPct}%)
                  </Text>
                  <Text style={styles.legendValue}>
                    {formatInrFromPaise(grocerySpend)}
                  </Text>
                </View>
                <View style={styles.legendRow}>
                  <View style={[styles.legendDot, { backgroundColor: '#D97706' }]} />
                  <Text style={styles.legendLabel}>
                    Outside Food ({outsidePct}%)
                  </Text>
                  <Text style={styles.legendValue}>
                    {formatInrFromPaise(outsideSpend)}
                  </Text>
                </View>
              </View>

              {(insights?.total_discounts_paise ?? 0) > 0 ||
              (insights?.total_tax_paise ?? 0) > 0 ||
              (insights?.total_additional_charges_paise ?? 0) > 0 ? (
                <View style={styles.accountingMetaRow}>
                  <Text style={styles.accountingMetaText}>
                    Discounts: -{formatInrFromPaise(insights?.total_discounts_paise ?? 0)}
                  </Text>
                  <Text style={styles.accountingMetaText}>
                    Taxes: {formatInrFromPaise(insights?.total_tax_paise ?? 0)}
                  </Text>
                  <Text style={styles.accountingMetaText}>
                    Charges: {formatInrFromPaise(insights?.total_additional_charges_paise ?? 0)}
                  </Text>
                </View>
              ) : null}
              <Text style={[styles.accountingMetaText, { marginTop: 4 }]}>
                All-time total: {formatInrFromPaise(insights?.all_time_spend_paise ?? 0)} ·
                Grocery: {formatInrFromPaise(insights?.all_time_grocery_spend_paise ?? 0)} ·
                Outside Food: {formatInrFromPaise(insights?.all_time_outside_food_spend_paise ?? 0)}
              </Text>
            </View>
          )}
        </View>

        {/* 2. Pantry Overview & Expiring Items */}
        <View style={styles.section}>
          <View style={styles.sectionHeaderRow}>
            <Text style={styles.sectionTitle}>
              Pantry Overview ({inventoryItems.length} items)
            </Text>
            <Pressable onPress={() => router.push('/(tabs)/inventory')}>
              <Text style={styles.linkText}>View Pantry</Text>
            </Pressable>
          </View>
          {expiringLots.length === 0 ? (
            <Text style={styles.secondaryMetaText}>
              No pantry items expiring in the next 5 days.
            </Text>
          ) : (
            <View style={styles.cardList}>
              {expiringLots.slice(0, 4).map((lot) => {
                const badge = formatExpiryBadge(lot.expires_on);
                return (
                  <View key={lot.id} style={styles.rowCard}>
                    <View style={styles.flex1}>
                      <Text style={styles.rowTitle}>
                        {lot.item_name ?? 'Pantry Item'}
                      </Text>
                      <Text style={styles.rowSubtitle}>
                        {formatQuantityWithUnit(lot.remaining_qty, lot.base_unit)}
                      </Text>
                    </View>
                    <View style={styles.badge}>
                      <Text style={styles.badgeText}>{badge.label}</Text>
                    </View>
                  </View>
                );
              })}
            </View>
          )}
        </View>

        {/* 3. Context-Aware Meal & Recipe Recommendations */}
        <View style={styles.section}>
          <View style={styles.sectionHeaderRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.sectionTitle}>
                {recsQuery.data?.greeting
                  ? `${recsQuery.data.greeting} · ${recsQuery.data.meal_window_label ?? 'Meal Ideas'}`
                  : 'Meal Ideas'}
              </Text>
              {recsQuery.data?.weather_summary ? (
                <Text style={styles.secondaryMetaText}>{recsQuery.data.weather_summary}</Text>
              ) : null}
            </View>
            <View style={{ flexDirection: 'row', gap: spacing.md, alignItems: 'center' }}>
              <Pressable onPress={() => setPrefsModalVisible(true)}>
                <Text style={styles.linkText}>Preferences</Text>
              </Pressable>
              <Pressable onPress={() => router.push('/(tabs)/recipes')}>
                <Text style={styles.linkText}>Ask AI Chef</Text>
              </Pressable>
            </View>
          </View>

          {recsQuery.isLoading ? (
            <View style={styles.recLoadingCard}>
              <ActivityIndicator size="small" color={colors.primary} />
              <Text style={styles.secondaryMetaText}>
                Personalising pantry recommendations...
              </Text>
            </View>
          ) : displayRecs.length === 0 ? (
            <EmptyState
              icon="restaurant-outline"
              title="No pantry-ready recipe yet"
              body="Ask AI Chef for ideas or substitutions based on your preferences."
              actionLabel="Ask AI Chef"
              onAction={() => router.push('/(tabs)/recipes')}
            />
          ) : (
            <View style={styles.cardList}>
              {displayRecs.map((rec) => {
                const availList = (rec.ingredients ?? []).filter(
                  (i) => i.status === 'available'
                );
                const detailPreview = recommendationToDetail(rec);
                const recConfMap = confirmedByRecipeId[rec.recipe_id] ?? {};
                const liveAvail =
                  detailPreview.ingredients.length > 0
                    ? computeRecipeAvailability(detailPreview.ingredients, recConfMap)
                    : null;
                const effectiveType = liveAvail?.recommendationType ?? rec.recommendation_type;
                const categoryLabel = getRecommendationStatusLabel(effectiveType);
                const missingList = liveAvail
                  ? liveAvail.missingEssential
                  : rec.missing_ingredients;
                const insufficientList = liveAvail
                  ? liveAvail.insufficientEssential
                  : (rec.insufficient_ingredients ?? []);
                return (
                  <Pressable
                    key={rec.recipe_id}
                    onPress={() => openRecDetail(rec)}
                    style={styles.recCard}
                    accessibilityRole="button"
                  >
                    <View style={styles.recTopRow}>
                      <Text style={[styles.rowTitle, styles.flex1]}>{rec.title}</Text>
                      <View
                        style={[
                          styles.recTypeBadge,
                          effectiveType === 'COOK_NOW'
                            ? styles.recTypeCookNow
                            : effectiveType === 'COOK_WITH_SUBSTITUTIONS'
                              ? styles.recTypeSub
                              : styles.recTypeShop,
                        ]}
                      >
                        <Text style={styles.recTypeBadgeText}>{categoryLabel}</Text>
                      </View>
                    </View>
                    {rec.description ? (
                      <Text style={styles.rowSubtitle} numberOfLines={2}>
                        {rec.description}
                      </Text>
                    ) : null}
                    <Text style={styles.recMetaText}>
                      {rec.servings} servings · Prep {rec.prep_minutes}m
                      {rec.cook_minutes ? ` · Cook ${rec.cook_minutes}m` : ''}
                    </Text>
                    {rec.why_recommended ? (
                      <Text style={styles.whyText}>{rec.why_recommended}</Text>
                    ) : null}
                    {availList.length > 0 ? (
                      <Text style={styles.availText}>
                        In pantry: {availList.map((a) => a.name).join(', ')}
                      </Text>
                    ) : null}
                    {insufficientList.length > 0 ? (
                      <Text style={styles.missingText}>
                        Insufficient: {insufficientList.join(', ')}
                      </Text>
                    ) : null}
                    {missingList.length > 0 ? (
                      <Text style={styles.missingText}>
                        Missing: {missingList.join(', ')}
                      </Text>
                    ) : null}
                  </Pressable>
                );
              })}
            </View>
          )}
        </View>

        {/* Smart Replenishment Compact Summary */}
        <View style={styles.section}>
          <View style={styles.sectionHeaderRow}>
            <Text style={styles.sectionTitle}>Smart Replenishment</Text>
            <Pressable
              onPress={() =>
                router.push({
                  pathname: '/(tabs)/inventory',
                  params: { tab: 'replenishment' },
                })
              }
            >
              <Text style={styles.linkText}>View All</Text>
            </Pressable>
          </View>
          <View style={styles.rowCard}>
            <View style={styles.flex1}>
              <Text style={styles.rowTitle}>
                {replenishmentQuery.data?.buy_now.length ?? 0} Buy Now ·{' '}
                {replenishmentQuery.data?.buy_soon.length ?? 0} Buy Soon
              </Text>
              <Text style={styles.rowSubtitle}>
                {replenishmentQuery.data?.regular_purchases.length ?? 0} regular items tracked
              </Text>
            </View>
            <Pressable
              onPress={() =>
                router.push({
                  pathname: '/(tabs)/inventory',
                  params: { tab: 'shopping_list' },
                })
              }
            >
              <Text style={styles.linkText}>Shopping List →</Text>
            </Pressable>
          </View>
        </View>

        {/* 4. Recent Purchases (Secondary Section) */}
        <View style={styles.section}>
          <View style={styles.sectionHeaderRow}>
            <Text style={styles.sectionTitle}>Recent Purchases</Text>
            <Pressable onPress={() => router.push('/(tabs)/purchases')}>
              <Text style={styles.linkText}>See All</Text>
            </Pressable>
          </View>
          {recentPurchases.length === 0 ? (
            <Text style={styles.secondaryMetaText}>No recent purchases yet.</Text>
          ) : (
            <View style={styles.cardList}>
              {recentPurchases.slice(0, 3).map((p) => (
                <View key={p.id} style={styles.rowCard}>
                  <View style={styles.flex1}>
                    <Text style={styles.rowTitle}>
                      {p.merchant_name ?? 'Grocery Purchase'}
                    </Text>
                    <Text style={styles.rowSubtitle}>
                      {p.purchased_on ?? ''} · {p.status}
                    </Text>
                  </View>
                  <Text style={styles.amountText}>
                    {formatInrFromPaise(p.total_amount_paise)}
                  </Text>
                </View>
              ))}
            </View>
          )}
        </View>
      </ScrollView>

      {/* Recipe Detail Modal from Home */}
      <Modal
        visible={selectedDetail !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setSelectedDetail(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle} numberOfLines={2}>
              {selectedDetail?.title ?? 'Recipe'}
            </Text>
            <Pressable
              onPress={() => setSelectedDetail(null)}
              style={styles.closeBtn}
              accessibilityRole="button"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.modalContent}>
            {selectedDetail ? (
              <View style={{ gap: spacing.sm }}>
                <Text style={styles.whyText}>{selectedDetail.source_label}</Text>
                {selectedDetail.description ? (
                  <Text style={styles.rowSubtitle}>{selectedDetail.description}</Text>
                ) : null}
                <View style={styles.stepperRow}>
                  <Text style={styles.rowTitle}>Servings:</Text>
                  <Pressable
                    onPress={() => setDetailServings((s) => Math.max(1, s - 1))}
                    style={styles.stepperBtn}
                  >
                    <Ionicons name="remove" size={16} color={colors.textPrimary} />
                  </Pressable>
                  <Text style={styles.rowTitle}>{detailServings}</Text>
                  <Pressable
                    onPress={() => setDetailServings((s) => Math.min(12, s + 1))}
                    style={styles.stepperBtn}
                  >
                    <Ionicons name="add" size={16} color={colors.textPrimary} />
                  </Pressable>
                </View>
                <Text style={styles.sectionTitle}>Ingredients</Text>
                {(() => {
                  const detailKey = selectedDetail.recipe_id ?? selectedDetail.title;
                  const confMap = confirmedByRecipeId[detailKey] ?? {};
                  const liveAvail = computeRecipeAvailability(selectedDetail.ingredients, confMap);
                  return (
                    <View style={{ gap: spacing.xs }}>
                      <Text style={styles.whyText}>
                        Status: {liveAvail.statusLabel}
                        {liveAvail.confirmedCount > 0
                          ? ` (${liveAvail.confirmedCount} confirmed by you — temporary)`
                          : ''}
                      </Text>
                      {selectedDetail.ingredients.map((ing, i) => {
                        const normIng = ing.name.trim().toLowerCase();
                        const userConfirmed = Boolean(confMap[normIng]);
                        const status =
                          ing.status ??
                          (ing.partial ? 'partial' : ing.available ? 'available' : 'missing');
                        const needsConfirm = !userConfirmed && status !== 'available';
                        const pres = formatIngredientDetailPresentation(
                          ing,
                          selectedDetail.servings || 2,
                          detailServings,
                          userConfirmed,
                        );
                        return (
                          <View
                            key={`${detailKey}-${i}`}
                            style={styles.ingredientRowCard}
                          >
                            <View style={styles.ingredientHeaderRow}>
                              <Text style={styles.ingredientNameText}>
                                {ing.name}
                              </Text>
                              {ing.is_optional ? (
                                <View style={styles.optionalPill}>
                                  <Text style={styles.optionalPillText}>Optional</Text>
                                </View>
                              ) : null}
                            </View>
                            <Text style={styles.ingredientQtyDetailText}>
                              {pres.requiredText} · {pres.availabilityDetail}
                            </Text>
                            <View style={styles.ingredientMetaFooterRow}>
                              <View
                                style={[
                                  styles.ingredientStatusPill,
                                  pres.isReady
                                    ? styles.ingredientStatusReady
                                    : styles.ingredientStatusMissing,
                                ]}
                              >
                                <Text
                                  style={[
                                    styles.ingredientStatusText,
                                    pres.isReady
                                      ? styles.availText
                                      : styles.missingText,
                                  ]}
                                >
                                  {pres.isReady ? `✓ ${pres.statusLabel}` : pres.statusLabel}
                                </Text>
                              </View>
                            </View>
                            {needsConfirm ? (
                              <Pressable
                                onPress={() => toggleUserIngredientConfirmed(detailKey, ing.name)}
                                style={styles.ingredientActionBtn}
                                accessibilityRole="button"
                                accessibilityLabel={`I have ${ing.name}`}
                              >
                                <Ionicons
                                  name="checkmark-circle-outline"
                                  size={16}
                                  color={colors.primary}
                                />
                                <Text style={styles.ingredientActionBtnText}>
                                  I have this
                                </Text>
                              </Pressable>
                            ) : userConfirmed ? (
                              <Pressable
                                onPress={() => toggleUserIngredientConfirmed(detailKey, ing.name)}
                                style={styles.ingredientUndoBtn}
                                accessibilityRole="button"
                                accessibilityLabel={`Undo ${ing.name}`}
                              >
                                <Ionicons
                                  name="arrow-undo-outline"
                                  size={16}
                                  color={colors.textSecondary}
                                />
                                <Text style={styles.ingredientUndoBtnText}>
                                  Undo
                                </Text>
                              </Pressable>
                            ) : null}
                          </View>
                        );
                      })}
                    </View>
                  );
                })()}
                <Text style={styles.sectionTitle}>Instructions</Text>
                {selectedDetail.instructions.map((step, i) => (
                  <Text key={i} style={styles.instructionStepText}>
                    {i + 1}. {step}
                  </Text>
                ))}
                {selectedDetail.substitutions.length > 0 ? (
                  <>
                    <Text style={styles.sectionTitle}>Substitutions</Text>
                    {selectedDetail.substitutions.map((s, i) => (
                      <Text key={i} style={styles.rowSubtitle}>
                        • {s}
                      </Text>
                    ))}
                  </>
                ) : null}
              </View>
            ) : null}
          </ScrollView>
          <View style={styles.modalFooter}>
            <Pressable
              onPress={() => {
                if (!selectedDetail) return;
                const detailKey = selectedDetail.recipe_id ?? selectedDetail.title;
                const confMap = confirmedByRecipeId[detailKey] ?? {};
                const confirmedKeys = Object.keys(confMap).filter((k) => confMap[k]);
                const targetPayload = JSON.stringify(selectedDetail);
                const targetId = selectedDetail.recipe_id ?? '';
                setSelectedDetail(null);
                router.push({
                  pathname: '/(tabs)/recipes',
                  params: {
                    recipeId: targetId,
                    openCooking: '1',
                    navSeq: String(Date.now()),
                    recipePayload: targetPayload,
                    confirmedKeys: JSON.stringify(confirmedKeys),
                  },
                });
              }}
              style={styles.primaryBtn}
              accessibilityRole="button"
            >
              <Text style={styles.primaryBtnText}>Let's make it</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
      <Modal
        visible={prefsModalVisible}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setPrefsModalVisible(false)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Meal Preferences</Text>
            <Pressable
              onPress={() => setPrefsModalVisible(false)}
              accessibilityRole="button"
              accessibilityLabel="Close preferences"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.modalContent}>
            <Text style={styles.budgetPeriodCaption}>Diet Type</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs }}>
              {(['any', 'vegetarian', 'vegan', 'eggetarian'] as const).map((dt) => (
                <Pressable
                  key={dt}
                  onPress={() => setPrefDietType(dt)}
                  style={[styles.periodChip, prefDietType === dt && styles.periodChipActive]}
                >
                  <Text style={[styles.periodChipText, prefDietType === dt && styles.periodChipTextActive]}>
                    {dt === 'any' ? 'Any' : dt.charAt(0).toUpperCase() + dt.slice(1)}
                  </Text>
                </Pressable>
              ))}
            </View>

            <Text style={styles.budgetPeriodCaption}>Preferred Cuisines</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs }}>
              {['Indian', 'South Indian', 'North Indian', 'Quick'].map((c) => {
                const selected = prefCuisines.includes(c);
                return (
                  <Pressable
                    key={c}
                    onPress={() =>
                      setPrefCuisines((prev) =>
                        selected ? prev.filter((x) => x !== c) : [...prev, c]
                      )
                    }
                    style={[styles.periodChip, selected && styles.periodChipActive]}
                  >
                    <Text style={[styles.periodChipText, selected && styles.periodChipTextActive]}>
                      {c}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            <Text style={styles.budgetPeriodCaption}>Max Prep Time</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs }}>
              {[null, 10, 15, 25].map((mins) => (
                <Pressable
                  key={String(mins)}
                  onPress={() => setPrefMaxPrep(mins)}
                  style={[styles.periodChip, prefMaxPrep === mins && styles.periodChipActive]}
                >
                  <Text style={[styles.periodChipText, prefMaxPrep === mins && styles.periodChipTextActive]}>
                    {mins === null ? 'Any' : `<= ${mins} min`}
                  </Text>
                </Pressable>
              ))}
            </View>

            <Pressable
              disabled={prefsSaving}
              onPress={() => void handleSavePrefs()}
              style={[styles.periodChip, styles.periodChipActive, { alignItems: 'center', paddingVertical: 12, marginTop: spacing.md }]}
            >
              <Text style={[styles.periodChipText, styles.periodChipTextActive]}>
                {prefsSaving ? 'Saving...' : 'Save Preferences'}
              </Text>
            </Pressable>
          </ScrollView>
        </View>
      </Modal>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  scrollContent: {
    paddingBottom: spacing.xxl,
    gap: spacing.lg,
  },
  headerCard: {
    gap: spacing.xs,
  },
  greeting: {
    fontSize: 14,
    color: colors.textSecondary,
    fontWeight: '500',
  },
  householdName: {
    fontSize: 24,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  section: {
    gap: spacing.sm,
  },
  sectionHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: spacing.sm,
  },
  sectionTitle: {
    fontSize: 17,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  linkText: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.primary,
  },
  periodRow: {
    flexDirection: 'row',
    gap: 6,
  },
  periodChip: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: radii.full,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  periodChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  periodChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  periodChipTextActive: {
    color: colors.textOnPrimary,
  },
  budgetCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    gap: spacing.sm,
  },
  budgetPeriodCaption: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.textSecondary,
    textTransform: 'uppercase',
  },
  budgetHeroAmount: {
    fontSize: 28,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  prevPeriodText: {
    fontSize: 12,
    color: colors.textSecondary,
  },
  proportionBarTrack: {
    height: 10,
    borderRadius: radii.full,
    backgroundColor: colors.border,
    flexDirection: 'row',
    overflow: 'hidden',
  },
  proportionGrocery: {
    backgroundColor: colors.primary,
    height: '100%',
  },
  proportionOutside: {
    backgroundColor: '#D97706',
    height: '100%',
  },
  legendGrid: {
    gap: 6,
  },
  legendRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  legendDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  legendLabel: {
    flex: 1,
    fontSize: 13,
    color: colors.textSecondary,
    fontWeight: '500',
  },
  legendValue: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  accountingMetaRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
    paddingTop: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  accountingMetaText: {
    fontSize: 12,
    color: colors.textMuted,
  },
  cardList: {
    gap: spacing.sm,
  },
  rowCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    minHeight: layout.minTapTarget,
  },
  flex1: {
    flex: 1,
  },
  rowTitle: {
    fontSize: 15,
    fontWeight: '600',
    color: colors.textPrimary,
  },
  rowSubtitle: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  secondaryMetaText: {
    fontSize: 13,
    color: colors.textMuted,
  },
  badge: {
    backgroundColor: colors.warningBg,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radii.full,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.warning,
  },
  recLoadingCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  recCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    gap: 6,
  },
  recTopRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  recTypeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radii.full,
  },
  recTypeCookNow: {
    backgroundColor: '#DCFCE7',
  },
  recTypeSub: {
    backgroundColor: '#FEF3C7',
  },
  recTypeShop: {
    backgroundColor: '#F3F4F6',
  },
  recTypeBadgeText: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  recMetaText: {
    fontSize: 12,
    color: colors.textMuted,
  },
  whyText: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.primary,
  },
  availText: {
    fontSize: 12,
    color: '#15803D',
  },
  missingText: {
    fontSize: 12,
    color: '#B45309',
  },
  amountText: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  modalContainer: {
    flex: 1,
    backgroundColor: colors.background,
  },
  modalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.surface,
  },
  modalTitle: {
    flex: 1,
    fontSize: 18,
    fontWeight: '700',
    color: colors.textPrimary,
    marginRight: spacing.sm,
  },
  closeBtn: {
    padding: 4,
  },
  modalContent: {
    padding: spacing.md,
    paddingBottom: spacing.xxl,
  },
  stepperRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginVertical: spacing.xs,
  },
  stepperBtn: {
    width: 32,
    height: 32,
    borderRadius: radii.sm,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
  },
  modalFooter: {
    padding: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.surface,
  },
  primaryBtn: {
    backgroundColor: colors.primary,
    borderRadius: radii.md,
    paddingVertical: 14,
    alignItems: 'center',
  },
  primaryBtnText: {
    color: colors.textOnPrimary,
    fontSize: 15,
    fontWeight: '700',
  },
  ingredientRowCard: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    padding: spacing.sm,
    gap: 6,
  },
  ingredientHeaderRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: spacing.xs,
  },
  ingredientNameText: {
    fontSize: 15,
    fontWeight: '600',
    color: colors.textPrimary,
    flexShrink: 1,
  },
  optionalPill: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.full,
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
  },
  optionalPillText: {
    fontSize: 11,
    fontWeight: '600',
    color: colors.textMuted,
  },
  ingredientQtyDetailText: {
    fontSize: 13,
    color: colors.textSecondary,
    lineHeight: 18,
  },
  ingredientMetaFooterRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  ingredientStatusPill: {
    borderRadius: radii.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  ingredientStatusReady: {
    backgroundColor: '#DCFCE7',
  },
  ingredientStatusMissing: {
    backgroundColor: '#FEF3C7',
  },
  ingredientStatusText: {
    fontSize: 12,
    fontWeight: '600',
  },
  ingredientActionBtn: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: 40,
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.primary,
    backgroundColor: colors.primaryLight,
  },
  ingredientActionBtnText: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.primary,
  },
  ingredientUndoBtn: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: 40,
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceMuted,
  },
  ingredientUndoBtnText: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  instructionStepText: {
    fontSize: 14,
    lineHeight: 21,
    color: colors.textSecondary,
    marginBottom: 4,
  },
});
