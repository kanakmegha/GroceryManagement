import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useHousehold } from "../auth/HouseholdContext";
import { useApiClient } from "../auth/useApiClient";
import {
  householdQueryKeys,
  invalidateHouseholdData,
} from "../../shared/api/queryKeys";
import type {
  FeedbackAction,
  ReplenishmentItem,
  ReplenishmentRecommendationsResponse,
  ShoppingListItem,
  ShoppingListResponse,
} from "../../shared/api/types";
import { EmptyState, ErrorState } from "../../shared/components";
import { colors, radii, spacing } from "../../shared/theme/tokens";
import {
  formatSourceLabel,
  isActionableReplenishmentItem,
  mapRecommendationToShoppingSource,
} from "./replenishmentHelpers";

export function SmartReplenishmentPanel({
  mode,
}: {
  mode: "replenishment" | "shopping_list";
}) {
  const { activeHouseholdId } = useHousehold();
  const apiClient = useApiClient();
  const queryClient = useQueryClient();

  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [manualName, setManualName] = useState("");
  const [manualQty, setManualQty] = useState("");
  const [manualUnit, setManualUnit] = useState("pcs");

  const recsQuery = useQuery({
    queryKey: householdQueryKeys.replenishment(activeHouseholdId),
    queryFn: () =>
      apiClient.get<ReplenishmentRecommendationsResponse>(
        "/api/v1/replenishment/recommendations",
      ),
    enabled: Boolean(activeHouseholdId) && mode === "replenishment",
  });

  const shopQuery = useQuery({
    queryKey: householdQueryKeys.shoppingList(activeHouseholdId),
    queryFn: () => apiClient.get<ShoppingListResponse>("/api/v1/shopping-list"),
    enabled: Boolean(activeHouseholdId) && mode === "shopping_list",
  });

  const handleAddToShoppingList = useCallback(
    async (item: ReplenishmentItem) => {
      setBusyId(item.canonical_item_id);
      setActionMsg(null);
      try {
        await apiClient.post("/api/v1/shopping-list/items", {
          canonical_item_id: item.canonical_item_id,
          item_name: item.item_name,
          quantity: item.recommended_qty && item.recommended_qty > 0
            ? item.recommended_qty
            : 1,
          unit: item.recommended_unit ?? item.base_unit,
          source: mapRecommendationToShoppingSource(item),
          source_ref_id: item.canonical_item_id,
          notes: item.reason,
        });
        await invalidateHouseholdData(queryClient, activeHouseholdId);
        setActionMsg(`Added "${item.item_name}" to Shopping List.`);
      } catch (err) {
        setActionMsg(
          err instanceof Error
            ? err.message
            : "Failed to add to Shopping List.",
        );
      } finally {
        setBusyId(null);
      }
    },
    [activeHouseholdId, apiClient, queryClient],
  );

  const handleFeedback = useCallback(
    async (item: ReplenishmentItem, action: FeedbackAction) => {
      setBusyId(item.canonical_item_id);
      setActionMsg(null);
      try {
        await apiClient.post(
          `/api/v1/replenishment/${item.canonical_item_id}/feedback`,
          {
            action,
            remind_days: action === "remind_later" ? 3 : undefined,
            reason_snapshot: item.reason,
          },
        );
        await invalidateHouseholdData(queryClient, activeHouseholdId);
      } catch (err) {
        setActionMsg(
          err instanceof Error ? err.message : "Could not save feedback.",
        );
      } finally {
        setBusyId(null);
      }
    },
    [activeHouseholdId, apiClient, queryClient],
  );

  const handleRestore = useCallback(
    async (item: ReplenishmentItem) => {
      setBusyId(item.canonical_item_id);
      setActionMsg(null);
      try {
        await apiClient.post(
          `/api/v1/replenishment/${item.canonical_item_id}/restore`,
          {},
        );
        await invalidateHouseholdData(queryClient, activeHouseholdId);
      } catch (err) {
        setActionMsg(
          err instanceof Error ? err.message : "Could not restore item.",
        );
      } finally {
        setBusyId(null);
      }
    },
    [activeHouseholdId, apiClient, queryClient],
  );

  const handleManualAdd = useCallback(async () => {
    const trimmed = manualName.trim();
    if (!trimmed) return;
    setBusyId("manual");
    setActionMsg(null);
    try {
      const parsedQty = Number(manualQty);
      await apiClient.post("/api/v1/shopping-list/items", {
        item_name: trimmed,
        quantity: Number.isFinite(parsedQty) && parsedQty > 0 ? parsedQty : 1,
        unit: manualUnit.trim() || "pcs",
        source: "manual",
      });
      setManualName("");
      setManualQty("");
      await invalidateHouseholdData(queryClient, activeHouseholdId);
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : "Could not add item.");
    } finally {
      setBusyId(null);
    }
  }, [
    activeHouseholdId,
    apiClient,
    manualName,
    manualQty,
    manualUnit,
    queryClient,
  ]);

  const handleUpdateShopStatus = useCallback(
    async (
      item: ShoppingListItem,
      status: "pending" | "purchased" | "dismissed",
    ) => {
      setBusyId(item.id);
      try {
        await apiClient.request(`/api/v1/shopping-list/items/${item.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status }),
        });
        await invalidateHouseholdData(queryClient, activeHouseholdId);
      } finally {
        setBusyId(null);
      }
    },
    [activeHouseholdId, apiClient, queryClient],
  );

  const handleDeleteShopItem = useCallback(
    async (itemId: string) => {
      setBusyId(itemId);
      try {
        await apiClient.request(`/api/v1/shopping-list/items/${itemId}`, {
          method: "DELETE",
        });
        await invalidateHouseholdData(queryClient, activeHouseholdId);
      } finally {
        setBusyId(null);
      }
    },
    [activeHouseholdId, apiClient, queryClient],
  );

  if (mode === "shopping_list") {
    if (shopQuery.isLoading) {
      return (
        <View style={styles.centerBox}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.mutedText}>Loading shopping list...</Text>
        </View>
      );
    }
    if (shopQuery.isError) {
      return (
        <ErrorState
          message="Could not load shopping list."
          onRetry={() => void shopQuery.refetch()}
        />
      );
    }

    const pending = shopQuery.data?.pending ?? [];
    const purchased = shopQuery.data?.purchased ?? [];

    return (
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        {/* Manual Add */}
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Add Item Manually</Text>
          <TextInput
            value={manualName}
            onChangeText={setManualName}
            placeholder="Item name (e.g. Toned Milk)"
            style={styles.input}
          />
          <View style={styles.row}>
            <TextInput
              value={manualQty}
              onChangeText={setManualQty}
              placeholder="Qty (1)"
              keyboardType="decimal-pad"
              style={[styles.input, { flex: 1 }]}
            />
            <TextInput
              value={manualUnit}
              onChangeText={setManualUnit}
              placeholder="Unit (pcs/g/ml)"
              style={[styles.input, { flex: 1 }]}
            />
            <Pressable
              onPress={() => void handleManualAdd()}
              disabled={busyId === "manual"}
              style={styles.primaryBtn}
            >
              <Text style={styles.primaryBtnText}>Add</Text>
            </Pressable>
          </View>
          <Text style={styles.helperNote}>
            Marking an item purchased here only updates your shopping list —
            pantry stock is added only when you confirm a receipt or purchase.
          </Text>
        </View>

        {actionMsg ? <Text style={styles.infoBanner}>{actionMsg}</Text> : null}

        <Text style={styles.sectionTitle}>To Buy ({pending.length})</Text>
        {pending.length === 0
          ? (
            <EmptyState
              title="Shopping list is empty"
              body="Add items from Smart Replenishment, missing recipe ingredients, or manually above."
            />
          )
          : (
            pending.map((item) => (
              <View key={item.id} style={styles.card}>
                <View style={styles.rowBetween}>
                  <Text style={styles.itemTitle}>{item.item_name}</Text>
                  {item.quantity != null
                    ? (
                      <Text style={styles.qtyBadge}>
                        {item.quantity} {item.unit ?? ""}
                      </Text>
                    )
                    : null}
                </View>
                <View style={styles.chipRow}>
                  {item.sources.map((src, idx) => (
                    <View
                      key={`${src.source}-${idx}`}
                      style={styles.sourcePill}
                    >
                      <Text style={styles.sourcePillText}>
                        {formatSourceLabel(src.source)}
                      </Text>
                    </View>
                  ))}
                </View>
                {item.notes
                  ? <Text style={styles.mutedText}>{item.notes}</Text>
                  : null}
                <View style={styles.actionRow}>
                  <Pressable
                    onPress={() =>
                      void handleUpdateShopStatus(item, "purchased")}
                    disabled={busyId === item.id}
                    style={styles.secondaryBtn}
                  >
                    <Text style={styles.secondaryBtnText}>Mark Purchased</Text>
                  </Pressable>
                  <Pressable
                    onPress={() =>
                      void handleUpdateShopStatus(item, "dismissed")}
                    disabled={busyId === item.id}
                    style={styles.ghostBtn}
                  >
                    <Text style={styles.ghostBtnText}>Dismiss</Text>
                  </Pressable>
                  <Pressable
                    onPress={() => void handleDeleteShopItem(item.id)}
                    disabled={busyId === item.id}
                    style={styles.ghostBtn}
                  >
                    <Ionicons
                      name="trash-outline"
                      size={16}
                      color={colors.error}
                    />
                  </Pressable>
                </View>
              </View>
            ))
          )}

        {purchased.length > 0
          ? (
            <>
              <Text style={styles.sectionTitle}>
                Purchased ({purchased.length})
              </Text>
              {purchased.map((item) => (
                <View key={item.id} style={[styles.card, { opacity: 0.75 }]}>
                  <View style={styles.rowBetween}>
                    <Text style={styles.itemTitle}>{item.item_name}</Text>
                    <Pressable
                      onPress={() =>
                        void handleUpdateShopStatus(item, "pending")}
                    >
                      <Text style={styles.linkText}>Move back</Text>
                    </Pressable>
                  </View>
                </View>
              ))}
            </>
          )
          : null}
      </ScrollView>
    );
  }

  // Replenishment mode
  if (recsQuery.isLoading) {
    return (
      <View style={styles.centerBox}>
        <ActivityIndicator size="large" color={colors.primary} />
        <Text style={styles.mutedText}>
          Analyzing purchase & stock patterns...
        </Text>
      </View>
    );
  }
  if (recsQuery.isError) {
    return (
      <ErrorState
        message="Could not load smart replenishment."
        onRetry={() => void recsQuery.refetch()}
      />
    );
  }

  const data = recsQuery.data;
  const buyNowItems = (data?.buy_now ?? []).filter(
    isActionableReplenishmentItem,
  );
  const buySoonItems = (data?.buy_soon ?? []).filter(
    isActionableReplenishmentItem,
  );
  const regularItems = (data?.regular_purchases ?? []).filter(
    isActionableReplenishmentItem,
  );
  const hasAnyActionable = buyNowItems.length > 0 || buySoonItems.length > 0 ||
    regularItems.length > 0;
  const renderRecCard = (item: ReplenishmentItem, isDismissed = false) => {
    const relevantStock = item.ingredient_usable_qty ?? item.usable_qty;
    return (
      <View key={item.canonical_item_id} style={styles.card}>
        <View style={styles.rowBetween}>
          <Text style={styles.itemTitle}>{item.item_name}</Text>
          {item.recommended_qty != null
            ? (
              <Text style={styles.qtyBadge}>
                {item.recommended_qty} {item.recommended_unit ?? item.base_unit}
              </Text>
            )
            : null}
        </View>

        <Text style={styles.reasonText}>{item.reason}</Text>

        <View style={styles.metaGrid}>
          <Text style={styles.metaText}>
            In stock: {relevantStock} {item.base_unit}
          </Text>
        </View>

        {isDismissed
          ? (
            <Pressable
              onPress={() => void handleRestore(item)}
              disabled={busyId === item.canonical_item_id}
              style={styles.secondaryBtn}
            >
              <Text style={styles.secondaryBtnText}>
                Restore Recommendation
              </Text>
            </Pressable>
          )
          : (
            <View style={styles.actionRow}>
              <Pressable
                onPress={() => void handleAddToShoppingList(item)}
                disabled={busyId === item.canonical_item_id}
                style={styles.primaryBtn}
              >
                <Text style={styles.primaryBtnText}>Add to List</Text>
              </Pressable>
              <Pressable
                onPress={() => void handleFeedback(item, "already_have_enough")}
                disabled={busyId === item.canonical_item_id}
                style={styles.ghostBtn}
              >
                <Text style={styles.ghostBtnText}>Have Enough</Text>
              </Pressable>
              <Pressable
                onPress={() => void handleFeedback(item, "remind_later")}
                disabled={busyId === item.canonical_item_id}
                style={styles.ghostBtn}
              >
                <Text style={styles.ghostBtnText}>Remind Later</Text>
              </Pressable>
              <Pressable
                onPress={() => void handleFeedback(item, "dismissed")}
                disabled={busyId === item.canonical_item_id}
                style={styles.ghostBtn}
              >
                <Text style={styles.ghostBtnText}>Dismiss</Text>
              </Pressable>
            </View>
          )}
      </View>
    );
  };

  return (
    <ScrollView
      contentContainerStyle={styles.scrollContent}
      showsVerticalScrollIndicator={false}
    >
      {actionMsg ? <Text style={styles.infoBanner}>{actionMsg}</Text> : null}

      {!hasAnyActionable ? (
        <EmptyState
          title="You're all set!"
          body="There are no groceries that need replenishing right now."
        />
      ) : (
        <>
          {buyNowItems.length > 0 ? (
            <>
              <Text style={styles.sectionTitle}>Buy Now ({buyNowItems.length})</Text>
              {buyNowItems.map((item) => renderRecCard(item))}
            </>
          ) : null}

            {buySoonItems.length > 0
              ? (
                <>
                  <Text style={styles.sectionTitle}>
                    Buy Soon ({buySoonItems.length})
                  </Text>
                  {buySoonItems.map((item) => renderRecCard(item))}
                </>
              )
              : null}

            {regularItems.length > 0
              ? (
                <>
                  <Text style={styles.sectionTitle}>
                    Regular Purchases ({regularItems.length})
                  </Text>
                  {regularItems.map((item) => renderRecCard(item))}
                </>
              )
              : null}
          </>
        )}

      {(data?.dismissed.length ?? 0) > 0
        ? (
          <>
            <Text style={styles.sectionTitle}>
              Dismissed ({data?.dismissed.length})
            </Text>
            {data?.dismissed.map((item) => renderRecCard(item, true))}
          </>
        )
        : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scrollContent: {
    padding: spacing.lg,
    gap: spacing.md,
    paddingBottom: 96,
  },
  centerBox: {
    paddingVertical: spacing.xxl,
    alignItems: "center",
    gap: spacing.sm,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.textPrimary,
    marginTop: spacing.xs,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.sm,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  rowBetween: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.sm,
  },
  itemTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.textPrimary,
    flex: 1,
  },
  confPill: {
    borderRadius: radii.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  confText: {
    fontSize: 11,
    fontWeight: "700",
  },
  reasonText: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  metaGrid: {
    gap: 2,
  },
  metaText: {
    fontSize: 12,
    color: colors.textMuted,
  },
  warningText: {
    fontSize: 12,
    color: colors.warning,
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
    marginTop: 2,
  },
  primaryBtn: {
    backgroundColor: colors.primary,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
  },
  primaryBtnText: {
    color: colors.textOnPrimary,
    fontSize: 12,
    fontWeight: "700",
  },
  secondaryBtn: {
    backgroundColor: colors.primaryLight,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
    alignSelf: "flex-start",
  },
  secondaryBtnText: {
    color: colors.primary,
    fontSize: 12,
    fontWeight: "700",
  },
  ghostBtn: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 8,
  },
  ghostBtnText: {
    color: colors.textSecondary,
    fontSize: 12,
    fontWeight: "600",
  },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 8,
    fontSize: 14,
    color: colors.textPrimary,
    backgroundColor: colors.background,
  },
  helperNote: {
    fontSize: 11,
    color: colors.textMuted,
  },
  mutedText: {
    fontSize: 13,
    color: colors.textMuted,
  },
  infoBanner: {
    backgroundColor: colors.primaryLight,
    color: colors.primary,
    padding: spacing.sm,
    borderRadius: radii.sm,
    fontSize: 13,
    fontWeight: "600",
  },
  qtyBadge: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.primary,
  },
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 4,
  },
  sourcePill: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  sourcePillText: {
    fontSize: 11,
    color: colors.textSecondary,
    fontWeight: "600",
  },
  linkText: {
    fontSize: 12,
    color: colors.primary,
    fontWeight: "600",
  },
});
