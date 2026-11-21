import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ActivityIndicator,
  Alert,
  AppState,
  Linking,
  Modal,
  PermissionsAndroid,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { router, useLocalSearchParams } from "expo-router";
import { useQueryClient } from "@tanstack/react-query";
import Constants from "expo-constants";
import * as ImagePicker from "expo-image-picker";
import * as DocumentPicker from "expo-document-picker";
import {
  CameraPermissionOutcome,
  checkCameraPermissionStatus,
  resolveCameraPermissionForCapture,
} from "./cameraPermission";
import {
  findUnresolvedIngredientLines,
  getIngredientConfidenceLabel,
} from "./ingredientReviewHelpers";
import { useApiClient } from "../auth/useApiClient";
import { useHousehold } from "../auth/HouseholdContext";
import { invalidateHouseholdData } from "../../shared/api/queryKeys";
import {
  BaseUnit,
  CanonicalIngredientOption,
  CanonicalIngredientsListResponse,
  CatalogItem,
  CatalogItemsResponse,
  DiningMode,
  MealType,
  PurchaseConfirmResponse,
  PurchaseDraft,
  PurchaseDraftCreateRequest,
  PurchaseDraftItemUpdateInput,
  PurchaseDraftListResponse,
  PurchaseDraftUpdateRequest,
  PurchaseRecord,
  PurchasesListResponse,
} from "../../shared/api/types";
import { ApiClientError } from "../../shared/api/client";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  ScreenContainer,
} from "../../shared/components";
import {
  formatInrFromPaise,
  paiseToRupeesString,
  rupeesToPaise,
} from "../../shared/format/inr";
import {
  createIdempotencyKey,
  formatQuantityWithUnit,
  InputUnit,
  normalizeToBaseUnit,
} from "../../shared/format/units";
import { colors, layout, radii, spacing } from "../../shared/theme/tokens";
import {
  buildMultiReceiptFormData,
  buildReceiptFormData,
  type ReceiptAssetInput,
} from "./receiptUpload";
import {
  buildConfirmOutsideFoodPayload,
  deriveInitialBudgetOnly,
  isExplicitOutsideFoodLine,
  toggleBudgetOnlyForLine,
} from "./purchaseDraftClassification";
import {
  computeDraftFinancials,
  computeLineTotalFromQtyAndRate,
  filterLiveOcrWarnings,
} from "./purchaseFinancials";

interface EditableDraftLine {
  localKey: string;
  id?: string;
  raw_text?: string;
  canonical_item_id: string | null;
  display_name: string;
  qtyText: string;
  unit: InputUnit;
  unitPriceRupeesText: string;
  lineTotalRupeesText: string;
  track_inventory: boolean;
  budgetOnly: boolean;
  classification_status?: string;
  food_state?: string | null;
  budget_treatment?: string | null;
  review_reason?: string | null;
  warnings?: string[];
  expiresOnText: string;
  standardized_name?: string | null;
  ingredient_family_name?: string | null;
  ingredient_variant_id?: string | null;
  canonical_ingredient_id?: string | null;
  ingredient_category?: string | null;
  ingredient_confidence_tier?: string | null;
  ingredient_confidence_score?: number | null;
  ingredient_review_reason?: string | null;
  user_approved_ingredient?: boolean;
  ingredient_suggestions?: Array<{
    variant_id: string;
    variant_name: string;
    family_id: string;
    family_name: string;
    canonical_ingredient_id: string | null;
    score: number;
  }>;
  quantity_confidence_tier?: string | null;
  quantity_review_reason?: string | null;
  financial_mismatch?: boolean;
  financial_review_reason?: string | null;
}

const UNIT_OPTIONS: InputUnit[] = ["g", "kg", "ml", "l", "pcs"];
const MEAL_OPTIONS: Array<{ value: MealType; label: string }> = [
  { value: "breakfast", label: "Breakfast" },
  { value: "lunch", label: "Lunch" },
  { value: "dinner", label: "Dinner" },
  { value: "snacks", label: "Snacks" },
  { value: "multiple", label: "Multiple" },
  { value: "other", label: "Other" },
];
const DINING_MODES: Array<{ value: DiningMode; label: string }> = [
  { value: "restaurant", label: "Restaurant" },
  { value: "takeaway", label: "Takeaway" },
  { value: "delivery", label: "Delivery" },
];

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function makeEmptyLine(index: number): EditableDraftLine {
  return {
    localKey: `line-${Date.now()}-${index}-${
      Math.random().toString(36).slice(2, 6)
    }`,
    canonical_item_id: null,
    display_name: "",
    qtyText: "1",
    unit: "pcs",
    unitPriceRupeesText: "0.00",
    lineTotalRupeesText: "0.00",
    track_inventory: true,
    budgetOnly: false,
    expiresOnText: "",
  };
}

function parseSignedRupeesToPaise(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) {
    return 0;
  }
  const neg = trimmed.startsWith("-");
  const absPaise = rupeesToPaise(trimmed.replace(/^[+-]\s*/, "")) ?? 0;
  return neg ? -absPaise : absPaise;
}

