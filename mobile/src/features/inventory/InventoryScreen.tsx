import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect, useLocalSearchParams } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';
import { SmartReplenishmentPanel } from '../replenishment/SmartReplenishmentPanel';
import { useApiClient } from '../auth/useApiClient';
import { useHousehold } from '../auth/HouseholdContext';
import { invalidateHouseholdData } from '../../shared/api/queryKeys';
import {
  AdjustEventType,
  AdjustLotRequest,
  CanonicalIngredientOption,
  CanonicalIngredientsListResponse,
  CatalogItem,
  CatalogItemsResponse,
  IngredientInventorySummaryResponse,
  IngredientSummaryItem,
  InventoryLot,
  InventoryLotsResponse,
  InventorySummaryItem,
  InventorySummaryResponse,
  ItemPriceHistoryResponse,
  PurchaseConfirmResponse,
  PurchaseDraft,
  UnmappedPantryProduct,
} from '../../shared/api/types';
import { ApiClientError } from '../../shared/api/client';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  ScreenContainer,
} from '../../shared/components';
import {
  canonicalUnitToken,
  convertCompatibleUnits,
  createIdempotencyKey,
  displayUnitLabel,
  formatExpiryBadge,
  formatQuantityWithUnit,
  InputUnit,
  normalizeToBaseUnit,
  PantryUnit,
  suggestProductUnits,
} from '../../shared/format/units';
import {
  cleanPantryProductDisplayName,
  formatLotCountLabel,
  isExpiryBadgeRelevant,
} from './pantryDisplayHelpers';
import { colors, layout, radii, spacing } from '../../shared/theme/tokens';

const INPUT_UNITS: InputUnit[] = ['g', 'kg', 'ml', 'l', 'pcs'];

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