export function PurchasesScreen() {
  const apiClient = useApiClient();
  const queryClient = useQueryClient();
  const { activeHouseholdId } = useHousehold();

  const [drafts, setDrafts] = useState<PurchaseDraft[]>([]);
  const [draftOrchestration, setDraftOrchestration] = useState<PurchaseDraft["ocr_orchestration"]>(null);
  const [purchases, setPurchases] = useState<PurchaseRecord[]>([]);
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [ingredientCatalog, setIngredientCatalog] = useState<CanonicalIngredientOption[]>([]);
  const [activePickerLineKey, setActivePickerLineKey] = useState<string | null>(null);
  const [ingredientSearchText, setIngredientSearchText] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const hasLoadedRef = useRef(false);
  const inFlightRef = useRef(false);
  const uploadSeqRef = useRef(0);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [cameraPermissionState, setCameraPermissionState] = useState<
    "unknown" | CameraPermissionOutcome
  >("unknown");
  const isExpoGo = Constants.appOwnership === "expo";

  const checkAndroidOsCamera = useCallback(async () => {
    if (Platform.OS !== "android") {
      return false;
    }
    return PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.CAMERA);
  }, []);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (nextState) => {
      if (nextState === "active") {
        void checkCameraPermissionStatus({
          getCameraPermissionsAsync: ImagePicker.getCameraPermissionsAsync,
          checkAndroidOsCameraPermission: checkAndroidOsCamera,
        }).then((outcome) => {
          setCameraPermissionState((prev) =>
            prev === "unknown" && outcome !== "granted" ? prev : outcome
          );
        });
      }
    });
    return () => {
      subscription.remove();
    };
  }, [checkAndroidOsCamera]);

  // Review / Manual Entry Modal State
  const [modalVisible, setModalVisible] = useState(false);
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const [viewingConfirmedPurchase, setViewingConfirmedPurchase] = useState<
    PurchaseRecord | null
  >(null);
  const [merchantName, setMerchantName] = useState("");
  const [receiptNumber, setReceiptNumber] = useState("");
  const [purchasedOn, setPurchasedOn] = useState(todayIsoDate());
  const [subtotalRupees, setSubtotalRupees] = useState("0.00");
  const [discountRupees, setDiscountRupees] = useState("0.00");
  const [additionalChargesRupees, setAdditionalChargesRupees] = useState(
    "0.00",
  );
  const [cgstRupees, setCgstRupees] = useState("0.00");
  const [sgstRupees, setSgstRupees] = useState("0.00");
  const [igstRupees, setIgstRupees] = useState("0.00");
  const [roundingRupees, setRoundingRupees] = useState("0.00");
  const [receiptTotalRupees, setReceiptTotalRupees] = useState("0.00");
  const [lines, setLines] = useState<EditableDraftLine[]>([]);
  const [isSavingDraft, setIsSavingDraft] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);
  const [ocrWarnings, setOcrWarnings] = useState<string[]>([]);
  const [documentType, setDocumentType] = useState<string>("unknown");
  const [orderId, setOrderId] = useState<string>("");
  const [missingFields, setMissingFields] = useState<string[]>([]);
  const [attachmentsCount, setAttachmentsCount] = useState<number>(0);
  const [expandedEvidenceKeys, setExpandedEvidenceKeys] = useState<
    Record<string, boolean>
  >({});
  const [mealType, setMealType] = useState<MealType | null>(null);
  const [diningMode, setDiningMode] = useState<DiningMode | null>(null);
  const [peopleCountText, setPeopleCountText] = useState("");

  const loadData = useCallback(
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
        const [draftsRes, purchasesRes, catalogRes, ingCatalogRes] = await Promise.all([
          apiClient.get<PurchaseDraftListResponse>(
            "/api/v1/purchases/drafts?status=draft&limit=20",
          ),
          apiClient.get<PurchasesListResponse>(
            "/api/v1/purchases?status=confirmed&limit=20",
          ),
          apiClient.get<CatalogItemsResponse>(
            "/api/v1/catalog/items?limit=100",
          ),
          apiClient.get<CanonicalIngredientsListResponse>(
            "/api/v1/inventory/ingredients/catalog",
          ).catch(() => ({ items: [] })),
        ]);
        setDrafts(draftsRes.items ?? []);
        setPurchases(purchasesRes.items ?? []);
        setCatalogItems(catalogRes.items ?? []);
        setIngredientCatalog(ingCatalogRes.items ?? []);
        hasLoadedRef.current = true;
      } catch (err) {
        const message = err instanceof ApiClientError
          ? err.message
          : "Failed to load purchases and drafts.";
        setLoadError(message);
      } finally {
        inFlightRef.current = false;
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [apiClient],
  );

  useEffect(() => {
    void loadData(false);
  }, [loadData, activeHouseholdId]);

  const openDraftInModal = useCallback((draft: PurchaseDraft) => {
    setViewingConfirmedPurchase(null);
    setEditingDraftId(draft.id);
    setDraftOrchestration(draft.ocr_orchestration ?? null);
    setMerchantName(draft.merchant_name ?? "");
    setReceiptNumber(draft.receipt_number ?? "");
    setPurchasedOn(draft.purchased_on ?? todayIsoDate());
    const itemsTotal = (draft.items ?? []).reduce(
      (acc, it) => acc + (it.line_total_paise ?? 0),
      0,
    );
    setSubtotalRupees(
      draft.subtotal_paise != null ? paiseToRupeesString(draft.subtotal_paise) : "",
    );
    setDiscountRupees(paiseToRupeesString(draft.discount_paise ?? 0));
    setAdditionalChargesRupees(
      paiseToRupeesString(draft.additional_charges_paise ?? 0),
    );
    setCgstRupees(paiseToRupeesString(draft.cgst_paise ?? 0));
    setSgstRupees(paiseToRupeesString(draft.sgst_paise ?? 0));
    setIgstRupees(paiseToRupeesString(draft.igst_paise ?? 0));
    const rPaise = draft.rounding_paise ?? 0;
    setRoundingRupees(
      rPaise < 0
        ? `-${paiseToRupeesString(Math.abs(rPaise))}`
        : paiseToRupeesString(rPaise),
    );
    setReceiptTotalRupees(paiseToRupeesString(draft.total_amount_paise ?? 0));
    setLines(
      (draft.items ?? []).map((item, idx) => ({
        localKey: item.id || `draft-line-${idx}`,
        id: item.id,
        raw_text: item.raw_text ?? undefined,
        canonical_item_id: item.canonical_item_id,
        display_name: item.display_name || item.raw_text || "",
        qtyText: String(Number(item.normalized_qty) || 1),
        unit: item.base_unit,
        unitPriceRupeesText: paiseToRupeesString(
          item.unit_price_paise ?? item.line_total_paise ?? 0,
        ),
        lineTotalRupeesText: paiseToRupeesString(item.line_total_paise ?? 0),
        track_inventory: item.track_inventory ?? true,
        budgetOnly: deriveInitialBudgetOnly(item),
        classification_status: item.classification_status,
        food_state: item.food_state,
        budget_treatment: item.budget_treatment,
        review_reason: item.review_reason ?? null,
        warnings: item.warnings ?? [],
        expiresOnText: item.expires_on ?? "",
        standardized_name: item.standardized_name ?? null,
        ingredient_family_name: item.ingredient_family_name ?? null,
        ingredient_variant_id: item.ingredient_variant_id ?? null,
        canonical_ingredient_id: item.canonical_ingredient_id ?? null,
        ingredient_category: item.ingredient_category ?? null,
        ingredient_confidence_tier: item.ingredient_confidence_tier ?? null,
        ingredient_confidence_score: item.ingredient_confidence_score ?? null,
        ingredient_review_reason: item.ingredient_review_reason ?? null,
        user_approved_ingredient: item.user_approved_ingredient ?? false,
        ingredient_suggestions: item.ingredient_suggestions ?? [],
        quantity_confidence_tier: item.quantity_confidence_tier ?? null,
        quantity_review_reason: item.quantity_review_reason ?? null,
        financial_mismatch: item.financial_mismatch ?? false,
        financial_review_reason: item.financial_review_reason ?? null,
      })),
    );
    setMealType(null);
    setDiningMode(null);
    setPeopleCountText("");
    setModalError(null);
    setOcrWarnings(draft.ocr_warnings ?? []);
    setDocumentType(draft.document_type ?? "unknown");
    setOrderId(draft.order_id ?? "");
    setMissingFields(draft.missing_fields ?? []);
    setAttachmentsCount(draft.attachments?.length ?? 0);
    setModalVisible(true);
  }, []);

  const openConfirmedPurchaseInModal = useCallback(
    (purchase: PurchaseRecord) => {
      setEditingDraftId(null);
      setViewingConfirmedPurchase(purchase);
      setModalError(null);
      setModalVisible(true);
    },
    [],
  );

  const { purchaseId: routePurchaseId } = useLocalSearchParams<{ purchaseId?: string }>();
  const handledRoutePurchaseRef = useRef<string | null>(null);
  useEffect(() => {
    if (!routePurchaseId || handledRoutePurchaseRef.current === routePurchaseId) {
      return;
    }
    const found = purchases.find((p) => p.id === routePurchaseId);
    if (found) {
      handledRoutePurchaseRef.current = routePurchaseId;
      openConfirmedPurchaseInModal(found);
      return;
    }
    let cancelled = false;
    apiClient
      .get<PurchaseRecord>(`/api/v1/purchases/${routePurchaseId}`)
      .then((rec) => {
        if (!cancelled && rec?.id) {
          handledRoutePurchaseRef.current = routePurchaseId;
          openConfirmedPurchaseInModal(rec);
        }
      })
      .catch(() => {
        // Ignore if purchase is unavailable
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, openConfirmedPurchaseInModal, purchases, routePurchaseId]);

  const openManualEntryModal = useCallback(() => {
    setViewingConfirmedPurchase(null);
    setEditingDraftId(null);
    setMerchantName("");
    setReceiptNumber("");
    setOcrWarnings([]);
    setDocumentType("unknown");
    setOrderId("");
    setMissingFields([]);
    setAttachmentsCount(0);
    setPurchasedOn(todayIsoDate());
    setSubtotalRupees("0.00");
    setDiscountRupees("0.00");
    setAdditionalChargesRupees("0.00");
    setCgstRupees("0.00");
    setSgstRupees("0.00");
    setIgstRupees("0.00");
    setRoundingRupees("0.00");
    setReceiptTotalRupees("0.00");
    setLines([makeEmptyLine(0)]);
    setMealType(null);
    setDiningMode(null);
    setPeopleCountText("");
    setModalError(null);
    setModalVisible(true);
  }, []);

  const uploadReceiptAssets = useCallback(
    async (assets: ReceiptAssetInput[], targetPurchaseId?: string | null) => {
      if (isUploading || assets.length === 0) {
        return;
      }
      const seq = ++uploadSeqRef.current;
      // Clear previous draft state before starting a fresh upload
      if (!targetPurchaseId) {
        setEditingDraftId(null);
        setViewingConfirmedPurchase(null);
        setModalVisible(false);
        setModalError(null);
      }
      setIsUploading(true);
      setActionError(null);
      try {
        const formData = assets.length === 1 && !targetPurchaseId
          ? await buildReceiptFormData(assets[0])
          : await buildMultiReceiptFormData(assets, targetPurchaseId);

        const createdDraft = await apiClient.post<PurchaseDraft>(
          "/api/v1/purchases/ocr",
          formData,
        );
        if (uploadSeqRef.current !== seq) {
          return;
        }
        await loadData(true);
        if (uploadSeqRef.current !== seq) {
          return;
        }
        openDraftInModal(createdDraft);
      } catch (err) {
        if (uploadSeqRef.current !== seq) {
          return;
        }
        const message = err instanceof ApiClientError || err instanceof Error
          ? err.message
          : "Receipt upload failed. Please try again or enter manually.";
        setActionError(message);
      } finally {
        if (uploadSeqRef.current === seq) {
          setIsUploading(false);
        }
      }
    },
    [apiClient, isUploading, loadData, openDraftInModal],
  );

  const handleCameraCapture = useCallback(async () => {
    setActionError(null);
    const outcome = await resolveCameraPermissionForCapture({
      getCameraPermissionsAsync: ImagePicker.getCameraPermissionsAsync,
      requestCameraPermissionsAsync: ImagePicker.requestCameraPermissionsAsync,
      checkAndroidOsCameraPermission: checkAndroidOsCamera,
    });
    setCameraPermissionState(outcome);
    if (outcome !== "granted") {
      return;
    }

    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ["images"],
      quality: 0.85,
    });

    if (result.canceled || !result.assets || result.assets.length === 0) {
      return;
    }

    const photo = result.assets[0];
    await uploadReceiptAssets([
      {
        uri: photo.uri,
        name: photo.fileName ?? `receipt-${Date.now()}.jpg`,
        mimeType: photo.mimeType ?? "image/jpeg",
      },
    ]);
  }, [uploadReceiptAssets]);

  const handleGalleryMultiUpload = useCallback(
    async (targetPurchaseId?: string | null) => {
      setActionError(null);
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ["images"],
        allowsMultipleSelection: true,
        quality: 0.85,
      });
      if (result.canceled || !result.assets || result.assets.length === 0) {
        return;
      }
      await uploadReceiptAssets(
        result.assets.map((asset, index) => ({
          uri: asset.uri,
          name: asset.fileName ?? `receipt-${Date.now()}-${index + 1}.jpg`,
          mimeType: asset.mimeType ?? "image/jpeg",
        })),
        targetPurchaseId,
      );
    },
    [uploadReceiptAssets],
  );

  const handleDocumentUpload = useCallback(async () => {
    setActionError(null);
    const result = await DocumentPicker.getDocumentAsync({
      type: ["image/*", "application/pdf"],
      multiple: true,
      copyToCacheDirectory: true,
    });

    if (result.canceled || !result.assets || result.assets.length === 0) {
      return;
    }

    await uploadReceiptAssets(
      result.assets.map((doc, index) => ({
        uri: doc.uri,
        name: doc.name ?? `receipt-${Date.now()}-${index + 1}`,
        mimeType: doc.mimeType ?? "application/octet-stream",
      })),
    );
  }, [uploadReceiptAssets]);

  const {
    itemsSumPaise,
    headerTotalPaise,
    hasMathMismatch,
    mismatchPaise,
    mismatchedLineKeys,
  } = useMemo(
    () =>
      computeDraftFinancials({
        lines,
        subtotalRupees,
        discountRupees,
        additionalChargesRupees,
        cgstRupees,
        sgstRupees,
        igstRupees,
        roundingRupees,
        receiptTotalRupees,
      }),
    [
      lines,
      subtotalRupees,
      discountRupees,
      additionalChargesRupees,
      cgstRupees,
      sgstRupees,
      igstRupees,
      roundingRupees,
      receiptTotalRupees,
    ],
  );

  const liveOcrWarnings = useMemo(
    () => filterLiveOcrWarnings(ocrWarnings),
    [ocrWarnings],
  );

  const updateLine = useCallback(
    (localKey: string, patch: Partial<EditableDraftLine>) => {
      setLines((prev) =>
        prev.map((line) =>
          line.localKey === localKey ? { ...line, ...patch } : line
        )
      );
    },
    [],
  );

  const removeLine = useCallback((localKey: string) => {
    setLines((prev) => prev.filter((line) => line.localKey !== localKey));
  }, []);

  const matchCatalogItem = useCallback(
    (name: string): CatalogItem | undefined => {
      const normalized = name.trim().toLowerCase();
      if (!normalized) {
        return undefined;
      }
      return catalogItems.find(
        (item) => item.name.toLowerCase() === normalized,
      );
    },
    [catalogItems],
  );

  const buildDraftItemsPayload = useCallback(():
    | PurchaseDraftItemUpdateInput[]
    | null => {
    if (lines.length === 0) {
      setModalError("Add at least one grocery item before saving.");
      return null;
    }

    const payloadItems: PurchaseDraftItemUpdateInput[] = [];
    for (const line of lines) {
      const trimmedName = line.display_name.trim();
      if (!trimmedName) {
        setModalError("Every line item must have a product name.");
        return null;
      }

      const qtyNum = Number(line.qtyText);
      if (!Number.isFinite(qtyNum) || qtyNum <= 0) {
        setModalError(`Enter a valid quantity (> 0) for "${trimmedName}".`);
        return null;
      }

      const linePaise = rupeesToPaise(line.lineTotalRupeesText);
      if (linePaise === null) {
        setModalError(`Enter a valid INR price for "${trimmedName}".`);
        return null;
      }

      const expiresTrimmed = line.expiresOnText.trim();
      if (
        expiresTrimmed.length > 0 &&
        !/^\d{4}-\d{2}-\d{2}$/.test(expiresTrimmed)
      ) {
        setModalError(
          `Expiry date for "${trimmedName}" must use YYYY-MM-DD format.`,
        );
        return null;
      }

      const matched = line.canonical_item_id !== null
        ? catalogItems.find((c) => c.id === line.canonical_item_id)
        : matchCatalogItem(trimmedName);

      const normalized = normalizeToBaseUnit(qtyNum, line.unit);

      const unitPricePaise = rupeesToPaise(line.unitPriceRupeesText) ??
        linePaise;

      payloadItems.push({
        id: line.id,
        raw_text: line.raw_text,
        canonical_item_id: matched ? matched.id : line.canonical_item_id,
        display_name: trimmedName,
        normalized_qty: normalized.qty,
        base_unit: normalized.baseUnit,
        unit_price_paise: unitPricePaise,
        line_total_paise: linePaise,
        track_inventory: line.track_inventory,
        food_state: line.food_state,
        budget_treatment: line.budget_treatment,
        expires_on: expiresTrimmed.length > 0 ? expiresTrimmed : null,
        canonical_ingredient_id: line.canonical_ingredient_id ?? null,
        ingredient_variant_id: line.ingredient_variant_id ?? null,
        user_approved_ingredient: Boolean(line.user_approved_ingredient),
      });
    }

    return payloadItems;
  }, [catalogItems, lines, matchCatalogItem]);

  const handleSaveDraft = useCallback(
    async (): Promise<PurchaseDraft | null> => {
      if (isSavingDraft || isConfirming) {
        return null;
      }
      setModalError(null);

      const itemsPayload = buildDraftItemsPayload();
      if (!itemsPayload) {
        return null;
      }

      const totalPaise = rupeesToPaise(receiptTotalRupees);
      if (totalPaise === null) {
        setModalError("Enter a valid receipt total in INR.");
        return null;
      }

      setIsSavingDraft(true);
      try {
        const subPaise = rupeesToPaise(subtotalRupees);
        const discPaise = rupeesToPaise(discountRupees) ?? 0;
        const chargesPaise = rupeesToPaise(additionalChargesRupees) ?? 0;
        const cgstP = rupeesToPaise(cgstRupees) ?? 0;
        const sgstP = rupeesToPaise(sgstRupees) ?? 0;
        const igstP = rupeesToPaise(igstRupees) ?? 0;
        const roundP = parseSignedRupeesToPaise(roundingRupees);
        const taxP = cgstP + sgstP + igstP;

        let saved: PurchaseDraft;
        if (editingDraftId) {
          const body: PurchaseDraftUpdateRequest = {
            merchant_name: merchantName.trim() || "Grocery Store",
            receipt_number: receiptNumber.trim() || null,
            purchased_on: purchasedOn.trim() || todayIsoDate(),
            subtotal_paise: subPaise,
            discount_paise: discPaise,
            additional_charges_paise: chargesPaise,
            cgst_paise: cgstP,
            sgst_paise: sgstP,
            igst_paise: igstP,
            tax_paise: taxP,
            rounding_paise: roundP,
            total_amount_paise: totalPaise,
            items: itemsPayload,
          };
          saved = await apiClient.request<PurchaseDraft>(
            `/api/v1/purchases/drafts/${editingDraftId}`,
            { method: "PATCH", body },
          );
        } else {
          const body: PurchaseDraftCreateRequest = {
            merchant_name: merchantName.trim() || "Grocery Store",
            receipt_number: receiptNumber.trim() || null,
            purchased_on: purchasedOn.trim() || todayIsoDate(),
            subtotal_paise: subPaise,
            discount_paise: discPaise,
            additional_charges_paise: chargesPaise,
            cgst_paise: cgstP,
            sgst_paise: sgstP,
            igst_paise: igstP,
            tax_paise: taxP,
            rounding_paise: roundP,
            total_amount_paise: totalPaise,
            items: itemsPayload,
          };
          saved = await apiClient.post<PurchaseDraft>(
            "/api/v1/purchases/drafts",
            body,
          );
          setEditingDraftId(saved.id);
        }
        await loadData(true);
        openDraftInModal(saved);
        return saved;
      } catch (err) {
        const message = err instanceof ApiClientError
          ? err.message
          : "Could not save purchase draft.";
        setModalError(message);
        return null;
      } finally {
        setIsSavingDraft(false);
      }
    },
    [
      apiClient,
      buildDraftItemsPayload,
      editingDraftId,
      isConfirming,
      isSavingDraft,
      loadData,
      merchantName,
      purchasedOn,
      receiptTotalRupees,
    ],
  );

  const handleConfirmPurchase = useCallback(async () => {
    if (isConfirming || isSavingDraft) {
      return;
    }
    if (missingFields.includes("line_items") && lines.length === 0) {
      setModalError(
        "Bill summary has no itemized products. Attach an itemized invoice or add items before confirming.",
      );
      return;
    }
    if (hasMathMismatch && mismatchPaise > 5000) {
      setModalError(
        `Severe total mismatch (${
          formatInrFromPaise(mismatchPaise)
        }). Review and correct flagged line prices or totals before confirming.`,
      );
      return;
    }
    const unresolvedIngredientLines = findUnresolvedIngredientLines(lines);
    if (unresolvedIngredientLines.length > 0) {
      const names = unresolvedIngredientLines
        .map((l) => l.display_name.trim() || l.raw_text || "Item")
        .slice(0, 3)
        .join(", ");
      setModalError(
        `Please approve, select an ingredient, or mark as non-pantry for: ${names}`,
      );
      return;
    }
    const outsideCheck = buildConfirmOutsideFoodPayload(
      lines,
      mealType,
      diningMode,
      purchasedOn.trim() || todayIsoDate(),
      peopleCountText,
    );
    if (!outsideCheck.ok) {
      setModalError(outsideCheck.error);
      return;
    }

    const saved = await handleSaveDraft();
    if (!saved) {
      return;
    }

    setIsConfirming(true);
    setModalError(null);
    try {
      const confirmBody = {
        ...outsideCheck.body,
        enforce_ingredient_review: true,
      };
      await apiClient.post<PurchaseConfirmResponse>(
        `/api/v1/purchases/drafts/${saved.id}/confirm`,
        confirmBody,
        {
          headers: {
            "Idempotency-Key": createIdempotencyKey("confirm-purchase"),
          },
        },
      );
      setModalVisible(false);
      await invalidateHouseholdData(queryClient, activeHouseholdId);
      await loadData(true);
    } catch (err) {
      const message = err instanceof ApiClientError
        ? err.message
        : "Could not confirm purchase into inventory.";
      setModalError(message);
    } finally {
      setIsConfirming(false);
    }
  }, [
    activeHouseholdId,
    apiClient,
    diningMode,
    handleSaveDraft,
    hasMathMismatch,
    isConfirming,
    isSavingDraft,
    lines,
    loadData,
    mealType,
    mismatchPaise,
    missingFields,
    peopleCountText,
    purchasedOn,
    queryClient,
  ]);

  const handleDiscardDraft = useCallback(
    async (draftId: string) => {
      try {
        await apiClient.request<void>(`/api/v1/purchases/drafts/${draftId}`, {
          method: "DELETE",
        });
        setDrafts((prev) => prev.filter((d) => d.id !== draftId));
        setPurchases((prev) => prev.filter((p) => p.id !== draftId));
        if (editingDraftId === draftId) {
          setEditingDraftId(null);
          setModalVisible(false);
        }
        await loadData(true);
      } catch (err) {
        const message = err instanceof ApiClientError
          ? err.message
          : "Unable to discard draft.";
        Alert.alert("Discard Failed", message);
      }
    },
    [apiClient, editingDraftId, loadData],
  );

  if (isLoading) {
    return (
      <ScreenContainer>
        <LoadingState message="Loading purchases and drafts..." />
      </ScreenContainer>
    );
  }

  if (loadError) {
    return (
      <ScreenContainer>
        <ErrorState message={loadError} onRetry={() => void loadData(false)} />
      </ScreenContainer>
    );
  }

  const isEmpty = drafts.length === 0 && purchases.length === 0;

  return (
    <ScreenContainer>
      <ScrollView
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={isRefreshing}
            onRefresh={() => void loadData(true)}
            tintColor={colors.primary}
          />
        }
      >
        <View style={styles.header}>
          <Text style={styles.title}>Purchases</Text>
          <Text style={styles.subtitle}>
            Scan receipts, review line items, and confirm stock into your pantry
          </Text>
          <Pressable
            onPress={() => router.push("/experimental-ocr-lab")}
            style={{ alignSelf: "flex-start", marginTop: 6, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 6, backgroundColor: colors.surfaceMuted }}
            accessibilityRole="button"
            accessibilityLabel="Open Experimental OCR Lab"
          >
            <Text style={{ fontSize: 12, fontWeight: "600", color: colors.primary }}>
              Experimental OCR Lab
            </Text>
          </Pressable>
        </View>

        <View style={styles.actionRow}>
          <Pressable
            onPress={() => void handleCameraCapture()}
            disabled={isUploading}
            style={({ pressed }) => [
              styles.actionButtonPrimary,
              pressed && styles.buttonPressed,
              isUploading && styles.buttonDisabled,
            ]}
            accessibilityRole="button"
            accessibilityLabel="Scan receipt with camera"
          >
            <Ionicons
              name="camera-outline"
              size={20}
              color={colors.textOnPrimary}
            />
            <Text style={styles.actionButtonPrimaryText}>Camera</Text>
          </Pressable>

          <Pressable
            onPress={() => void handleDocumentUpload()}
            disabled={isUploading}
            style={({ pressed }) => [
              styles.actionButtonSecondary,
              pressed && styles.secondaryPressed,
              isUploading && styles.buttonDisabled,
            ]}
            accessibilityRole="button"
            accessibilityLabel="Upload receipt image or PDF"
          >
            <Ionicons
              name="document-attach-outline"
              size={20}
              color={colors.primary}
            />
            <Text style={styles.actionButtonSecondaryText}>Upload</Text>
          </Pressable>

          <Pressable
            onPress={openManualEntryModal}
            disabled={isUploading}
            style={({ pressed }) => [
              styles.actionButtonSecondary,
              pressed && styles.secondaryPressed,
              isUploading && styles.buttonDisabled,
            ]}
            accessibilityRole="button"
            accessibilityLabel="Enter purchase manually"
          >
            <Ionicons name="create-outline" size={20} color={colors.primary} />
            <Text style={styles.actionButtonSecondaryText}>Manual</Text>
          </Pressable>
        </View>

        {isUploading
          ? (
            <View style={styles.uploadingBanner}>
              <ActivityIndicator color={colors.primary} />
              <Text style={styles.uploadingText}>
                Extracting receipt (Experimental V10.6 primary, fallback ready)...
              </Text>
            </View>
          )
          : null}

        {actionError
          ? (
            <View style={styles.errorBanner} accessibilityRole="alert">
              <Ionicons
                name="alert-circle-outline"
                size={18}
                color={colors.error}
              />
              <Text style={styles.errorBannerText}>{actionError}</Text>
            </View>
          )
          : null}

        {cameraPermissionState === "denied" ||
        cameraPermissionState === "permanently_denied" ||
        cameraPermissionState === "expo_go_scoped_denied"
          ? (
            <View
              style={[
                styles.errorBanner,
                { flexDirection: "column", alignItems: "flex-start", gap: spacing.xs },
              ]}
              accessibilityRole="alert"
            >
              <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.xs }}>
                <Ionicons
                  name="camera-outline"
                  size={18}
                  color={colors.error}
                />
                <Text style={[styles.errorBannerText, { fontWeight: "700" }]}>
                  {cameraPermissionState === "expo_go_scoped_denied"
                    ? "Expo Go Permission Cache Blocked"
                    : cameraPermissionState === "permanently_denied"
                      ? "Camera Permission Blocked"
                      : "Camera Permission Needed"}
                </Text>
              </View>
              <Text style={styles.errorBannerText}>
                {cameraPermissionState === "expo_go_scoped_denied"
                  ? "Android Camera permission is enabled for Expo Go, but Expo Go's internal per-project permission cache is still marked denied from earlier. To reset it, clear Expo Go's app storage/cache in Android App Info and reopen this project, or use Upload / Manual Entry below."
                  : cameraPermissionState === "permanently_denied"
                    ? isExpoGo
                      ? "Android has blocked further camera prompts. Because you are testing in Expo Go, camera permission belongs to the Expo Go app rather than GroceryManagement. Open Android Settings for Expo Go and allow Camera, or use Gallery Upload / Manual Entry below."
                      : "Android has blocked further camera prompts. Open Android Settings and enable Camera permission for this app, or use Gallery Upload / Manual Entry below."
                    : "Camera permission is required to capture receipt photos. You can grant access below, or use Gallery Upload / Manual Entry."}
              </Text>
              <View style={{ flexDirection: "row", gap: spacing.sm, marginTop: 4 }}>
                {cameraPermissionState === "permanently_denied" ||
                cameraPermissionState === "expo_go_scoped_denied" ? (
                  <Pressable
                    onPress={() => void Linking.openSettings()}
                    style={styles.actionButtonSecondary}
                    accessibilityRole="button"
                    accessibilityLabel="Open Android Settings for Expo Go"
                  >
                    <Ionicons
                      name="settings-outline"
                      size={16}
                      color={colors.primary}
                    />
                    <Text style={styles.actionButtonSecondaryText}>
                      {cameraPermissionState === "expo_go_scoped_denied"
                        ? "Open Expo Go App Info"
                        : "Open Settings"}
                    </Text>
                  </Pressable>
                ) : (
                  <Pressable
                    onPress={() => void handleCameraCapture()}
                    style={styles.actionButtonSecondary}
                    accessibilityRole="button"
                    accessibilityLabel="Grant camera access"
                  >
                    <Ionicons
                      name="camera-outline"
                      size={16}
                      color={colors.primary}
                    />
                    <Text style={styles.actionButtonSecondaryText}>
                      Grant Camera Access
                    </Text>
                  </Pressable>
                )}
              </View>
            </View>
          )
          : null}

        {isEmpty
          ? (
            <EmptyState
              icon="receipt-outline"
              title="No purchases recorded yet"
              body="Capture a receipt photo, upload a PDF/image, or add groceries manually. Nothing enters your pantry until you review and confirm."
              actionLabel="Add Manual Purchase"
              onAction={openManualEntryModal}
            />
          )
          : (
            <>
              {drafts.length > 0
                ? (
                  <View style={styles.section}>
                    <Text style={styles.sectionTitle}>
                      Pending Review ({drafts.length})
                    </Text>
                    {drafts.map((draft) => (
                      <Pressable
                        key={draft.id}
                        onPress={() => openDraftInModal(draft)}
                        style={({ pressed }) => [
                          styles.card,
                          styles.draftCard,
                          pressed && styles.cardPressed,
                        ]}
                        accessibilityRole="button"
                        accessibilityLabel={`Review draft from ${
                          draft.merchant_name ?? "Unknown merchant"
                        }`}
                      >
                        <View style={styles.cardTopRow}>
                          <View style={styles.cardTitleGroup}>
                            <Text style={styles.cardTitle}>
                              {draft.merchant_name ?? "Receipt Draft"}
                            </Text>
                            <Text style={styles.cardMeta}>
                              {draft.purchased_on ?? "Date pending"} ·{" "}
                              {draft.items?.length ?? 0} items
                            </Text>
                          </View>
                          <View style={styles.draftBadge}>
                            <Text style={styles.draftBadgeText}>Draft</Text>
                          </View>
                        </View>
                        <View style={styles.cardBottomRow}>
                          <Text style={styles.amountText}>
                            {formatInrFromPaise(draft.total_amount_paise ?? 0)}
                          </Text>
                          <View style={styles.unitChipsRow}>
                            <Pressable
                              onPress={() => void handleDiscardDraft(draft.id)}
                              accessibilityRole="button"
                              accessibilityLabel={`Delete draft ${
                                draft.merchant_name ?? "receipt"
                              }`}
                              style={styles.removeLineButton}
                            >
                              <Ionicons
                                name="trash-outline"
                                size={16}
                                color={colors.error}
                              />
                            </Pressable>
                            <Text style={styles.reviewLink}>
                              Review & Confirm →
                            </Text>
                          </View>
                        </View>
                      </Pressable>
                    ))}
                  </View>
                )
                : null}

              {purchases.length > 0
                ? (
                  <View style={styles.section}>
                    <Text style={styles.sectionTitle}>
                      Purchase History ({purchases.length})
                    </Text>
                    {purchases.map((purchase) => (
                      <Pressable
                        key={purchase.id}
                        onPress={() => openConfirmedPurchaseInModal(purchase)}
                        style={({ pressed }) => [
                          styles.card,
                          pressed && styles.cardPressed,
                        ]}
                        accessibilityRole="button"
                        accessibilityLabel={`View purchase from ${
                          purchase.merchant_name ?? "Grocery Purchase"
                        }`}
                      >
                        <View style={styles.cardTopRow}>
                          <View style={styles.cardTitleGroup}>
                            <Text style={styles.cardTitle}>
                              {purchase.merchant_name ?? "Grocery Purchase"}
                            </Text>
                            <Text style={styles.cardMeta}>
                              {purchase.purchased_on}
                              {purchase.receipt_number
                                ? ` · #${purchase.receipt_number}`
                                : ""}
                              {purchase.items
                                ? ` · ${purchase.items.length} items`
                                : ""}
                            </Text>
                          </View>
                          <Text style={styles.amountText}>
                            {formatInrFromPaise(purchase.total_amount_paise)}
                          </Text>
                        </View>
                        {purchase.items && purchase.items.length > 0
                          ? (
                            <View style={styles.purchaseItemsPreview}>
                              {purchase.items.slice(0, 3).map((item) => (
                                <Text
                                  key={item.id}
                                  style={styles.previewItemText}
                                >
                                  • {item.display_name ?? "Item"} (
                                  {formatQuantityWithUnit(
                                    item.normalized_qty,
                                    item.base_unit,
                                  )}
                                  ) —{" "}
                                  {formatInrFromPaise(item.line_total_paise)}
                                </Text>
                              ))}
                            </View>
                          )
                          : null}
                        <View style={styles.cardBottomRow}>
                          <Text style={styles.cardMeta}>
                            Status: {purchase.status}
                            {purchase.receipt_category
                              ? ` · ${purchase.receipt_category}`
                              : ""}
                          </Text>
                          <Text style={styles.reviewLink}>View Details →</Text>
                        </View>
                      </Pressable>
                    ))}
                  </View>
                )
                : null}
            </>
          )}
      </ScrollView>

      <Modal
        visible={modalVisible}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setModalVisible(false)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>
              {viewingConfirmedPurchase
                ? "Purchase Receipt Details"
                : editingDraftId
                ? "Review Purchase Draft"
                : "New Manual Purchase"}
            </Text>
            <Pressable
              onPress={() => setModalVisible(false)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Close modal"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>

          {viewingConfirmedPurchase
            ? (
              <>
                <ScrollView
                  style={styles.modalScroll}
                  contentContainerStyle={styles.modalContent}
                >
                  <View style={styles.warningBanner}>
                    <Ionicons
                      name="lock-closed-outline"
                      size={18}
                      color={colors.textSecondary}
                    />
                    <Text style={styles.warningBannerText}>
                      Status:{" "}
                      {viewingConfirmedPurchase.status.toUpperCase()}. Confirmed
                      financial and inventory ledger records cannot be silently
                      edited or deleted.
                    </Text>
                  </View>

                  <View style={styles.lineCard}>
                    <Text style={styles.sectionTitle}>Receipt Information</Text>
                    <Text style={styles.cardMeta}>
                      Merchant:{" "}
                      {viewingConfirmedPurchase.merchant_name ?? "Unknown"}
                    </Text>
                    <Text style={styles.cardMeta}>
                      Date: {viewingConfirmedPurchase.purchased_on ?? "—"}
                      {viewingConfirmedPurchase.purchased_time
                        ? ` ${viewingConfirmedPurchase.purchased_time}`
                        : ""}
                    </Text>
                    <Text style={styles.cardMeta}>
                      Receipt #:{" "}
                      {viewingConfirmedPurchase.receipt_number ?? "—"}
                    </Text>
                    <Text style={styles.cardMeta}>
                      Category:{" "}
                      {viewingConfirmedPurchase.receipt_category ?? "grocery"}
                      {" "}
                      · Payment:{" "}
                      {viewingConfirmedPurchase.payment_method ?? "—"}
                    </Text>
                    <Text style={styles.cardMeta}>
                      Adapter: {viewingConfirmedPurchase.ocr_adapter ??
                        viewingConfirmedPurchase.source_type}
                      {viewingConfirmedPurchase.ocr_confidence != null
                        ? ` · Confidence: ${
                          Math.round(
                            viewingConfirmedPurchase.ocr_confidence * 100,
                          )
                        }%`
                        : ""}
                    </Text>
                  </View>

                  {(viewingConfirmedPurchase.ocr_warnings?.length ?? 0) > 0
                    ? (
                      <View style={styles.warningBanner}>
                        <Ionicons
                          name="alert-circle-outline"
                          size={18}
                          color={colors.warning}
                        />
                        <Text style={styles.warningBannerText}>
                          {viewingConfirmedPurchase.ocr_warnings?.join(" ")}
                        </Text>
                      </View>
                    )
                    : null}

                  <Text style={styles.sectionTitle}>
                    Items ({viewingConfirmedPurchase.items?.length ?? 0})
                  </Text>
                  {(viewingConfirmedPurchase.items ?? []).map((item, idx) => (
                    <View
                      key={item.id || `view-item-${idx}`}
                      style={styles.lineCard}
                    >
                      <Text style={styles.cardTitle}>
                        {idx + 1}. {item.display_name}
                      </Text>
                      {item.raw_text && item.raw_text !== item.display_name
                        ? (
                          <Text style={styles.cardMeta}>
                            Printed: {item.raw_text}
                          </Text>
                        )
                        : null}
                      <Text style={styles.cardMeta}>
                        Qty: {formatQuantityWithUnit(
                          item.normalized_qty,
                          item.base_unit,
                        )} · Unit Price: {formatInrFromPaise(
                          item.unit_price_paise ?? item.line_total_paise,
                        )} · Line Total:{" "}
                        {formatInrFromPaise(item.line_total_paise)}
                      </Text>
                      <Text style={styles.cardMeta}>
                        Inventory: {item.track_inventory === false
                          ? "Budget Only (Not Added to Pantry)"
                          : "Added to Kitchen Inventory"}
                      </Text>
                    </View>
                  ))}

                  <View style={styles.lineCard}>
                    <Text style={styles.sectionTitle}>Financial Breakdown</Text>
                    <Text style={styles.cardMeta}>
                      Subtotal: {formatInrFromPaise(
                        viewingConfirmedPurchase.subtotal_paise ??
                          viewingConfirmedPurchase.total_amount_paise,
                      )}
                    </Text>
                    <Text style={styles.cardMeta}>
                      Discounts: {formatInrFromPaise(
                        viewingConfirmedPurchase.discount_paise ?? 0,
                      )} · Additional Charges: {formatInrFromPaise(
                        viewingConfirmedPurchase.additional_charges_paise ?? 0,
                      )}
                    </Text>
                    <Text style={styles.cardMeta}>
                      CGST: {formatInrFromPaise(
                        viewingConfirmedPurchase.cgst_paise ?? 0,
                      )} · SGST: {formatInrFromPaise(
                        viewingConfirmedPurchase.sgst_paise ?? 0,
                      )} · IGST: {formatInrFromPaise(
                        viewingConfirmedPurchase.igst_paise ?? 0,
                      )}
                    </Text>
                    <Text style={styles.cardMeta}>
                      Other Tax: {formatInrFromPaise(
                        viewingConfirmedPurchase.other_tax_paise ?? 0,
                      )} · Round-off: {formatInrFromPaise(
                        viewingConfirmedPurchase.rounding_paise ?? 0,
                      )}
                    </Text>
                    <Text style={styles.amountText}>
                      Final Paid: {formatInrFromPaise(
                        viewingConfirmedPurchase.total_amount_paise,
                      )}
                    </Text>
                  </View>

                  {viewingConfirmedPurchase.food_consumption
                    ? (
                      <View style={styles.lineCard}>
                        <Text style={styles.sectionTitle}>
                          Outside Food & Meal Tracking
                        </Text>
                        <Text style={styles.cardMeta}>
                          Meal:{" "}
                          {viewingConfirmedPurchase.food_consumption.meal_type}
                          {" "}
                          · Mode: {viewingConfirmedPurchase.food_consumption
                            .dining_mode}
                          {viewingConfirmedPurchase.food_consumption
                              .people_count
                            ? ` · People: ${viewingConfirmedPurchase.food_consumption.people_count}`
                            : ""}
                        </Text>
                        <Text style={styles.cardMeta}>
                          Food Spend: {formatInrFromPaise(
                            viewingConfirmedPurchase.food_consumption
                              .amount_paise,
                          )} on {viewingConfirmedPurchase.food_consumption
                            .consumed_on}
                        </Text>
                      </View>
                    )
                    : null}
                </ScrollView>

                {modalError
                  ? (
                    <View style={[styles.errorBanner, { marginHorizontal: spacing.lg, marginBottom: spacing.xs }]}>
                      <Ionicons
                        name="alert-circle"
                        size={18}
                        color={colors.error}
                      />
                      <Text style={styles.errorBannerText}>{modalError}</Text>
                    </View>
                  )
                  : null}

                <View style={styles.modalFooter}>
                  <Pressable
                    onPress={() => setModalVisible(false)}
                    style={styles.confirmButton}
                    accessibilityRole="button"
                    accessibilityLabel="Close purchase details"
                  >
                    <Text style={styles.confirmButtonText}>Close</Text>
                  </Pressable>
                </View>
              </>
            )
            : (
              <>
                <ScrollView
                  style={styles.modalScroll}
                  contentContainerStyle={styles.modalContent}
                  keyboardShouldPersistTaps="handled"
                >
                  {modalError
                    ? (
                      <View
                        style={styles.errorBanner}
                        accessibilityRole="alert"
                      >
                        <Ionicons
                          name="alert-circle-outline"
                          size={18}
                          color={colors.error}
                        />
                        <Text style={styles.errorBannerText}>{modalError}</Text>
                      </View>
                    )
                    : null}

                  {liveOcrWarnings.length > 0
                    ? (
                      <View
                        style={styles.warningBanner}
                        accessibilityRole="alert"
                      >
                        <Ionicons
                          name="alert-circle-outline"
                          size={18}
                          color={colors.warning}
                        />
                        <Text style={styles.warningBannerText}>
                          {liveOcrWarnings.join(" ")}
                        </Text>
                      </View>
                    )
                    : null}

                  {draftOrchestration
                    ? (
                      <View style={styles.warningBanner}>
                        <Ionicons
                          name="sparkles-outline"
                          size={18}
                          color={colors.primary}
                        />
                        <Text style={styles.warningBannerText}>
                          {draftOrchestration.fallback_triggered
                            ? `Existing OCR fallback (${draftOrchestration.selected_provider}) · Reason: ${draftOrchestration.fallback_reason ?? "experimental unavailable"}`
                            : `Experimental V10.6 (${draftOrchestration.extraction_status}) · ${Math.round(draftOrchestration.total_latency_ms)} ms`}
                        </Text>
                      </View>
                    )
                    : null}

                  {hasMathMismatch
                    ? (
                      <View
                        style={styles.warningBanner}
                        accessibilityRole="alert"
                      >
                        <Ionicons
                          name="warning-outline"
                          size={18}
                          color={colors.warning}
                        />
                        <Text style={styles.warningBannerText}>
                          Math mismatch: items sum to{" "}
                          {formatInrFromPaise(itemsSumPaise)} vs receipt total
                          {" "}
                          {formatInrFromPaise(headerTotalPaise)} (diff{" "}
                          {formatInrFromPaise(mismatchPaise)}). Verify before
                          confirming.
                        </Text>
                      </View>
                    )
                    : null}

                  <View style={styles.fieldGroup}>
                    <Text style={styles.fieldLabel}>Merchant / Store</Text>
                    <TextInput
                      value={merchantName}
                      onChangeText={setMerchantName}
                      placeholder="e.g. BigBasket, Blinkit, Local Kirana"
                      placeholderTextColor={colors.textMuted}
                      style={styles.input}
                    />
                  </View>

                  <View style={styles.rowFields}>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>Purchase Date</Text>
                      <TextInput
                        value={purchasedOn}
                        onChangeText={setPurchasedOn}
                        placeholder="YYYY-MM-DD"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>Receipt / Bill #</Text>
                      <TextInput
                        value={receiptNumber}
                        onChangeText={setReceiptNumber}
                        placeholder="Optional"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                  </View>

                  <View style={styles.rowFields}>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>Subtotal (₹)</Text>
                      <TextInput
                        value={subtotalRupees}
                        onChangeText={setSubtotalRupees}
                        keyboardType="decimal-pad"
                        placeholder="0.00"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>Discount (₹)</Text>
                      <TextInput
                        value={discountRupees}
                        onChangeText={setDiscountRupees}
                        keyboardType="decimal-pad"
                        placeholder="0.00"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                  </View>

                  <View style={styles.rowFields}>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>CGST (₹)</Text>
                      <TextInput
                        value={cgstRupees}
                        onChangeText={setCgstRupees}
                        keyboardType="decimal-pad"
                        placeholder="0.00"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>SGST (₹)</Text>
                      <TextInput
                        value={sgstRupees}
                        onChangeText={setSgstRupees}
                        keyboardType="decimal-pad"
                        placeholder="0.00"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>IGST (₹)</Text>
                      <TextInput
                        value={igstRupees}
                        onChangeText={setIgstRupees}
                        keyboardType="decimal-pad"
                        placeholder="0.00"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                  </View>

                  <View style={styles.rowFields}>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>Charges (₹)</Text>
                      <TextInput
                        value={additionalChargesRupees}
                        onChangeText={setAdditionalChargesRupees}
                        keyboardType="decimal-pad"
                        placeholder="0.00"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>Round-off (₹)</Text>
                      <TextInput
                        value={roundingRupees}
                        onChangeText={setRoundingRupees}
                        keyboardType="numbers-and-punctuation"
                        placeholder="0.00"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                    <View style={[styles.fieldGroup, styles.flex1]}>
                      <Text style={styles.fieldLabel}>Final Paid (₹)</Text>
                      <TextInput
                        value={receiptTotalRupees}
                        onChangeText={setReceiptTotalRupees}
                        keyboardType="decimal-pad"
                        placeholder="0.00"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                  </View>

                  <View style={styles.itemsHeaderRow}>
                    <Text style={styles.sectionTitle}>
                      Line Items ({lines.length})
                    </Text>
                    <Pressable
                      onPress={() =>
                        setLines((
                          prev,
                        ) => [...prev, makeEmptyLine(prev.length)])}
                      style={styles.addLineButton}
                      accessibilityRole="button"
                      accessibilityLabel="Add line item"
                    >
                      <Ionicons
                        name="add-circle-outline"
                        size={18}
                        color={colors.primary}
                      />
                      <Text style={styles.addLineText}>Add Item</Text>
                    </Pressable>
                  </View>

                  {lines.map((line, idx) => (
                    <View key={line.localKey} style={styles.lineCard}>
                      <View style={styles.lineCardHeader}>
                        <Text style={styles.lineIndexText}>
                          Item #{idx + 1}
                        </Text>
                        <Pressable
                          onPress={() =>
                            removeLine(line.localKey)}
                          accessibilityRole="button"
                          accessibilityLabel={`Remove item ${idx + 1}`}
                          style={styles.removeLineButton}
                        >
                          <Ionicons
                            name="trash-outline"
                            size={18}
                            color={colors.error}
                          />
                        </Pressable>
                      </View>

                      {line.raw_text ? (
                        <View style={styles.rawReceiptBox}>
                          <Text style={styles.rawReceiptLabel}>
                            Original receipt description
                          </Text>
                          <Text style={styles.rawReceiptText}>
                            {line.raw_text}
                          </Text>
                        </View>
                      ) : null}

                      <View style={styles.standardizedHeaderRow}>
                        <Text style={styles.fieldLabel}>
                          Standardised pantry name
                        </Text>
                        {line.ingredient_confidence_tier ? (
                          <View
                            style={[
                              styles.confidenceBadge,
                              line.ingredient_confidence_tier === "HIGH"
                                ? styles.confidenceHigh
                                : line.ingredient_confidence_tier === "MEDIUM"
                                ? styles.confidenceMedium
                                : line.ingredient_confidence_tier === "NON_PANTRY"
                                ? styles.confidenceNeutral
                                : styles.confidenceLow,
                            ]}
                          >
                            <Text style={styles.confidenceBadgeText}>
                              {getIngredientConfidenceLabel(line)}
                            </Text>
                          </View>
                        ) : null}
                      </View>
                      {line.ingredient_family_name ? (
                        <Text style={styles.familyMetaText}>
                          Family: {line.ingredient_family_name}
                          {line.ingredient_category
                            ? ` · ${line.ingredient_category}`
                            : ""}
                        </Text>
                      ) : null}
                      <TextInput
                        value={line.display_name}
                        onChangeText={(text) =>
                          updateLine(line.localKey, {
                            display_name: text,
                            canonical_item_id: matchCatalogItem(text)?.id ??
                              null,
                            canonical_ingredient_id: null,
                            ingredient_variant_id: null,
                            user_approved_ingredient: false,
                            review_reason: null,
                            warnings: [],
                          })}
                        placeholder="e.g. Toned Milk, Whole Wheat Atta, Onion"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />

                      {line.ingredient_review_reason &&
                      !line.user_approved_ingredient ? (
                        <Text style={styles.warningBannerText}>
                          {line.ingredient_review_reason}
                        </Text>
                      ) : null}

                      {line.quantity_confidence_tier &&
                      line.quantity_confidence_tier !== "HIGH" &&
                      line.quantity_review_reason ? (
                        <Text style={styles.warningBannerText}>
                          Qty/Unit ({line.quantity_confidence_tier}):{" "}
                          {line.quantity_review_reason}
                        </Text>
                      ) : null}

                      {line.financial_mismatch && line.financial_review_reason ? (
                        <Text style={styles.warningBannerText}>
                          {line.financial_review_reason}
                        </Text>
                      ) : null}

                      {line.track_inventory && !line.budgetOnly ? (
                        <View style={styles.ingredientActionRow}>
                          {!line.user_approved_ingredient &&
                          (line.standardized_name ||
                            line.display_name.trim().length > 0) ? (
                            <Pressable
                              onPress={() =>
                                updateLine(line.localKey, {
                                  user_approved_ingredient: true,
                                  ingredient_confidence_tier: "HIGH",
                                  ingredient_review_reason: null,
                                })}
                              style={styles.quickActionChip}
                              accessibilityRole="button"
                              accessibilityLabel={`Approve ${line.display_name || "ingredient"}`}
                            >
                              <Text style={styles.quickActionChipText}>
                                Approve
                              </Text>
                            </Pressable>
                          ) : null}
                          <Pressable
                            onPress={() => {
                              if (activePickerLineKey === line.localKey) {
                                setActivePickerLineKey(null);
                                setIngredientSearchText("");
                              } else {
                                setActivePickerLineKey(line.localKey);
                                setIngredientSearchText("");
                              }
                            }}
                            style={styles.quickActionChip}
                            accessibilityRole="button"
                            accessibilityLabel={`Choose ingredient for item ${idx + 1}`}
                          >
                            <Text style={styles.quickActionChipText}>
                              {activePickerLineKey === line.localKey
                                ? "Close List"
                                : "Choose Ingredient"}
                            </Text>
                          </Pressable>
                          <Pressable
                            onPress={() =>
                              updateLine(line.localKey, {
                                track_inventory: false,
                                budgetOnly: true,
                                ingredient_confidence_tier: "NON_PANTRY",
                                ingredient_review_reason: null,
                                budget_treatment: "household_non_food",
                              })}
                            style={styles.quickActionChip}
                            accessibilityRole="button"
                            accessibilityLabel={`Mark item ${idx + 1} as non-pantry`}
                          >
                            <Text style={styles.quickActionChipText}>
                              Mark Non-Pantry
                            </Text>
                          </Pressable>
                        </View>
                      ) : null}

                      {activePickerLineKey === line.localKey ? (
                        <View style={styles.ingredientPickerBox}>
                          {(line.ingredient_suggestions ?? []).length > 0 ? (
                            <View style={styles.unitChipsRow}>
                              {(line.ingredient_suggestions ?? []).map((sug) => (
                                <Pressable
                                  key={sug.variant_id}
                                  onPress={() => {
                                    updateLine(line.localKey, {
                                      display_name: sug.variant_name,
                                      standardized_name: sug.variant_name,
                                      ingredient_family_name: sug.family_name,
                                      ingredient_variant_id: sug.variant_id,
                                      canonical_ingredient_id:
                                        sug.canonical_ingredient_id,
                                      ingredient_confidence_tier: "HIGH",
                                      ingredient_confidence_score: 100,
                                      user_approved_ingredient: true,
                                      ingredient_review_reason: null,
                                    });
                                    setActivePickerLineKey(null);
                                  }}
                                  style={styles.unitChip}
                                >
                                  <Text style={styles.unitChipText}>
                                    {sug.variant_name} ({sug.score}%)
                                  </Text>
                                </Pressable>
                              ))}
                            </View>
                          ) : null}
                          <TextInput
                            value={ingredientSearchText}
                            onChangeText={setIngredientSearchText}
                            placeholder="Search pantry ingredients..."
                            placeholderTextColor={colors.textMuted}
                            style={styles.input}
                          />
                          {ingredientCatalog
                            .filter((opt) =>
                              !ingredientSearchText.trim() ||
                              opt.name
                                .toLowerCase()
                                .includes(
                                  ingredientSearchText.trim().toLowerCase(),
                                )
                            )
                            .slice(0, 6)
                            .map((opt) => (
                              <Pressable
                                key={opt.id}
                                onPress={() => {
                                  updateLine(line.localKey, {
                                    display_name: opt.name,
                                    standardized_name: opt.name,
                                    canonical_ingredient_id: opt.id,
                                    ingredient_category: opt.category,
                                    ingredient_confidence_tier: "HIGH",
                                    ingredient_confidence_score: 100,
                                    user_approved_ingredient: true,
                                    ingredient_review_reason: null,
                                  });
                                  setActivePickerLineKey(null);
                                  setIngredientSearchText("");
                                }}
                                style={styles.pickerOptionRow}
                              >
                                <Text style={styles.pickerOptionText}>
                                  {opt.name}
                                </Text>
                                <Text style={styles.pickerOptionMeta}>
                                  {opt.category} · {opt.standard_unit}
                                </Text>
                              </Pressable>
                            ))}
                        </View>
                      ) : null}

                      <View style={styles.rowFields}>
                        <View style={[styles.fieldGroup, styles.flex1]}>
                          <Text style={styles.fieldLabel}>Qty</Text>
                          <TextInput
                            value={line.qtyText}
                            onChangeText={(text) => {
                              const recomputed = computeLineTotalFromQtyAndRate(
                                text,
                                line.unitPriceRupeesText,
                              );
                              updateLine(line.localKey, {
                                qtyText: text,
                                ...(recomputed !== null
                                  ? { lineTotalRupeesText: recomputed }
                                  : {}),
                              });
                            }}
                            keyboardType="decimal-pad"
                            style={styles.input}
                          />
                        </View>
                        <View style={[styles.fieldGroup, styles.flex1]}>
                          <Text style={styles.fieldLabel}>Unit Price (₹)</Text>
                          <TextInput
                            value={line.unitPriceRupeesText}
                            onChangeText={(text) => {
                              const recomputed = computeLineTotalFromQtyAndRate(
                                line.qtyText,
                                text,
                              );
                              updateLine(line.localKey, {
                                unitPriceRupeesText: text,
                                ...(recomputed !== null
                                  ? { lineTotalRupeesText: recomputed }
                                  : {}),
                              });
                            }}
                            keyboardType="decimal-pad"
                            style={styles.input}
                          />
                        </View>
                        <View style={[styles.fieldGroup, styles.flex1]}>
                          <Text style={styles.fieldLabel}>Line Total (₹)</Text>
                          <TextInput
                            value={line.lineTotalRupeesText}
                            onChangeText={(text) =>
                              updateLine(line.localKey, {
                                lineTotalRupeesText: text,
                              })}
                            keyboardType="decimal-pad"
                            style={styles.input}
                          />
                        </View>
                      </View>

                      <Pressable
                        onPress={() => {
                          setLines((prev) => {
                            const updated = toggleBudgetOnlyForLine(
                              prev,
                              line.localKey,
                              !line.budgetOnly,
                            );
                            if (!updated.some(isExplicitOutsideFoodLine)) {
                              setMealType(null);
                              setDiningMode(null);
                              setPeopleCountText("");
                            }
                            return updated;
                          });
                        }}
                        style={styles.unitChipsRow}
                        accessibilityRole="switch"
                        accessibilityState={{ checked: line.budgetOnly }}
                      >
                        <Ionicons
                          name={line.budgetOnly
                            ? "checkbox-outline"
                            : "square-outline"}
                          size={18}
                          color={colors.primary}
                        />
                        <Text style={styles.fieldLabel}>
                          Budget Only (Prepared Meal / Non-Pantry)
                        </Text>
                      </Pressable>
                      <Pressable
                        onPress={() => {
                          setLines((prev) => {
                            const updated = prev.map((l) =>
                              l.localKey === line.localKey
                                ? {
                                  ...l,
                                  track_inventory: !l.track_inventory,
                                  budgetOnly: false,
                                  classification_status: !l.track_inventory
                                    ? "pantry_eligible"
                                    : l.classification_status,
                                  food_state: !l.track_inventory ? "raw" : l.food_state,
                                  budget_treatment: !l.track_inventory
                                    ? "grocery_inventory"
                                    : l.budget_treatment,
                                  review_reason: !l.track_inventory ? null : l.review_reason,
                                  warnings: !l.track_inventory ? [] : l.warnings,
                                }
                                : l
                            );
                            if (!updated.some(isExplicitOutsideFoodLine)) {
                              setMealType(null);
                              setDiningMode(null);
                              setPeopleCountText("");
                            }
                            return updated;
                          });
                        }}
                        style={styles.unitChipsRow}
                        accessibilityRole="switch"
                        accessibilityState={{ checked: line.track_inventory }}
                      >
                        <Ionicons
                          name={line.track_inventory
                            ? "checkbox-outline"
                            : "square-outline"}
                          size={18}
                          color={colors.primary}
                        />
                        <Text style={styles.fieldLabel}>
                          Add to Kitchen Inventory
                        </Text>
                      </Pressable>
                      {line.classification_status ? (
                        <View style={{ marginBottom: spacing.xs }}>
                          <Text style={styles.fieldLabel}>
                            Classification:{' '}
                            {line.classification_status === 'non_food'
                              ? 'Non-Food (Excluded from Pantry)'
                              : line.classification_status === 'prepared_meal'
                                ? 'Prepared Meal (Outside Food)'
                                : line.classification_status === 'ambiguous_review'
                                  ? 'Needs Review (Not Added Until Resolved)'
                                  : 'Pantry Eligible'}
                          </Text>
                          {line.review_reason ? (
                            <Text style={styles.warningBannerText}>
                              {line.review_reason}
                            </Text>
                          ) : null}
                        </View>
                      ) : null}

                      <Text style={styles.fieldLabel}>Unit</Text>
                      <View style={styles.unitChipsRow}>
                        {UNIT_OPTIONS.map((unitOpt) => {
                          const selected = line.unit === unitOpt;
                          return (
                            <Pressable
                              key={unitOpt}
                              onPress={() =>
                                updateLine(line.localKey, { unit: unitOpt })}
                              style={[
                                styles.unitChip,
                                selected && styles.unitChipSelected,
                              ]}
                              accessibilityRole="button"
                              accessibilityState={{ selected }}
                            >
                              <Text
                                style={[
                                  styles.unitChipText,
                                  selected && styles.unitChipTextSelected,
                                ]}
                              >
                                {unitOpt}
                              </Text>
                            </Pressable>
                          );
                        })}
                      </View>

                      <Text style={styles.fieldLabel}>
                        Expiry Date (Optional)
                      </Text>
                      <TextInput
                        value={line.expiresOnText}
                        onChangeText={(text) =>
                          updateLine(line.localKey, { expiresOnText: text })}
                        placeholder="YYYY-MM-DD"
                        placeholderTextColor={colors.textMuted}
                        style={styles.input}
                      />
                    </View>
                  ))}

                  {lines.some(isExplicitOutsideFoodLine)
                    ? (
                      <View style={styles.lineCard}>
                        <Text style={styles.sectionTitle}>
                          Which meal was this outside food for?
                        </Text>
                        <View style={styles.unitChipsRow}>
                          {MEAL_OPTIONS.map((opt) => {
                            const selected = mealType === opt.value;
                            return (
                              <Pressable
                                key={opt.value}
                                onPress={() => setMealType(opt.value)}
                                style={[
                                  styles.unitChip,
                                  selected && styles.unitChipSelected,
                                ]}
                                accessibilityRole="button"
                                accessibilityState={{ selected }}
                              >
                                <Text
                                  style={[
                                    styles.unitChipText,
                                    selected && styles.unitChipTextSelected,
                                  ]}
                                >
                                  {opt.label}
                                </Text>
                              </Pressable>
                            );
                          })}
                        </View>
                        <Text style={styles.fieldLabel}>Dining Mode</Text>
                        <View style={styles.unitChipsRow}>
                          {DINING_MODES.map((dm) => {
                            const selected = diningMode === dm.value;
                            return (
                              <Pressable
                                key={dm.value}
                                onPress={() => setDiningMode(dm.value)}
                                style={[
                                  styles.unitChip,
                                  selected && styles.unitChipSelected,
                                ]}
                                accessibilityRole="button"
                                accessibilityState={{ selected }}
                              >
                                <Text
                                  style={[
                                    styles.unitChipText,
                                    selected && styles.unitChipTextSelected,
                                  ]}
                                >
                                  {dm.label}
                                </Text>
                              </Pressable>
                            );
                          })}
                        </View>
                        <Text style={styles.fieldLabel}>
                          People Sharing (Optional)
                        </Text>
                        <TextInput
                          value={peopleCountText}
                          onChangeText={setPeopleCountText}
                          keyboardType="number-pad"
                          placeholder="e.g. 2"
                          placeholderTextColor={colors.textMuted}
                          style={styles.input}
                        />
                      </View>
                    )
                    : null}
                </ScrollView>

                {modalError
                  ? (
                    <View style={[styles.errorBanner, { marginHorizontal: spacing.lg, marginBottom: spacing.xs }]}>
                      <Ionicons
                        name="alert-circle"
                        size={18}
                        color={colors.error}
                      />
                      <Text style={styles.errorBannerText}>{modalError}</Text>
                    </View>
                  )
                  : null}

                <View style={styles.modalFooter}>
                  {editingDraftId
                    ? (
                      <Pressable
                        onPress={() => void handleDiscardDraft(editingDraftId)}
                        disabled={isSavingDraft || isConfirming}
                        style={styles.discardButton}
                        accessibilityRole="button"
                        accessibilityLabel="Discard draft"
                      >
                        <Ionicons
                          name="trash-outline"
                          size={18}
                          color={colors.error}
                        />
                      </Pressable>
                    )
                    : null}

                  <Pressable
                    onPress={() => void handleSaveDraft()}
                    disabled={isSavingDraft || isConfirming}
                    style={[
                      styles.saveDraftButton,
                      (isSavingDraft || isConfirming) && styles.buttonDisabled,
                    ]}
                    accessibilityRole="button"
                    accessibilityLabel="Save draft"
                  >
                    {isSavingDraft
                      ? <ActivityIndicator color={colors.primary} />
                      : <Text style={styles.saveDraftText}>Save Draft</Text>}
                  </Pressable>

                  <Pressable
                    onPress={() => void handleConfirmPurchase()}
                    disabled={isSavingDraft || isConfirming}
                    style={[
                      styles.confirmButton,
                      (isSavingDraft || isConfirming) && styles.buttonDisabled,
                    ]}
                    accessibilityRole="button"
                    accessibilityLabel="Confirm purchase into inventory"
                  >
                    {isConfirming
                      ? <ActivityIndicator color={colors.textOnPrimary} />
                      : (
                        <Text style={styles.confirmButtonText}>
                          Confirm Purchase
                        </Text>
                      )}
                  </Pressable>
                </View>
              </>
            )}
        </View>
      </Modal>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  header: {
    marginBottom: spacing.md,
    gap: spacing.xs,
  },
  title: {
    fontSize: 24,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  subtitle: {
    fontSize: 14,
    color: colors.textSecondary,
  },
  actionRow: {
    flexDirection: "row",
    gap: spacing.sm,
    marginBottom: spacing.lg,
  },
  actionButtonPrimary: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.xs,
    backgroundColor: colors.primary,
    minHeight: layout.minTapTarget,
    borderRadius: radii.md,
    paddingHorizontal: spacing.md,
  },
  actionButtonPrimaryText: {
    color: colors.textOnPrimary,
    fontSize: 14,
    fontWeight: "600",
  },
  actionButtonSecondary: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.xs,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    minHeight: layout.minTapTarget,
    borderRadius: radii.md,
    paddingHorizontal: spacing.md,
  },
  actionButtonSecondaryText: {
    color: colors.primary,
    fontSize: 14,
    fontWeight: "600",
  },
  buttonPressed: {
    backgroundColor: colors.primaryPressed,
  },
  secondaryPressed: {
    backgroundColor: colors.surfaceMuted,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  uploadingBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.primaryLight,
    padding: spacing.md,
    borderRadius: radii.md,
    marginBottom: spacing.md,
  },
  uploadingText: {
    fontSize: 14,
    fontWeight: "600",
    color: colors.primary,
  },
  errorBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.errorBg,
    borderColor: colors.errorBorder,
    borderWidth: 1,
    padding: spacing.md,
    borderRadius: radii.md,
    marginBottom: spacing.md,
  },
  errorBannerText: {
    flex: 1,
    fontSize: 13,
    color: colors.error,
  },
  warningBanner: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.sm,
    backgroundColor: colors.warningBg,
    borderColor: colors.warning,
    borderWidth: 1,
    padding: spacing.md,
    borderRadius: radii.md,
    marginBottom: spacing.md,
  },
  warningBannerText: {
    flex: 1,
    fontSize: 13,
    color: colors.warning,
    lineHeight: 18,
  },
  section: {
    marginBottom: spacing.xl,
    gap: spacing.sm,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.textSecondary,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  draftCard: {
    borderColor: colors.primary,
  },
  cardPressed: {
    backgroundColor: colors.surfaceMuted,
  },
  cardTopRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: spacing.md,
  },
  cardTitleGroup: {
    flex: 1,
    gap: 2,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  cardMeta: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  draftBadge: {
    backgroundColor: colors.warningBg,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.full,
  },
  draftBadgeText: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.warning,
  },
  cardBottomRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: spacing.xs,
  },
  amountText: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  reviewLink: {
    fontSize: 14,
    fontWeight: "600",
    color: colors.primary,
  },
  purchaseItemsPreview: {
    marginTop: spacing.xs,
    gap: 2,
  },
  previewItemText: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  modalContainer: {
    flex: 1,
    backgroundColor: colors.background,
  },
  modalHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    backgroundColor: colors.surface,
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  closeButton: {
    minWidth: layout.minTapTarget,
    minHeight: layout.minTapTarget,
    alignItems: "center",
    justifyContent: "center",
  },
  modalScroll: {
    flex: 1,
  },
  modalContent: {
    padding: spacing.lg,
    gap: spacing.md,
  },
  fieldGroup: {
    gap: spacing.xs,
  },
  fieldLabel: {
    fontSize: 12,
    fontWeight: "600",
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
  rowFields: {
    flexDirection: "row",
    gap: spacing.md,
  },
  flex1: {
    flex: 1,
  },
  itemsHeaderRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: spacing.sm,
  },
  addLineButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    minHeight: layout.minTapTarget,
    paddingHorizontal: spacing.sm,
  },
  addLineText: {
    fontSize: 14,
    fontWeight: "600",
    color: colors.primary,
  },
  lineCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.md,
    padding: spacing.md,
    gap: spacing.sm,
  },
  lineCardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  lineIndexText: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  removeLineButton: {
    minWidth: 36,
    minHeight: 36,
    alignItems: "center",
    justifyContent: "center",
  },
  unitChipsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
  },
  unitChip: {
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.full,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    backgroundColor: colors.surfaceMuted,
  },
  unitChipSelected: {
    borderColor: colors.primary,
    backgroundColor: colors.primaryLight,
  },
  unitChipText: {
    fontSize: 13,
    fontWeight: "600",
    color: colors.textSecondary,
  },
  unitChipTextSelected: {
    color: colors.primary,
  },
  modalFooter: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    padding: spacing.lg,
    backgroundColor: colors.surface,
    borderTopColor: colors.border,
    borderTopWidth: 1,
  },
  discardButton: {
    minWidth: layout.minTapTarget,
    minHeight: layout.minTapTarget,
    borderRadius: radii.md,
    borderColor: colors.errorBorder,
    borderWidth: 1,
    backgroundColor: colors.errorBg,
    alignItems: "center",
    justifyContent: "center",
  },
  saveDraftButton: {
    flex: 1,
    minHeight: layout.minTapTarget,
    borderRadius: radii.md,
    borderColor: colors.primary,
    borderWidth: 1,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  saveDraftText: {
    fontSize: 15,
    fontWeight: "600",
    color: colors.primary,
  },
  confirmButton: {
    flex: 1.4,
    minHeight: layout.minTapTarget,
    borderRadius: radii.md,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  confirmButtonText: {
    fontSize: 15,
    fontWeight: "600",
    color: colors.textOnPrimary,
  },
  rawReceiptBox: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    marginBottom: spacing.xs,
  },
  rawReceiptLabel: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.textMuted,
  },
  rawReceiptText: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  standardizedHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.xs,
    marginBottom: 4,
  },
  confidenceBadge: {
    borderRadius: radii.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  confidenceHigh: {
    backgroundColor: "#DCFCE7",
  },
  confidenceMedium: {
    backgroundColor: "#FEF3C7",
  },
  confidenceLow: {
    backgroundColor: "#FEE2E2",
  },
  confidenceNeutral: {
    backgroundColor: colors.surfaceMuted,
  },
  confidenceBadgeText: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  familyMetaText: {
    fontSize: 12,
    color: colors.textSecondary,
    marginBottom: 4,
  },
  ingredientActionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
    marginTop: spacing.xs,
    marginBottom: spacing.xs,
  },
  quickActionChip: {
    borderColor: colors.primary,
    borderWidth: 1,
    borderRadius: radii.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    backgroundColor: colors.primaryLight,
  },
  quickActionChipText: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.primary,
  },
  ingredientPickerBox: {
    marginTop: spacing.xs,
    marginBottom: spacing.sm,
    padding: spacing.sm,
    borderRadius: radii.md,
    borderColor: colors.border,
    borderWidth: 1,
    backgroundColor: colors.surfaceMuted,
    gap: spacing.xs,
  },
  pickerOptionRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 6,
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
  },
  pickerOptionText: {
    fontSize: 13,
    fontWeight: "600",
    color: colors.textPrimary,
  },
  pickerOptionMeta: {
    fontSize: 11,
    color: colors.textMuted,
  },
});