export function InventoryScreen() {
  const apiClient = useApiClient();
  const queryClient = useQueryClient();
  const { activeHouseholdId } = useHousehold();
  const { tab: routeTab } = useLocalSearchParams<{ tab?: string }>();
  const [activeSubTab, setActiveSubTab] = useState<'pantry' | 'replenishment' | 'shopping_list'>('pantry');

  useEffect(() => {
    if (routeTab === 'replenishment' || routeTab === 'shopping_list' || routeTab === 'pantry') {
      setActiveSubTab(routeTab);
    }
  }, [routeTab]);

  const [lots, setLots] = useState<InventoryLot[]>([]);
  const [summaryItems, setSummaryItems] = useState<InventorySummaryItem[]>([]);
  const [ingredientItems, setIngredientItems] = useState<IngredientSummaryItem[]>([]);
  const [unmappedProducts, setUnmappedProducts] = useState<UnmappedPantryProduct[]>([]);
  const [mappingReviewItems, setMappingReviewItems] = useState<UnmappedPantryProduct[]>([]);
  const [showMappingReview, setShowMappingReview] = useState(false);
  const [pantryViewMode, setPantryViewMode] = useState<'ingredients' | 'products' | 'identify'>('ingredients');
  const [ingredientCatalog, setIngredientCatalog] = useState<CanonicalIngredientOption[]>([]);
  const [identifyTarget, setIdentifyTarget] = useState<UnmappedPantryProduct | null>(null);
  const [identifyNameDraft, setIdentifyNameDraft] = useState('');
  const [identifyIngSearch, setIdentifyIngSearch] = useState('');
  const [identifyBusy, setIdentifyBusy] = useState(false);
  const [identifyError, setIdentifyError] = useState<string | null>(null);
  // Direct Edit Pantry Item Modal (Phase 5C.4)
  const [editTarget, setEditTarget] = useState<{
    kind: 'ingredient' | 'product';
    id: string;
    name: string;
    baseUnit: PantryUnit;
    lotId?: string;
    originalQty: number;
  } | null>(null);
  const [editNameDraft, setEditNameDraft] = useState('');
  const [editQtyDraft, setEditQtyDraft] = useState('');
  const [editUnitDraft, setEditUnitDraft] = useState<PantryUnit>('g');
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [expandedIngredientIds, setExpandedIngredientIds] = useState<Record<string, boolean>>({});
  const [expandedIngProductIds, setExpandedIngProductIds] = useState<Record<string, boolean>>({});
  const [mappingBusyId, setMappingBusyId] = useState<string | null>(null);
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [expandedItemIds, setExpandedItemIds] = useState<Record<string, boolean>>({});
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const hasLoadedRef = useRef(false);
  const inFlightRef = useRef(false);

  // Manual Add Modal
  const [addModalVisible, setAddModalVisible] = useState(false);
  const [addItemName, setAddItemName] = useState('');
  const [addCategory, setAddCategory] = useState('pantry');
  const [addQtyText, setAddQtyText] = useState('1');
  const [addUnit, setAddUnit] = useState<InputUnit>('pcs');
  const [addExpiresOn, setAddExpiresOn] = useState('');
  const [isAdding, setIsAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // Adjust / Consume / Waste Modal
  const [selectedLot, setSelectedLot] = useState<InventoryLot | null>(null);
  const [adjustType, setAdjustType] =
    useState<AdjustEventType>('ADJUSTMENT_OUT');
  const [adjustQtyText, setAdjustQtyText] = useState('');
  const [adjustNotes, setAdjustNotes] = useState('');
  const [isAdjusting, setIsAdjusting] = useState(false);
  const [adjustError, setAdjustError] = useState<string | null>(null);
  const [priceHistory, setPriceHistory] = useState<ItemPriceHistoryResponse | null>(null);
  const [isLoadingPriceHistory, setIsLoadingPriceHistory] = useState(false);

  const openPriceHistory = useCallback(
    async (canonicalItemId: string) => {
      setIsLoadingPriceHistory(true);
      try {
        const res = await apiClient.get<ItemPriceHistoryResponse>(
          `/api/v1/inventory/items/${canonicalItemId}/price-history`
        );
        setPriceHistory(res);
      } catch {
        setPriceHistory(null);
      } finally {
        setIsLoadingPriceHistory(false);
      }
    },
    [apiClient]
  );

  const loadInventory = useCallback(
    async (refresh = false) => {
      if (inFlightRef.current) {
        return;
      }
      inFlightRef.current = true;
      if (refresh || hasLoadedRef.current) {
        setIsRefreshing(true);
      } else {
        setIsLoading(true);
      }
      setLoadError(null);

      try {
        const [lotsRes, summaryRes, catalogRes, ingSummaryRes, ingCatRes] = await Promise.all([
          apiClient.get<InventoryLotsResponse>(
            '/api/v1/inventory/lots?status=active'
          ),
          apiClient.get<InventorySummaryResponse>('/api/v1/inventory/summary'),
          apiClient.get<CatalogItemsResponse>(
            '/api/v1/catalog/items?limit=100'
          ),
          apiClient
            .get<IngredientInventorySummaryResponse>('/api/v1/inventory/ingredients/summary')
            .catch(() => null),
          apiClient
            .get<CanonicalIngredientsListResponse>('/api/v1/inventory/ingredients/catalog')
            .catch(() => null),
        ]);
        setLots(lotsRes.items ?? []);
        setSummaryItems(summaryRes.items ?? []);
        setCatalogItems(catalogRes.items ?? []);
        setIngredientItems(ingSummaryRes?.ingredients ?? []);
        setUnmappedProducts(ingSummaryRes?.unmapped_products ?? []);
        setMappingReviewItems(ingSummaryRes?.mapping_review_items ?? []);
        setIngredientCatalog(ingCatRes?.items ?? []);
        hasLoadedRef.current = true;
      } catch (err) {
        const message =
          err instanceof ApiClientError
            ? err.message
            : 'Unable to load pantry inventory.';
        setLoadError(message);
      } finally {
        inFlightRef.current = false;
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [apiClient]
  );

  useEffect(() => {
    void loadInventory(false);
  }, [loadInventory, activeHouseholdId]);

  useFocusEffect(
    useCallback(() => {
      if (hasLoadedRef.current) {
        void loadInventory(true);
      }
    }, [loadInventory])
  );

  const itemMetadataById = useMemo(() => {
    const map = new Map<string, { name: string; category: string }>();
    for (const c of catalogItems) {
      map.set(c.id, { name: c.name, category: c.category });
    }
    for (const s of summaryItems) {
      map.set(s.canonical_item_id, { name: s.name, category: s.category });
    }
    return map;
  }, [catalogItems, summaryItems]);

  const lotsByCanonicalItemId = useMemo(() => {
    const map = new Map<string, InventoryLot[]>();
    for (const lot of lots) {
      const existing = map.get(lot.canonical_item_id);
      if (existing) {
        existing.push(lot);
      } else {
        map.set(lot.canonical_item_id, [lot]);
      }
    }
    return map;
  }, [lots]);

  const filteredSummaryItems = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) {
      return summaryItems;
    }
    return summaryItems.filter((item) => {
      const name = item.name.toLowerCase();
      const category = (item.category ?? '').toLowerCase();
      return name.includes(q) || category.includes(q);
    });
  }, [searchQuery, summaryItems]);

  const filteredIngredientItems = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) {
      return ingredientItems;
    }
    return ingredientItems.filter((ing) => {
      const ingMatch =
        ing.name.toLowerCase().includes(q) ||
        ing.category.toLowerCase().includes(q);
      const prodMatch = ing.products.some((p) => p.name.toLowerCase().includes(q));
      return ingMatch || prodMatch;
    });
  }, [ingredientItems, searchQuery]);

  const filteredUnmappedProducts = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) {
      return unmappedProducts;
    }
    return unmappedProducts.filter((u) => {
      const cleaned = cleanPantryProductDisplayName(u.name).title.toLowerCase();
      return (
        cleaned.includes(q) ||
        u.name.toLowerCase().includes(q) ||
        u.category.toLowerCase().includes(q)
      );
    });
  }, [searchQuery, unmappedProducts]);

  const toggleItemExpanded = useCallback((canonicalItemId: string) => {
    setExpandedItemIds((prev) => ({
      ...prev,
      [canonicalItemId]: !prev[canonicalItemId],
    }));
  }, []);

  const handleConfirmSuggestedMapping = useCallback(
    async (item: UnmappedPantryProduct) => {
      if (!item.suggested_ingredient_id || mappingBusyId) {
        return;
      }
      setMappingBusyId(item.canonical_item_id);
      try {
        await apiClient.post('/api/v1/inventory/ingredients/mappings/confirm', {
          canonical_item_id: item.canonical_item_id,
          canonical_ingredient_id: item.suggested_ingredient_id,
        });
        await invalidateHouseholdData(queryClient, activeHouseholdId);
        await loadInventory(true);
      } finally {
        setMappingBusyId(null);
      }
    },
    [activeHouseholdId, apiClient, loadInventory, mappingBusyId, queryClient]
  );

  const handleRejectSuggestedMapping = useCallback(
    async (item: UnmappedPantryProduct) => {
      if (mappingBusyId) {
        return;
      }
      setMappingBusyId(item.canonical_item_id);
      try {
        await apiClient.post('/api/v1/inventory/ingredients/mappings/reject', {
          canonical_item_id: item.canonical_item_id,
        });
        await invalidateHouseholdData(queryClient, activeHouseholdId);
        await loadInventory(true);
      } finally {
        setMappingBusyId(null);
      }
    },
    [activeHouseholdId, apiClient, loadInventory, mappingBusyId, queryClient]
  );

  const openIdentifyModal = useCallback((item: UnmappedPantryProduct) => {
    setIdentifyTarget(item);
    setIdentifyNameDraft(cleanPantryProductDisplayName(item.name).title);
    setIdentifyIngSearch('');
    setIdentifyError(null);
  }, []);

  const handleSaveProductName = useCallback(async () => {
    if (!identifyTarget || identifyBusy) return;
    const trimmed = identifyNameDraft.trim();
    if (!trimmed) {
      setIdentifyError('Enter a product name.');
      return;
    }
    setIdentifyBusy(true);
    setIdentifyError(null);
    try {
      await apiClient.request(`/api/v1/inventory/items/${identifyTarget.canonical_item_id}`, {
        method: 'PATCH',
        body: { name: trimmed },
      });
      await invalidateHouseholdData(queryClient, activeHouseholdId);
      await loadInventory(true);
      setIdentifyTarget((prev) => (prev ? { ...prev, name: trimmed } : null));
    } catch (err) {
      setIdentifyError(err instanceof ApiClientError ? err.message : 'Failed to update name.');
    } finally {
      setIdentifyBusy(false);
    }
  }, [activeHouseholdId, apiClient, identifyBusy, identifyNameDraft, identifyTarget, loadInventory, queryClient]);

  const handleConfirmIngredientId = useCallback(
    async (ingredientId: string) => {
      if (!identifyTarget || identifyBusy) return;
      setIdentifyBusy(true);
      setIdentifyError(null);
      try {
        await apiClient.post('/api/v1/inventory/ingredients/mappings/confirm', {
          canonical_item_id: identifyTarget.canonical_item_id,
          canonical_ingredient_id: ingredientId,
        });
        await invalidateHouseholdData(queryClient, activeHouseholdId);
        await loadInventory(true);
        setIdentifyTarget(null);
      } catch (err) {
        setIdentifyError(err instanceof ApiClientError ? err.message : 'Failed to confirm ingredient.');
      } finally {
        setIdentifyBusy(false);
      }
    },
    [activeHouseholdId, apiClient, identifyBusy, identifyTarget, loadInventory, queryClient]
  );

  const handleKeepUnmapped = useCallback(async () => {
    if (!identifyTarget || identifyBusy) return;
    setIdentifyBusy(true);
    setIdentifyError(null);
    try {
      if (identifyTarget.suggestion_status === 'unconfirmed_suggestion') {
        await apiClient.post('/api/v1/inventory/ingredients/mappings/reject', {
          canonical_item_id: identifyTarget.canonical_item_id,
        });
        await invalidateHouseholdData(queryClient, activeHouseholdId);
        await loadInventory(true);
      }
      setIdentifyTarget(null);
    } catch (err) {
      setIdentifyError(err instanceof ApiClientError ? err.message : 'Failed to update status.');
    } finally {
      setIdentifyBusy(false);
    }
  }, [activeHouseholdId, apiClient, identifyBusy, identifyTarget, loadInventory, queryClient]);

  const filteredCatalogOptions = useMemo(() => {
    const q = identifyIngSearch.trim().toLowerCase();
    const list = q
      ? ingredientCatalog.filter(
          (c) => c.name.toLowerCase().includes(q) || c.category.toLowerCase().includes(q)
        )
      : ingredientCatalog;
    return list.slice(0, 12);
  }, [identifyIngSearch, ingredientCatalog]);

  const openEditIngredient = useCallback((ing: IngredientSummaryItem) => {
    const rawQty = Number(ing.total_remaining_qty ?? 0);
    const stdUnit = displayUnitLabel(ing.standard_unit || 'g');
    const displayUnit: PantryUnit =
      stdUnit === 'g' && rawQty >= 1000
        ? 'kg'
        : stdUnit === 'ml' && rawQty >= 1000
          ? 'L'
          : stdUnit;
    const displayQty = convertCompatibleUnits(rawQty, stdUnit, displayUnit) ?? rawQty;
    setEditTarget({
      kind: 'ingredient',
      id: ing.canonical_ingredient_id,
      name: ing.name,
      baseUnit: stdUnit,
      originalQty: rawQty,
    });
    setEditNameDraft(ing.name);
    setEditQtyDraft(String(Number(displayQty.toFixed(3))));
    setEditUnitDraft(displayUnit);
    setEditError(null);
  }, []);

  const openEditProduct = useCallback(
    (u: UnmappedPantryProduct) => {
      const rawQty = Number(u.total_remaining_qty ?? 0);
      const stdUnit = displayUnitLabel(u.base_unit || 'pcs');
      const displayUnit: PantryUnit =
        stdUnit === 'g' && rawQty >= 1000
          ? 'kg'
          : stdUnit === 'ml' && rawQty >= 1000
            ? 'L'
            : stdUnit;
      const displayQty = convertCompatibleUnits(rawQty, stdUnit, displayUnit) ?? rawQty;
      const firstLot = (lotsByCanonicalItemId.get(u.canonical_item_id) ?? [])[0];
      const cleanedTitle = cleanPantryProductDisplayName(u.name).title;
      setEditTarget({
        kind: 'product',
        id: u.canonical_item_id,
        name: cleanedTitle,
        baseUnit: stdUnit,
        lotId: firstLot?.id,
        originalQty: rawQty,
      });
      setEditNameDraft(cleanedTitle);
      setEditQtyDraft(String(Number(displayQty.toFixed(3))));
      setEditUnitDraft(displayUnit);
      setEditError(null);
    },
    [lotsByCanonicalItemId]
  );

  const handleSelectEditUnit = useCallback(
    (nextUnit: PantryUnit) => {
      if (!editTarget) return;
      const currentQty = Number(editQtyDraft);
      const convertedToBase = convertCompatibleUnits(1, nextUnit, editTarget.baseUnit);
      if (convertedToBase === null) {
        setEditUnitDraft(nextUnit);
        setEditError(
          `Cannot automatically convert '${editTarget.baseUnit}' to '${nextUnit}' without a known package size or conversion factor. Choose a compatible unit.`
        );
        return;
      }
      if (Number.isFinite(currentQty) && currentQty >= 0) {
        const convertedDraft = convertCompatibleUnits(currentQty, editUnitDraft, nextUnit);
        if (convertedDraft !== null) {
          setEditQtyDraft(String(Number(convertedDraft.toFixed(3))));
        }
      }
      setEditUnitDraft(nextUnit);
      setEditError(null);
    },
    [editQtyDraft, editTarget, editUnitDraft]
  );

  const handleSaveEditPantryItem = useCallback(async () => {
    if (!editTarget || editBusy) return;
    const trimmedName = editNameDraft.trim();
    if (!trimmedName) {
      setEditError('Enter a valid name.');
      return;
    }
    const parsedQty = Number(editQtyDraft);
    if (!Number.isFinite(parsedQty) || parsedQty < 0) {
      setEditError('Enter a valid non-negative quantity.');
      return;
    }
    setEditBusy(true);
    setEditError(null);
    try {
      if (editTarget.kind === 'ingredient') {
        const compatible = convertCompatibleUnits(parsedQty, editUnitDraft, editTarget.baseUnit);
        if (compatible === null) {
          setEditError(
            `Cannot convert '${editUnitDraft}' to '${editTarget.baseUnit}' without a valid conversion factor or explicit stock correction.`
          );
          setEditBusy(false);
          return;
        }
        await apiClient.request(`/api/v1/inventory/ingredients/${editTarget.id}`, {
          method: 'PATCH',
          body: {
            name: trimmedName,
            target_quantity: parsedQty,
            unit: canonicalUnitToken(editUnitDraft),
          },
        });
      } else {
        const convertedQty = convertCompatibleUnits(parsedQty, editUnitDraft, editTarget.baseUnit);
        if (convertedQty === null) {
          setEditError(
            `Unit '${editUnitDraft}' is incompatible with '${editTarget.baseUnit}' without a known package size or conversion factor.`
          );
          setEditBusy(false);
          return;
        }
        if (trimmedName !== editTarget.name) {
          await apiClient.request(`/api/v1/inventory/items/${editTarget.id}`, {
            method: 'PATCH',
            body: { name: trimmedName },
          });
        }
        const delta = Number((convertedQty - editTarget.originalQty).toFixed(3));
        if (delta !== 0) {
          if (!editTarget.lotId) {
            setEditError('No active lot available to adjust.');
            setEditBusy(false);
            return;
          }
          await apiClient.post(`/api/v1/inventory/lots/${editTarget.lotId}/adjust`, {
            event_type: delta > 0 ? 'ADJUSTMENT_IN' : 'ADJUSTMENT_OUT',
            quantity_delta: delta,
            notes: 'User-requested pantry quantity correction',
          });
        }
      }
      await invalidateHouseholdData(queryClient, activeHouseholdId);
      await loadInventory(true);
      setEditTarget(null);
    } catch (err) {
      setEditError(err instanceof ApiClientError ? err.message : 'Could not save changes.');
    } finally {
      setEditBusy(false);
    }
  }, [activeHouseholdId, apiClient, editBusy, editNameDraft, editQtyDraft, editTarget, editUnitDraft, loadInventory, queryClient]);

  const openAdjustModal = useCallback((lot: InventoryLot) => {
    setSelectedLot(lot);
    setAdjustType('ADJUSTMENT_OUT');
    setAdjustQtyText('');
    setAdjustNotes('');
    setAdjustError(null);
  }, []);

  const handleManualAdd = useCallback(async () => {
    if (isAdding) {
      return;
    }
    setAddError(null);

    const trimmedName = addItemName.trim();
    if (!trimmedName) {
      setAddError('Enter an item name.');
      return;
    }

    const qtyNum = Number(addQtyText);
    if (!Number.isFinite(qtyNum) || qtyNum <= 0) {
      setAddError('Enter a valid quantity greater than zero.');
      return;
    }

    const expiresTrimmed = addExpiresOn.trim();
    if (
      expiresTrimmed.length > 0 &&
      !/^\d{4}-\d{2}-\d{2}$/.test(expiresTrimmed)
    ) {
      setAddError('Expiry date must use YYYY-MM-DD format.');
      return;
    }

    const normalized = normalizeToBaseUnit(qtyNum, addUnit);
    const matchedCatalog = catalogItems.find(
      (c) => c.name.toLowerCase() === trimmedName.toLowerCase()
    );

    setIsAdding(true);
    try {
      let canonicalItemId = matchedCatalog?.id ?? null;
      if (!canonicalItemId) {
        const createdCatalog = await apiClient.post<CatalogItem>(
          '/api/v1/catalog/items',
          {
            name: trimmedName,
            category: addCategory.trim() || 'pantry',
            default_base_unit: normalized.baseUnit,
          }
        );
        canonicalItemId = createdCatalog.id;
      }

      try {
        await apiClient.post('/api/v1/inventory/lots', {
          canonical_item_id: canonicalItemId,
          remaining_qty: normalized.qty,
          base_unit: normalized.baseUnit,
          expires_on: expiresTrimmed.length > 0 ? expiresTrimmed : null,
        });
      } catch (lotErr) {
        if (
          lotErr instanceof ApiClientError &&
          (lotErr.status === 404 || lotErr.status === 405)
        ) {
          const draft = await apiClient.post<PurchaseDraft>(
            '/api/v1/purchases/drafts',
            {
              merchant_name: 'Manual Pantry Entry',
              purchased_on: todayIsoDate(),
              total_amount_paise: 0,
              items: [
                {
                  canonical_item_id: canonicalItemId,
                  display_name: trimmedName,
                  normalized_qty: normalized.qty,
                  base_unit: normalized.baseUnit,
                  line_total_paise: 0,
                  expires_on: expiresTrimmed.length > 0 ? expiresTrimmed : null,
                },
              ],
            }
          );
          await apiClient.post<PurchaseConfirmResponse>(
            `/api/v1/purchases/drafts/${draft.id}/confirm`,
            {},
            {
              headers: {
                'Idempotency-Key': createIdempotencyKey('manual-add'),
              },
            }
          );
        } else {
          throw lotErr;
        }
      }

      setAddModalVisible(false);
      setAddItemName('');
      setAddQtyText('1');
      setAddExpiresOn('');
      await invalidateHouseholdData(queryClient, activeHouseholdId);
      await loadInventory(true);
    } catch (err) {
      const message =
        err instanceof ApiClientError
          ? err.message
          : 'Could not add item to inventory.';
      setAddError(message);
    } finally {
      setIsAdding(false);
    }
  }, [
    addCategory,
    addExpiresOn,
    addItemName,
    addQtyText,
    addUnit,
    apiClient,
    catalogItems,
    isAdding,
    loadInventory,
  ]);

  const handleAdjustSubmit = useCallback(async () => {
    if (!selectedLot || isAdjusting) {
      return;
    }
    setAdjustError(null);

    const qtyNum = Number(adjustQtyText);
    if (!Number.isFinite(qtyNum) || qtyNum <= 0) {
      setAdjustError('Enter a positive quantity.');
      return;
    }

    const signedDelta =
      adjustType === 'ADJUSTMENT_IN'
        ? qtyNum.toFixed(3)
        : (-qtyNum).toFixed(3);

    setIsAdjusting(true);
    try {
      const payload: AdjustLotRequest = {
        event_type: adjustType,
        quantity_delta: signedDelta,
        notes: adjustNotes.trim() || undefined,
      };
      await apiClient.post(
        `/api/v1/inventory/lots/${selectedLot.id}/adjust`,
        payload,
        {
          headers: {
            'Idempotency-Key': createIdempotencyKey('adjust-lot'),
          },
        }
      );
      setSelectedLot(null);
      await invalidateHouseholdData(queryClient, activeHouseholdId);
      await loadInventory(true);
    } catch (err) {
      const message =
        err instanceof ApiClientError
          ? err.message
          : 'Failed to update inventory lot.';
      setAdjustError(message);
    } finally {
      setIsAdjusting(false);
    }
  }, [
    adjustNotes,
    adjustQtyText,
    adjustType,
    apiClient,
    isAdjusting,
    loadInventory,
    selectedLot,
  ]);

  if (isLoading) {
    return (
      <ScreenContainer>
        <LoadingState message="Loading pantry lots..." />
      </ScreenContainer>
    );
  }

  if (loadError) {
    return (
      <ScreenContainer>
        <ErrorState
          message={loadError}
          onRetry={() => void loadInventory(false)}
        />
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <View style={styles.headerRow}>
        <View style={styles.headerTextGroup}>
          <Text style={styles.title}>Inventory</Text>
          <Text style={styles.subtitle}>
            {summaryItems.length} {summaryItems.length === 1 ? 'product' : 'products'} · {lots.length} active {lots.length === 1 ? 'lot' : 'lots'}
          </Text>
        </View>
        <Pressable
          onPress={() => {
            setAddError(null);
            setAddModalVisible(true);
          }}
          style={({ pressed }) => [
            styles.addButton,
            pressed && styles.buttonPressed,
          ]}
          accessibilityRole="button"
          accessibilityLabel="Add item to inventory"
        >
          <Ionicons name="add" size={20} color={colors.textOnPrimary} />
          <Text style={styles.addButtonText}>Add</Text>
        </Pressable>
      </View>

      <View style={styles.subTabRow}>
        {(
          [
            { key: 'pantry', label: 'Pantry Stock' },
            { key: 'replenishment', label: 'Smart Replenish' },
            { key: 'shopping_list', label: 'Shopping List' },
          ] as const
        ).map((t) => {
          const active = activeSubTab === t.key;
          return (
            <Pressable
              key={t.key}
              onPress={() => setActiveSubTab(t.key)}
              style={[styles.subTabChip, active && styles.subTabChipActive]}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
            >
              <Text style={[styles.subTabText, active && styles.subTabTextActive]}>
                {t.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {activeSubTab === 'replenishment' ? (
        <SmartReplenishmentPanel mode="replenishment" />
      ) : activeSubTab === 'shopping_list' ? (
        <SmartReplenishmentPanel mode="shopping_list" />
      ) : (
        <>
      <View style={styles.subTabRow}>
        {(
          [
            { key: 'ingredients', label: 'Pantry' },
            { key: 'identify', label: `To Identify (${unmappedProducts.length})` },
          ] as const
        ).map((m) => {
          const active = pantryViewMode === m.key;
          return (
            <Pressable
              key={m.key}
              onPress={() => setPantryViewMode(m.key)}
              style={[styles.subTabChip, active && styles.subTabChipActive]}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
            >
              <Text style={[styles.subTabText, active && styles.subTabTextActive]}>
                {m.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <View style={styles.searchBar}>
        <Ionicons name="search-outline" size={18} color={colors.textMuted} />
        <TextInput
          value={searchQuery}
          onChangeText={setSearchQuery}
          placeholder="Search pantry items or category..."
          placeholderTextColor={colors.textMuted}
          style={styles.searchInput}
          accessibilityLabel="Search inventory"
        />
        {searchQuery.length > 0 ? (
          <Pressable
            onPress={() => setSearchQuery('')}
            accessibilityRole="button"
            accessibilityLabel="Clear search"
          >
            <Ionicons name="close-circle" size={18} color={colors.textMuted} />
          </Pressable>
        ) : null}
      </View>

      <ScrollView
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={isRefreshing}
            onRefresh={() => void loadInventory(true)}
            tintColor={colors.primary}
          />
        }
        contentContainerStyle={
          (pantryViewMode === 'products'
            ? filteredSummaryItems.length === 0
            : filteredIngredientItems.length === 0 &&
              filteredUnmappedProducts.length === 0 &&
              mappingReviewItems.length === 0)
            ? styles.emptyListContainer
            : styles.listContent
        }
      >
        {pantryViewMode === 'identify' ? (
          filteredUnmappedProducts.length === 0 ? (
            <EmptyState
              icon="checkmark-circle-outline"
              title="All products identified"
              body="Every active pantry product has a confirmed ingredient mapping."
            />
          ) : (
            <View style={{ gap: spacing.sm }}>
              <Text style={styles.lotCategory}>
                Products to Identify — confirm ingredient, edit name, or verify measurement.
              </Text>
              {filteredUnmappedProducts.map((u) => {
                const { title } = cleanPantryProductDisplayName(u.name);
                const formattedTotal = formatQuantityWithUnit(
                  u.total_remaining_qty,
                  u.base_unit as InputUnit
                );
                const isBusy = mappingBusyId === u.canonical_item_id;
                return (
                  <View key={u.canonical_item_id} style={styles.lotCard}>
                    <View style={styles.lotTopRow}>
                      <View style={styles.lotTitleGroup}>
                        <Text style={styles.lotName}>{title}</Text>
                        <Text style={styles.lotCategory}>
                          {formattedTotal}
                          {u.suggested_ingredient_name
                            ? ` · Suggested: ${u.suggested_ingredient_name}`
                            : ''}
                        </Text>
                      </View>
                    </View>
                    <View style={{ flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs }}>
                      {u.suggested_ingredient_id ? (
                        <Pressable
                          disabled={isBusy}
                          onPress={() => void handleConfirmSuggestedMapping(u)}
                          style={[styles.subTabChip, styles.subTabChipActive, { paddingHorizontal: spacing.md, flex: 0 }]}
                          accessibilityRole="button"
                          accessibilityLabel={`Confirm ${title} as ${u.suggested_ingredient_name ?? ''}`}
                        >
                          <Text style={[styles.subTabText, styles.subTabTextActive]}>
                            {isBusy ? 'Saving...' : `Confirm ${u.suggested_ingredient_name ?? ''}`}
                          </Text>
                        </Pressable>
                      ) : null}
                      <Pressable
                        onPress={() => openIdentifyModal(u)}
                        style={[styles.subTabChip, { paddingHorizontal: spacing.md, flex: 0 }]}
                        accessibilityRole="button"
                        accessibilityLabel={`Identify or edit ${title}`}
                      >
                        <Text style={styles.subTabText}>Identify / Edit</Text>
                      </Pressable>
                    </View>
                  </View>
                );
              })}
            </View>
          )
        ) : pantryViewMode === 'ingredients' ? (
          filteredIngredientItems.length === 0 &&
          filteredUnmappedProducts.length === 0 &&
          mappingReviewItems.length === 0 ? (
            <EmptyState
              icon="nutrition-outline"
              title="No pantry ingredients yet"
              body="Confirm a receipt or add stock to see your pantry ingredients here."
              actionLabel="Add Lot"
              onAction={() => setAddModalVisible(true)}
            />
          ) : (
            <>
              {filteredIngredientItems.map((ing) => {
                const isIngExpanded = Boolean(expandedIngredientIds[ing.canonical_ingredient_id]);
                const expiry = formatExpiryBadge(ing.earliest_expires_on);
                const totalLabel =
                  ing.unit_completeness_status === 'complete' && ing.total_remaining_qty !== null
                    ? formatQuantityWithUnit(ing.total_remaining_qty, ing.standard_unit as InputUnit)
                    : ing.unit_completeness_status === 'empty'
                      ? `0 ${ing.standard_unit}`
                      : `Incomplete (${ing.unit_groups
                          .map((g) => `${g.total_qty} ${g.unit}`)
                          .join(' + ')})`;

                return (
                  <Pressable
                    key={ing.canonical_ingredient_id}
                    onPress={() =>
                      setExpandedIngredientIds((prev) => ({
                        ...prev,
                        [ing.canonical_ingredient_id]: !prev[ing.canonical_ingredient_id],
                      }))
                    }
                    style={({ pressed }) => [
                      styles.lotCard,
                      pressed && styles.lotCardPressed,
                    ]}
                    accessibilityRole="button"
                    accessibilityLabel={`${ing.name}, ${totalLabel}`}
                  >
                    <View style={styles.lotTopRow}>
                      <View style={styles.lotTitleGroup}>
                        <Text style={styles.lotName}>{ing.name}</Text>
                        <Text style={styles.lotCategory}>
                          {formatLotCountLabel(ing.lot_count)}
                        </Text>
                      </View>
                      {ing.unit_completeness_status === 'incomplete' ? (
                        <View style={[styles.expiryBadge, styles.expirySoon]}>
                          <Text style={[styles.expiryBadgeText, styles.expirySoonText]}>
                            Mixed Units
                          </Text>
                        </View>
                      ) : isExpiryBadgeRelevant(expiry.tone) ? (
                        <View
                          style={[
                            styles.expiryBadge,
                            expiry.tone === 'expired'
                              ? styles.expiryExpired
                              : styles.expirySoon,
                          ]}
                        >
                          <Text
                            style={[
                              styles.expiryBadgeText,
                              expiry.tone === 'expired'
                                ? styles.expiryExpiredText
                                : styles.expirySoonText,
                            ]}
                          >
                            {expiry.label}
                          </Text>
                        </View>
                      ) : null}
                    </View>

                    <View style={styles.lotBottomRow}>
                      <Text style={styles.qtyText}>{totalLabel}</Text>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.md }}>
                        <Pressable
                          onPress={() => openEditIngredient(ing)}
                          accessibilityRole="button"
                          accessibilityLabel={`Edit ${ing.name}`}
                        >
                          <Text style={styles.adjustHint}>Edit</Text>
                        </Pressable>
                        <Text style={styles.adjustHint}>
                          {isIngExpanded ? 'Hide ▲' : 'Details ▼'}
                        </Text>
                      </View>
                    </View>

                    {isIngExpanded ? (
                      <View style={{ marginTop: spacing.sm, gap: spacing.xs }}>
                        <Text style={styles.lotBreakdownMeta}>{ing.calculation_note}</Text>
                        {ing.products.map((prod) => {
                          const isProdExpanded = Boolean(
                            expandedIngProductIds[prod.canonical_item_id]
                          );
                          const liveLots =
                            lotsByCanonicalItemId.get(prod.canonical_item_id) ?? [];
                          return (
                            <View key={prod.canonical_item_id} style={{ gap: 6 }}>
                              <Pressable
                                onPress={() =>
                                  setExpandedIngProductIds((prev) => ({
                                    ...prev,
                                    [prod.canonical_item_id]: !prev[prod.canonical_item_id],
                                  }))
                                }
                                style={styles.lotBreakdownRow}
                              >
                                <Text style={styles.lotBreakdownText}>
                                  {prod.name} (
                                  {formatQuantityWithUnit(
                                    prod.total_remaining_qty,
                                    prod.base_unit as InputUnit
                                  )}
                                  )
                                </Text>
                                <Text style={styles.lotBreakdownMeta}>
                                  {prod.active_lot_count} lot{prod.active_lot_count === 1 ? '' : 's'}{' '}
                                  {isProdExpanded ? '▲' : '▼'}
                                </Text>
                              </Pressable>

                              {isProdExpanded ? (
                                <View style={{ paddingLeft: 12, gap: 4 }}>
                                  <Pressable
                                    onPress={() => void openPriceHistory(prod.canonical_item_id)}
                                  >
                                    <Text style={styles.adjustHint}>Price History →</Text>
                                  </Pressable>
                                  {prod.lots.map((lotDetail, idx) => {
                                    const matchedLot = liveLots.find(
                                      (l) => l.id === lotDetail.lot_id
                                    );
                                    return (
                                      <Pressable
                                        key={lotDetail.lot_id}
                                        onPress={() => {
                                          if (matchedLot) {
                                            openAdjustModal(matchedLot);
                                          }
                                        }}
                                        style={styles.lotBreakdownRow}
                                      >
                                        <Text style={styles.lotBreakdownText}>
                                          Lot {idx + 1}:{' '}
                                          {formatQuantityWithUnit(
                                            lotDetail.remaining_qty,
                                            lotDetail.base_unit as InputUnit
                                          )}
                                        </Text>
                                        <Text style={styles.lotBreakdownMeta}>
                                          {formatExpiryBadge(lotDetail.expires_on).label}
                                          {matchedLot ? ' · Adjust →' : ''}
                                        </Text>
                                      </Pressable>
                                    );
                                  })}
                                </View>
                              ) : null}
                            </View>
                          );
                        })}
                      </View>
                    ) : null}
                  </Pressable>
                );
              })}

              {filteredUnmappedProducts.length > 0 ? (
                <View style={{ marginTop: spacing.sm, gap: spacing.xs }}>
                  <Text style={[styles.lotCategory, { fontWeight: '700', marginBottom: 2 }]}>
                    Unmapped Products ({filteredUnmappedProducts.length})
                  </Text>
                </View>
              ) : null}
              {filteredUnmappedProducts.map((u) => {
                const { title, packageDetail } = cleanPantryProductDisplayName(u.name);
                const isExpanded = Boolean(expandedIngProductIds[u.canonical_item_id]);
                const liveLots = lotsByCanonicalItemId.get(u.canonical_item_id) ?? [];
                const formattedTotal = formatQuantityWithUnit(
                  u.total_remaining_qty,
                  u.base_unit as InputUnit
                );
                return (
                  <Pressable
                    key={u.canonical_item_id}
                    onPress={() =>
                      setExpandedIngProductIds((prev) => ({
                        ...prev,
                        [u.canonical_item_id]: !prev[u.canonical_item_id],
                      }))
                    }
                    style={({ pressed }) => [
                      styles.lotCard,
                      pressed && styles.lotCardPressed,
                    ]}
                    accessibilityRole="button"
                    accessibilityLabel={`${title}, ${formattedTotal}`}
                  >
                    <View style={styles.lotTopRow}>
                      <View style={styles.lotTitleGroup}>
                        <Text style={styles.lotName}>{title}</Text>
                        <Text style={styles.lotCategory}>
                          {formatLotCountLabel(u.active_lot_count)}
                          {packageDetail ? ` · ${packageDetail}` : ''}
                        </Text>
                      </View>
                    </View>
                    <View style={styles.lotBottomRow}>
                      <Text style={styles.qtyText}>{formattedTotal}</Text>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.md }}>
                        <Pressable
                          onPress={() => openEditProduct(u)}
                          accessibilityRole="button"
                          accessibilityLabel={`Edit ${title}`}
                        >
                          <Text style={styles.adjustHint}>Edit</Text>
                        </Pressable>
                        <Text style={styles.adjustHint}>
                          {isExpanded ? 'Hide ▲' : 'Lots ▼'}
                        </Text>
                      </View>
                    </View>
                    {isExpanded ? (
                      <View style={{ marginTop: spacing.sm, gap: spacing.xs }}>
                        <Pressable onPress={() => void openPriceHistory(u.canonical_item_id)}>
                          <Text style={styles.adjustHint}>Price History →</Text>
                        </Pressable>
                        {liveLots.map((lotDetail, idx) => (
                          <Pressable
                            key={lotDetail.id}
                            onPress={() => openAdjustModal(lotDetail)}
                            style={styles.lotBreakdownRow}
                          >
                            <Text style={styles.lotBreakdownText}>
                              Lot {idx + 1}:{' '}
                              {formatQuantityWithUnit(
                                lotDetail.remaining_qty,
                                lotDetail.base_unit as InputUnit
                              )}
                            </Text>
                            <Text style={styles.lotBreakdownMeta}>
                              {formatExpiryBadge(lotDetail.expires_on).label} · Adjust →
                            </Text>
                          </Pressable>
                        ))}
                      </View>
                    ) : null}
                  </Pressable>
                );
              })}

            </>
          )
        ) : filteredSummaryItems.length === 0 ? (
          <EmptyState
            icon="cube-outline"
            title={
              searchQuery.trim().length > 0
                ? 'No matching items'
                : 'Your pantry is empty'
            }
            body={
              searchQuery.trim().length > 0
                ? 'Try a different search term or clear your filter.'
                : 'Confirmed grocery purchases and manual additions will appear here as lot-tracked inventory with expiry dates.'
            }
            actionLabel={
              searchQuery.trim().length > 0 ? 'Clear Search' : 'Add Item Manually'
            }
            onAction={() => {
              if (searchQuery.trim().length > 0) {
                setSearchQuery('');
              } else {
                setAddModalVisible(true);
              }
            }}
          />
        ) : (
          filteredSummaryItems.map((item) => {
            const itemLots = lotsByCanonicalItemId.get(item.canonical_item_id) ?? [];
            const expiry = formatExpiryBadge(item.earliest_expires_on);
            const isExpanded = Boolean(expandedItemIds[item.canonical_item_id]);
            const formattedTotal = formatQuantityWithUnit(
              item.total_remaining_qty,
              item.base_unit
            );

            return (
              <Pressable
                key={item.canonical_item_id}
                onPress={() => {
                  if (itemLots.length === 1) {
                    openAdjustModal(itemLots[0]);
                  } else if (itemLots.length > 1) {
                    toggleItemExpanded(item.canonical_item_id);
                  }
                }}
                style={({ pressed }) => [
                  styles.lotCard,
                  pressed && styles.lotCardPressed,
                ]}
                accessibilityRole="button"
                accessibilityLabel={`${item.name}, remaining ${formattedTotal}, ${item.active_lot_count} active ${item.active_lot_count === 1 ? 'lot' : 'lots'}`}
              >
                <View style={styles.lotTopRow}>
                  <View style={styles.lotTitleGroup}>
                    <Text style={styles.lotName}>
                      {cleanPantryProductDisplayName(item.name).title}
                    </Text>
                    <Text style={styles.lotCategory}>
                      {formatLotCountLabel(item.active_lot_count)}
                      {cleanPantryProductDisplayName(item.name).packageDetail
                        ? ` · ${cleanPantryProductDisplayName(item.name).packageDetail}`
                        : ''}
                    </Text>
                  </View>
                  <View
                    style={[
                      styles.expiryBadge,
                      expiry.tone === 'expired' && styles.expiryExpired,
                      expiry.tone === 'soon' && styles.expirySoon,
                      expiry.tone === 'ok' && styles.expiryOk,
                      expiry.tone === 'none' && styles.expiryNone,
                    ]}
                  >
                    <Text
                      style={[
                        styles.expiryBadgeText,
                        expiry.tone === 'expired' && styles.expiryExpiredText,
                        expiry.tone === 'soon' && styles.expirySoonText,
                        expiry.tone === 'ok' && styles.expiryOkText,
                        expiry.tone === 'none' && styles.expiryNoneText,
                      ]}
                    >
                      {expiry.label}
                    </Text>
                  </View>
                </View>

                <View style={styles.lotBottomRow}>
                  <Text style={styles.qtyText}>{formattedTotal}</Text>
                  <Pressable
                    onPress={() => void openPriceHistory(item.canonical_item_id)}
                    accessibilityRole="button"
                    accessibilityLabel={`View price history for ${item.name}`}
                  >
                    <Text style={styles.adjustHint}>Price History →</Text>
                  </Pressable>
                </View>

                {isExpanded && itemLots.length > 1 ? (
                  <View style={styles.lotBreakdownContainer}>
                    {itemLots.map((lot, idx) => {
                      const lotExpiry = formatExpiryBadge(lot.expires_on);
                      return (
                        <Pressable
                          key={lot.id}
                          onPress={() => openAdjustModal(lot)}
                          style={styles.lotBreakdownRow}
                          accessibilityRole="button"
                          accessibilityLabel={`Adjust ${item.name} lot ${idx + 1}, ${formatQuantityWithUnit(lot.remaining_qty, lot.base_unit)}`}
                        >
                          <Text style={styles.lotBreakdownText}>
                            Lot {idx + 1}:{' '}
                            {formatQuantityWithUnit(lot.remaining_qty, lot.base_unit)}
                          </Text>
                          <Text style={styles.lotBreakdownMeta}>
                            {lotExpiry.label} · Adjust →
                          </Text>
                        </Pressable>
                      );
                    })}
                  </View>
                ) : null}
              </Pressable>
            );
          })
        )}
      </ScrollView>
        </>
      )}

      {/* Manual Add Modal */}
      <Modal
        visible={addModalVisible}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setAddModalVisible(false)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Add Inventory Item</Text>
            <Pressable
              onPress={() => setAddModalVisible(false)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Close add modal"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>

          <ScrollView
            contentContainerStyle={styles.modalContent}
            keyboardShouldPersistTaps="handled"
          >
            {addError ? (
              <View style={styles.errorBanner} accessibilityRole="alert">
                <Ionicons
                  name="alert-circle-outline"
                  size={18}
                  color={colors.error}
                />
                <Text style={styles.errorBannerText}>{addError}</Text>
              </View>
            ) : null}

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Item Name</Text>
              <TextInput
                value={addItemName}
                onChangeText={setAddItemName}
                placeholder="e.g. Whole Milk, Basmati Rice"
                placeholderTextColor={colors.textMuted}
                style={styles.input}
              />
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Category</Text>
              <TextInput
                value={addCategory}
                onChangeText={setAddCategory}
                placeholder="dairy, produce, grains, pantry"
                placeholderTextColor={colors.textMuted}
                style={styles.input}
              />
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Quantity</Text>
              <TextInput
                value={addQtyText}
                onChangeText={setAddQtyText}
                keyboardType="decimal-pad"
                style={styles.input}
              />
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Unit</Text>
              <View style={styles.chipsRow}>
                {INPUT_UNITS.map((unitOpt) => {
                  const selected = addUnit === unitOpt;
                  return (
                    <Pressable
                      key={unitOpt}
                      onPress={() => setAddUnit(unitOpt)}
                      style={[
                        styles.chip,
                        selected && styles.chipSelected,
                      ]}
                      accessibilityRole="button"
                      accessibilityState={{ selected }}
                    >
                      <Text
                        style={[
                          styles.chipText,
                          selected && styles.chipTextSelected,
                        ]}
                      >
                        {unitOpt}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Expiry Date (Optional)</Text>
              <TextInput
                value={addExpiresOn}
                onChangeText={setAddExpiresOn}
                placeholder="YYYY-MM-DD"
                placeholderTextColor={colors.textMuted}
                style={styles.input}
              />
            </View>
          </ScrollView>

          <View style={styles.modalFooter}>
            <Pressable
              onPress={() => void handleManualAdd()}
              disabled={isAdding}
              style={[
                styles.primaryFooterButton,
                isAdding && styles.buttonDisabled,
              ]}
              accessibilityRole="button"
              accessibilityLabel="Save item to inventory"
            >
              {isAdding ? (
                <ActivityIndicator color={colors.textOnPrimary} />
              ) : (
                <Text style={styles.primaryFooterButtonText}>
                  Add to Pantry
                </Text>
              )}
            </Pressable>
          </View>
        </View>
      </Modal>

      {/* Consume / Waste / Adjust Modal */}
      <Modal
        visible={selectedLot !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setSelectedLot(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Adjust Stock</Text>
            <Pressable
              onPress={() => setSelectedLot(null)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Close adjust modal"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>

          <ScrollView
            contentContainerStyle={styles.modalContent}
            keyboardShouldPersistTaps="handled"
          >
            {selectedLot ? (
              <View style={styles.lotSummaryBox}>
                <Text style={styles.lotSummaryTitle}>
                  {selectedLot.item_name ??
                    itemMetadataById.get(selectedLot.canonical_item_id)?.name ??
                    'Pantry Item'}
                </Text>
                <Text style={styles.lotSummarySub}>
                  Current lot balance:{' '}
                  {formatQuantityWithUnit(
                    selectedLot.remaining_qty,
                    selectedLot.base_unit
                  )}
                </Text>
              </View>
            ) : null}

            {adjustError ? (
              <View style={styles.errorBanner} accessibilityRole="alert">
                <Ionicons
                  name="alert-circle-outline"
                  size={18}
                  color={colors.error}
                />
                <Text style={styles.errorBannerText}>{adjustError}</Text>
              </View>
            ) : null}

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Action Type</Text>
              <View style={styles.chipsRow}>
                {(
                  [
                    { type: 'ADJUSTMENT_OUT', label: 'Consume' },
                    { type: 'WASTE_OUT', label: 'Waste / Spoiled' },
                    { type: 'ADJUSTMENT_IN', label: 'Restock (+)' },
                  ] as const
                ).map((opt) => {
                  const selected = adjustType === opt.type;
                  return (
                    <Pressable
                      key={opt.type}
                      onPress={() => setAdjustType(opt.type)}
                      style={[
                        styles.chip,
                        selected && styles.chipSelected,
                      ]}
                      accessibilityRole="button"
                      accessibilityState={{ selected }}
                    >
                      <Text
                        style={[
                          styles.chipText,
                          selected && styles.chipTextSelected,
                        ]}
                      >
                        {opt.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>
                Quantity ({selectedLot?.base_unit ?? 'g'})
              </Text>
              <TextInput
                value={adjustQtyText}
                onChangeText={setAdjustQtyText}
                keyboardType="decimal-pad"
                placeholder={`Enter amount in ${selectedLot?.base_unit ?? 'g'}`}
                placeholderTextColor={colors.textMuted}
                style={styles.input}
              />
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Notes (Optional)</Text>
              <TextInput
                value={adjustNotes}
                onChangeText={setAdjustNotes}
                placeholder="e.g. Used in breakfast, expired"
                placeholderTextColor={colors.textMuted}
                style={styles.input}
              />
            </View>
          </ScrollView>

          <View style={styles.modalFooter}>
            <Pressable
              onPress={() => void handleAdjustSubmit()}
              disabled={isAdjusting}
              style={[
                styles.primaryFooterButton,
                isAdjusting && styles.buttonDisabled,
              ]}
              accessibilityRole="button"
              accessibilityLabel="Apply stock adjustment"
            >
              {isAdjusting ? (
                <ActivityIndicator color={colors.textOnPrimary} />
              ) : (
                <Text style={styles.primaryFooterButtonText}>
                  Apply Adjustment
                </Text>
              )}
            </Pressable>
          </View>
        </View>
      </Modal>

      <Modal
        visible={priceHistory !== null || isLoadingPriceHistory}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setPriceHistory(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>
              {priceHistory?.product_name ?? 'Price History'}
            </Text>
            <Pressable
              onPress={() => setPriceHistory(null)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Close price history"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.modalContent}>
            {isLoadingPriceHistory ? (
              <LoadingState message="Loading price history..." />
            ) : priceHistory ? (
              <>
                <View style={styles.lotCard}>
                  <Text style={styles.lotCategory}>
                    Standard Unit: {priceHistory.standard_unit_label}
                  </Text>
                  <Text style={styles.qtyText}>
                    Latest:{' '}
                    {priceHistory.latest_price_paise_per_std != null
                      ? `₹${(priceHistory.latest_price_paise_per_std / 100).toFixed(2)}`
                      : '—'}{' '}
                    · Avg:{' '}
                    {priceHistory.avg_price_paise_per_std != null
                      ? `₹${(priceHistory.avg_price_paise_per_std / 100).toFixed(2)}`
                      : '—'}
                  </Text>
                  {priceHistory.price_change_paise != null ? (
                    <Text style={styles.lotCategory}>
                      Change:{' '}
                      {priceHistory.price_change_paise >= 0 ? '+' : ''}
                      ₹{(priceHistory.price_change_paise / 100).toFixed(2)} (
                      {priceHistory.price_change_pct ?? 0}%)
                    </Text>
                  ) : null}
                </View>
                {priceHistory.entries.length === 0 ? (
                  <EmptyState
                    icon="pricetag-outline"
                    title="No purchase history yet"
                    body="Confirmed grocery purchases for this product will appear here with normalized unit prices."
                  />
                ) : (
                  priceHistory.entries.map((entry, idx) => (
                    <View
                      key={`${entry.purchase_id}-${idx}`}
                      style={styles.lotCard}
                    >
                      <Text style={styles.lotName}>
                        {entry.merchant_name ?? 'Grocery Store'} ·{' '}
                        {entry.purchased_on}
                      </Text>
                      <Text style={styles.lotCategory}>
                        Bought:{' '}
                        {formatQuantityWithUnit(
                          String(entry.purchased_qty),
                          entry.base_unit
                        )}
                        {entry.pack_size ? ` (${entry.pack_size})` : ''} · Total: ₹
                        {(entry.line_total_paise / 100).toFixed(2)}
                      </Text>
                      <Text style={styles.lotCategory}>
                        Normalized: ₹
                        {(entry.normalized_std_price_paise / 100).toFixed(2)}{' '}
                        {priceHistory.standard_unit_label} · Receipt #
                        {entry.purchase_id.slice(0, 8)}
                      </Text>
                    </View>
                  ))
                )}
              </>
            ) : null}
          </ScrollView>
        </View>
      </Modal>

      {/* Product Identification Modal (separated from inventory lifecycle actions) */}
      <Modal
        visible={identifyTarget !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setIdentifyTarget(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Identify Product</Text>
            <Pressable
              onPress={() => setIdentifyTarget(null)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Close identify modal"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.modalContent} keyboardShouldPersistTaps="handled">
            {identifyError ? (
              <View style={styles.errorBanner} accessibilityRole="alert">
                <Ionicons name="alert-circle-outline" size={18} color={colors.error} />
                <Text style={styles.errorBannerText}>{identifyError}</Text>
              </View>
            ) : null}

            {identifyTarget ? (
              <>
                <View style={styles.fieldGroup}>
                  <Text style={styles.fieldLabel}>Product Display Name</Text>
                  <TextInput
                    value={identifyNameDraft}
                    onChangeText={setIdentifyNameDraft}
                    style={styles.input}
                  />
                  <Pressable
                    disabled={identifyBusy}
                    onPress={() => void handleSaveProductName()}
                    style={[styles.subTabChip, { marginTop: spacing.xs, flex: 0, paddingHorizontal: spacing.md }]}
                  >
                    <Text style={styles.subTabText}>Save Product Name</Text>
                  </Pressable>
                </View>

                <View style={styles.fieldGroup}>
                  <Text style={styles.fieldLabel}>
                    Current Stock: {formatQuantityWithUnit(identifyTarget.total_remaining_qty, identifyTarget.base_unit as InputUnit)}
                  </Text>
                  {(lotsByCanonicalItemId.get(identifyTarget.canonical_item_id) ?? []).length > 0 ? (
                    <Pressable
                      onPress={() => {
                        const lot = (lotsByCanonicalItemId.get(identifyTarget.canonical_item_id) ?? [])[0];
                        setIdentifyTarget(null);
                        if (lot) openAdjustModal(lot);
                      }}
                      style={[styles.subTabChip, { flex: 0, paddingHorizontal: spacing.md }]}
                    >
                      <Text style={styles.subTabText}>Correct Measurement / Stock</Text>
                    </Pressable>
                  ) : null}
                </View>

                {identifyTarget.suggested_ingredient_id ? (
                  <Pressable
                    disabled={identifyBusy}
                    onPress={() => void handleConfirmIngredientId(identifyTarget.suggested_ingredient_id!)}
                    style={[styles.primaryFooterButton, { marginBottom: spacing.md }]}
                  >
                    <Text style={styles.primaryFooterButtonText}>
                      Confirm Suggested: {identifyTarget.suggested_ingredient_name}
                    </Text>
                  </Pressable>
                ) : null}

                <View style={styles.fieldGroup}>
                  <Text style={styles.fieldLabel}>Choose Canonical Ingredient</Text>
                  <TextInput
                    value={identifyIngSearch}
                    onChangeText={setIdentifyIngSearch}
                    placeholder="Search ingredients (e.g. Milk, Apple, Urad Dal)..."
                    placeholderTextColor={colors.textMuted}
                    style={styles.input}
                  />
                  <View style={{ gap: spacing.xs, marginTop: spacing.xs }}>
                    {filteredCatalogOptions.map((ing) => (
                      <Pressable
                        key={ing.id}
                        disabled={identifyBusy}
                        onPress={() => void handleConfirmIngredientId(ing.id)}
                        style={styles.lotBreakdownRow}
                      >
                        <Text style={styles.lotBreakdownText}>{ing.name}</Text>
                        <Text style={styles.adjustHint}>Select →</Text>
                      </Pressable>
                    ))}
                  </View>
                </View>

                <Pressable
                  disabled={identifyBusy}
                  onPress={() => void handleKeepUnmapped()}
                  style={[styles.subTabChip, { marginTop: spacing.md, flex: 0 }]}
                >
                  <Text style={styles.subTabText}>Keep Unmapped</Text>
                </Pressable>
              </>
            ) : null}
          </ScrollView>
        </View>
      </Modal>
      {/* Direct Edit Pantry Item Modal (Phase 5C.4) */}
      <Modal
        visible={editTarget !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setEditTarget(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Edit Pantry Item</Text>
            <Pressable
              onPress={() => setEditTarget(null)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Close edit modal"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>

          <ScrollView contentContainerStyle={styles.modalContent} keyboardShouldPersistTaps="handled">
            {editError ? (
              <View style={styles.errorBanner} accessibilityRole="alert">
                <Ionicons name="alert-circle-outline" size={18} color={colors.error} />
                <Text style={styles.errorBannerText}>{editError}</Text>
              </View>
            ) : null}

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>
                {editTarget?.kind === 'ingredient' ? 'Ingredient Name' : 'Product Name'}
              </Text>
              <TextInput
                value={editNameDraft}
                onChangeText={setEditNameDraft}
                style={styles.input}
                accessibilityLabel="Edit pantry item name"
              />
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Available Quantity</Text>
              <TextInput
                value={editQtyDraft}
                onChangeText={setEditQtyDraft}
                keyboardType="decimal-pad"
                style={styles.input}
                accessibilityLabel="Edit available quantity"
              />
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Measurement Unit</Text>
              {suggestProductUnits(editNameDraft || editTarget?.name, editTarget?.baseUnit).map((group) => (
                <View key={group.category} style={{ marginBottom: spacing.xs }}>
                  <Text style={{ fontSize: 12, fontWeight: '600', color: colors.textSecondary, marginBottom: 4 }}>
                    {group.label}
                  </Text>
                  <View style={styles.chipsRow}>
                    {group.units.map((uOpt) => {
                      const selected = canonicalUnitToken(editUnitDraft) === canonicalUnitToken(uOpt);
                      return (
                        <Pressable
                          key={uOpt}
                          onPress={() => handleSelectEditUnit(uOpt)}
                          style={[styles.chip, selected && styles.chipSelected]}
                          accessibilityRole="button"
                          accessibilityState={{ selected }}
                        >
                          <Text style={[styles.chipText, selected && styles.chipTextSelected]}>
                            {uOpt}
                          </Text>
                        </Pressable>
                      );
                    })}
                  </View>
                </View>
              ))}
            </View>
          </ScrollView>

          <View style={[styles.modalFooter, { flexDirection: 'row', gap: spacing.sm }]}>
            <Pressable
              onPress={() => setEditTarget(null)}
              style={[styles.subTabChip, { minHeight: layout.minTapTarget }]}
              accessibilityRole="button"
            >
              <Text style={styles.subTabText}>Cancel</Text>
            </Pressable>
            <Pressable
              disabled={editBusy}
              onPress={() => void handleSaveEditPantryItem()}
              style={[styles.primaryFooterButton, { flex: 2 }, editBusy && styles.buttonDisabled]}
              accessibilityRole="button"
            >
              <Text style={styles.primaryFooterButtonText}>
                {editBusy ? 'Saving...' : 'Save Changes'}
              </Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  subTabRow: {
    flexDirection: 'row',
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.md,
    padding: 4,
    gap: 4,
    marginBottom: spacing.md,
  },
  subTabChip: {
    flex: 1,
    minHeight: 38,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.sm,
  },
  subTabChipActive: {
    backgroundColor: colors.primary,
  },
  subTabText: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  subTabTextActive: {
    color: colors.textOnPrimary,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  headerTextGroup: {
    gap: 2,
  },
  title: {
    fontSize: 24,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  subtitle: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  addButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    backgroundColor: colors.primary,
    minHeight: layout.minTapTarget,
    paddingHorizontal: spacing.lg,
    borderRadius: radii.md,
  },
  addButtonText: {
    color: colors.textOnPrimary,
    fontSize: 15,
    fontWeight: '600',
  },
  buttonPressed: {
    backgroundColor: colors.primaryPressed,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: spacing.md,
    minHeight: layout.minTapTarget,
    marginBottom: spacing.md,
  },
  searchInput: {
    flex: 1,
    fontSize: 15,
    color: colors.textPrimary,
  },
  emptyListContainer: {
    flexGrow: 1,
  },
  listContent: {
    gap: spacing.sm,
    paddingBottom: spacing.xl,
  },
  lotCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  lotCardPressed: {
    backgroundColor: colors.surfaceMuted,
  },
  lotBreakdownContainer: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: spacing.sm,
    gap: spacing.xs,
  },
  lotBreakdownRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.xs,
  },
  lotBreakdownText: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  lotBreakdownMeta: {
    fontSize: 12,
    color: colors.primary,
    fontWeight: '600',
  },
  lotTopRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: spacing.sm,
  },
  lotTitleGroup: {
    flex: 1,
    gap: 2,
  },
  lotName: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  lotCategory: {
    fontSize: 12,
    color: colors.textMuted,
    textTransform: 'capitalize',
  },
  expiryBadge: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.full,
  },
  expiryExpired: {
    backgroundColor: colors.errorBg,
  },
  expirySoon: {
    backgroundColor: colors.warningBg,
  },
  expiryOk: {
    backgroundColor: colors.primaryLight,
  },
  expiryNone: {
    backgroundColor: colors.surfaceMuted,
  },
  expiryBadgeText: {
    fontSize: 12,
    fontWeight: '700',
  },
  expiryExpiredText: {
    color: colors.error,
  },
  expirySoonText: {
    color: colors.warning,
  },
  expiryOkText: {
    color: colors.primary,
  },
  expiryNoneText: {
    color: colors.textMuted,
  },
  lotBottomRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  qtyText: {
    fontSize: 17,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  adjustHint: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.primary,
  },
  modalContainer: {
    flex: 1,
    backgroundColor: colors.background,
  },
  modalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    backgroundColor: colors.surface,
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  closeButton: {
    minWidth: layout.minTapTarget,
    minHeight: layout.minTapTarget,
    alignItems: 'center',
    justifyContent: 'center',
  },
  modalContent: {
    padding: spacing.lg,
    gap: spacing.md,
  },
  lotSummaryBox: {
    backgroundColor: colors.primaryLight,
    borderRadius: radii.md,
    padding: spacing.md,
    gap: 2,
  },
  lotSummaryTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.primary,
  },
  lotSummarySub: {
    fontSize: 14,
    color: colors.textPrimary,
  },
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.errorBg,
    borderColor: colors.errorBorder,
    borderWidth: 1,
    padding: spacing.md,
    borderRadius: radii.md,
  },
  errorBannerText: {
    flex: 1,
    fontSize: 13,
    color: colors.error,
  },
  fieldGroup: {
    gap: spacing.xs,
  },
  fieldLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.md,
    minHeight: layout.minTapTarget,
    fontSize: 15,
    color: colors.textPrimary,
  },
  chipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  chip: {
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.full,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surface,
  },
  chipSelected: {
    borderColor: colors.primary,
    backgroundColor: colors.primaryLight,
  },
  chipText: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  chipTextSelected: {
    color: colors.primary,
  },
  modalFooter: {
    padding: spacing.lg,
    backgroundColor: colors.surface,
    borderTopColor: colors.border,
    borderTopWidth: 1,
  },
  primaryFooterButton: {
    minHeight: layout.minTapTarget,
    borderRadius: radii.md,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryFooterButtonText: {
    fontSize: 15,
    fontWeight: '600',
    color: colors.textOnPrimary,
  },
});
