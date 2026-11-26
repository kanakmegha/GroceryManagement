import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams } from "expo-router";
import { useQueryClient } from "@tanstack/react-query";
import { useApiClient } from "../auth/useApiClient";
import {
  householdQueryKeys,
  invalidateHouseholdData,
} from "../../shared/api/queryKeys";
import { useHousehold } from "../auth/HouseholdContext";
import {
  AiProviderStatus,
  BaseUnit,
  CompleteCookingPlanItemInput,
  CompleteCookingPlanResponse,
  CookingPlan,
  CookingPlansListResponse,
  CreateCookingPlanRequest,
  HouseholdRecipePreferences,
  InventoryLot,
  InventoryLotsResponse,
  MealFeedbackRequest,
  MealHistoryItem,
  MealHistoryListResponse,
  RecipeChatExternalLink,
  RecipeChatRequest,
  RecipeChatResponse,
  RecipeChatSuggestion,
  RecipeRecommendation,
  RecipeRecommendationsResponse,
  RecommendationMode,
} from "../../shared/api/types";
import { ApiClientError } from "../../shared/api/client";
import {
  buildSessionAvailabilityRecord,
  evaluateReportedAvailability,
  getCompatibleUnitsForUnit,
  ReportedAvailabilityEntry,
  ReportedUnit,
  toCanonicalBaseUnit,
} from "./pantryReconciliation";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  ScreenContainer,
} from "../../shared/components";
import {
  createIdempotencyKey,
  formatQuantityWithUnit,
} from "../../shared/format/units";
import { colors, layout, radii, spacing } from "../../shared/theme/tokens";
import {
  AI_CHEF_CHAT_TIMEOUT_MS,
  areUnitsCompatible,
  buildAskChefPayload,
  buildCompleteLineId,
  buildFallbackCookingDetail,
  buildSelectionOnlyPrompt,
  CompletePlanLineState,
  computeCombinedDeductionByCanonical,
  computeCookingProgress,
  convertCompatibleQty,
  detectStepMinutes,
  groupMealHistoryByDate,
  isCompletePlanLineUnresolved,
  mapChatResponseToTurn,
  RecipeDetailTarget,
  reconcileCompletePlanLines,
  recommendationToDetail,
  resolveCookingStep,
  scaleQuantityText,
  stepCookingIndex,
  suggestionToDetail,
} from "./recipeHelpers";

interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
  providerStatus?: AiProviderStatus;
  providerMessage?: string | null;
  followUpQuestions?: string[];
  suggestions?: RecipeChatSuggestion[];
  externalLinks?: RecipeChatExternalLink[];
}

interface MissingResolutionTarget {
  key: string;
  name: string;
  canonical_item_id: string | null;
  requiredQty: number;
  unit: string;
  mode: "have" | "bought";
}

const CONSTRAINT_OPTIONS = [
  "High protein",
  "Keto",
  "Comforting",
  "Sweet",
  "Savoury",
  "Spicy",
  "Healthy",
  "Light meal",
  "South Indian",
  "North Indian",
  "Quick meal",
  "Something different",
] as const;

const HUNGER_LEVELS = [
  "Light appetite",
  "Moderately hungry",
  "Very hungry",
] as const;

const COOKING_TIME_OPTIONS = [
  { label: "15 min", minutes: 15 },
  { label: "30 min", minutes: 30 },
  { label: "45 min", minutes: 45 },
  { label: "60+ min", minutes: 60 },
] as const;

function isSafeHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

export function RecipesScreen() {
  const apiClient = useApiClient();
  const queryClient = useQueryClient();
  const { activeHouseholdId } = useHousehold();
  const routeParams = useLocalSearchParams<{
    recipeId?: string;
    openCooking?: string;
    navSeq?: string;
    recipePayload?: string;
    confirmedKeys?: string;
  }>();
  const lastHandledNavSeqRef = useRef<string | null>(null);
  const [userConfirmedByRecipe, setUserConfirmedByRecipe] = useState<
    Record<string, Record<string, boolean>>
  >({});

  const toggleRecipeUserConfirmed = useCallback((recipeKey: string, ingName: string) => {
    const norm = ingName.trim().toLowerCase();
    setUserConfirmedByRecipe((prev) => {
      const curr = prev[recipeKey] ?? {};
      return {
        ...prev,
        [recipeKey]: {
          ...curr,
          [norm]: !curr[norm],
        },
      };
    });
  }, []);

  const [aiChefVisible, setAiChefVisible] = useState(false);
  const [hungerLevel, setHungerLevel] = useState<string | null>(null);
  const [cookingTimeMinutes, setCookingTimeMinutes] = useState<number | null>(
    null,
  );
  const [historyLimit, setHistoryLimit] = useState<number>(20);
  const [historyTotal, setHistoryTotal] = useState<number>(0);
  const [missingResolutions, setMissingResolutions] = useState<
    Record<string, "has_unrecorded" | "willing_to_buy">
  >({});

  const [activePlans, setActivePlans] = useState<CookingPlan[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const hasLoadedRef = useRef(false);
  const inFlightRef = useRef(false);

  // Conversational AI Chef state
  const [chatInput, setChatInput] = useState("");
  const [servings, setServings] = useState(2);
  const [selectedConstraints, setSelectedConstraints] = useState<string[]>([]);
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [isSendingChat, setIsSendingChat] = useState(false);
  const isSendingChatRef = useRef(false);
  const [latestProviderStatus, setLatestProviderStatus] = useState<
    AiProviderStatus | null
  >(null);
  const [latestProviderMessage, setLatestProviderMessage] = useState<
    string | null
  >(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);
  const [recommendationMode, setRecommendationMode] = useState<RecommendationMode>("balanced");
  const [showPersonalisationPrompt, setShowPersonalisationPrompt] = useState(false);
  const [isSavingPrefMode, setIsSavingPrefMode] = useState(false);

  // Cooking plan actions
  const [isStartingPlan, setIsStartingPlan] = useState(false);
  const [isCancellingPlanId, setIsCancellingPlanId] = useState<string | null>(
    null,
  );

  // Complete Meal Modal
  const [completingPlan, setCompletingPlan] = useState<CookingPlan | null>(
    null,
  );
  const [completeLines, setCompleteLines] = useState<CompletePlanLineState[]>(
    [],
  );
  const [isCompleting, setIsCompleting] = useState(false);
  const [completeError, setCompleteError] = useState<string | null>(null);
  const [activeLots, setActiveLots] = useState<InventoryLot[]>([]);

  // Missing-Ingredient Resolution ("I have it" / "I bought it" / "Need to buy")
  const [resolveTarget, setResolveTarget] = useState<
    MissingResolutionTarget | null
  >(null);
  const [resolveNameText, setResolveNameText] = useState("");
  const [resolveQtyText, setResolveQtyText] = useState("");
  const [resolveUnit, setResolveUnit] = useState<ReportedUnit>("g");
  const [resolvePriceText, setResolvePriceText] = useState("");
  const [resolveMerchantText, setResolveMerchantText] = useState("");
  const [resolveNoteText, setResolveNoteText] = useState("");
  const [resolvePersistToPantry, setResolvePersistToPantry] = useState(false);
  const [sessionReportedAvailability, setSessionReportedAvailability] = useState<
    Record<string, ReportedAvailabilityEntry>
  >({});
  const [isResolvingIngredient, setIsResolvingIngredient] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);
  const [shoppingRequirements, setShoppingRequirements] = useState<
    Record<string, { name: string; missingQty: string; unit: string }>
  >({});
  const [acceptedSubstitutions, setAcceptedSubstitutions] = useState<
    Record<string, boolean>
  >({});

  // Meal History & Feedback
  const [mealHistory, setMealHistory] = useState<MealHistoryItem[]>([]);
  const [frequentlyCooked, setFrequentlyCooked] = useState<string[]>([]);
  const [favouriteMeals, setFavouriteMeals] = useState<string[]>([]);
  const [dislikedMeals, setDislikedMeals] = useState<string[]>([]);
  const [historyMealTypeFilter, setHistoryMealTypeFilter] = useState<string>(
    "all",
  );
  const [historyRatingFilter, setHistoryRatingFilter] = useState<
    "all" | "liked" | "disliked"
  >("all");
  const [historyDateFilter, setHistoryDateFilter] = useState<
    "all" | "7d" | "30d"
  >("all");
  const [selectedMealDetail, setSelectedMealDetail] = useState<
    MealHistoryItem | null
  >(null);
  const [feedbackModalMealId, setFeedbackModalMealId] = useState<string | null>(
    null,
  );
  const [feedbackModalTitle, setFeedbackModalTitle] = useState<string>("");
  const [feedbackRating, setFeedbackRating] = useState<number | null>(null);
  const [feedbackTags, setFeedbackTags] = useState<string[]>([]);
  const [feedbackComment, setFeedbackComment] = useState<string>("");
  const [feedbackWouldMakeAgain, setFeedbackWouldMakeAgain] = useState<
    boolean | null
  >(null);
  const [isSavingFeedback, setIsSavingFeedback] = useState<boolean>(false);

  // Recipe Detail & Guided Cooking Mode
  const [selectedRecipeDetail, setSelectedRecipeDetail] = useState<
    RecipeDetailTarget | null
  >(null);
  const [detailServings, setDetailServings] = useState<number>(2);
  const [guidedSession, setGuidedSession] = useState<
    {
      plan: CookingPlan;
      detail: RecipeDetailTarget;
    } | null
  >(null);
  const [guidedProgressByPlanId, setGuidedProgressByPlanId] = useState<
    Record<string, { stepIndex: number; servings: number; reviewAll: boolean; showIngredients?: boolean }>
  >({});
  const [recipeDetailsByPlanId, setRecipeDetailsByPlanId] = useState<
    Record<string, RecipeDetailTarget>
  >({});
  const [timerRemainingSec, setTimerRemainingSec] = useState<number>(0);
  const [timerRunning, setTimerRunning] = useState<boolean>(false);
  const [showFinishPrompt, setShowFinishPrompt] = useState<boolean>(false);

  useEffect(() => {
    if (!timerRunning || timerRemainingSec <= 0) {
      if (timerRemainingSec <= 0 && timerRunning) {
        setTimerRunning(false);
      }
      return;
    }
    const id = setInterval(() => {
      setTimerRemainingSec((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(id);
  }, [timerRemainingSec, timerRunning]);

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
        const historyParams = new URLSearchParams({
          limit: String(historyLimit),
        });
        if (historyMealTypeFilter !== "all") {
          historyParams.set("meal_type", historyMealTypeFilter);
        }
        if (historyRatingFilter === "liked") {
          historyParams.set("min_rating", "4");
        } else if (historyRatingFilter === "disliked") {
          historyParams.set("max_rating", "2");
        }
        if (historyDateFilter !== "all") {
          const days = historyDateFilter === "7d" ? 7 : 30;
          const d = new Date(Date.now() - days * 86400000);
          historyParams.set("date_from", d.toISOString().slice(0, 10));
        }

        const [plansRes, historyRes, lotsRes, prefsRes] = await Promise.all([
          apiClient.get<CookingPlansListResponse>(
            "/api/v1/cooking-plans?status=planned&limit=10",
          ),
          apiClient.get<MealHistoryListResponse>(
            `/api/v1/meals/history?${historyParams.toString()}`,
          ),
          apiClient
            .get<InventoryLotsResponse>("/api/v1/inventory/lots?status=active")
            .catch(() => ({ items: [] as InventoryLot[] })),
          apiClient
            .get<HouseholdRecipePreferences>("/api/v1/recipes/preferences")
            .catch(() => null),
        ]);
        setActivePlans(plansRes.items ?? []);
        setMealHistory(historyRes.items ?? []);
        setActiveLots(lotsRes.items ?? []);
        if (prefsRes) {
          if (prefsRes.recommendation_mode) {
            setRecommendationMode(prefsRes.recommendation_mode);
          }
          setShowPersonalisationPrompt(
            Boolean(prefsRes.should_show_personalisation_prompt),
          );
        }
        setHistoryTotal(historyRes.total ?? (historyRes.items?.length ?? 0));
        setFrequentlyCooked(historyRes.frequently_cooked ?? []);
        setFavouriteMeals(historyRes.favourite_meals ?? []);
        setDislikedMeals(historyRes.disliked_meals ?? []);
        hasLoadedRef.current = true;
      } catch (err) {
        const message = err instanceof ApiClientError
          ? err.message
          : "Unable to load meal history and cooking plans.";
        setLoadError(message);
      } finally {
        inFlightRef.current = false;
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [
      apiClient,
      historyDateFilter,
      historyLimit,
      historyMealTypeFilter,
      historyRatingFilter,
    ],
  );

  useEffect(() => {
    void loadData(false);
  }, [loadData, activeHouseholdId]);

  const toggleConstraint = useCallback((label: string) => {
    setSelectedConstraints((prev) =>
      prev.includes(label)
        ? prev.filter((item) => item !== label)
        : [...prev, label]
    );
  }, []);

  const handleOpenVerifiedLink = useCallback(async (url: string) => {
    if (!isSafeHttpUrl(url)) {
      setActionError("Blocked unsafe external link protocol.");
      return;
    }
    try {
      await Linking.openURL(url);
    } catch {
      setActionError("Unable to open external recipe link.");
    }
  }, []);

  const handleUpdateRecommendationMode = useCallback(
    async (
      mode: RecommendationMode | null,
      dismissOnly = false,
    ) => {
      if (isSavingPrefMode) return;
      setIsSavingPrefMode(true);
      try {
        const updated = await apiClient.request<HouseholdRecipePreferences>(
          "/api/v1/recipes/preferences/recommendation-mode",
          {
            method: "PATCH",
            body: dismissOnly
              ? { dismiss_personalisation_prompt: true }
              : {
                recommendation_mode: mode,
                dismiss_personalisation_prompt: true,
              },
          },
        );
        if (updated.recommendation_mode) {
          setRecommendationMode(updated.recommendation_mode);
        }
        setShowPersonalisationPrompt(
          Boolean(updated.should_show_personalisation_prompt),
        );
      } catch {
        // Non-blocking preference update
      } finally {
        setIsSavingPrefMode(false);
      }
    },
    [apiClient, isSavingPrefMode],
  );

  const handleSendChat = useCallback(async (overrideMessage?: string) => {
    const promptText = overrideMessage?.trim()
      ? overrideMessage.trim()
      : buildSelectionOnlyPrompt({
        customText: chatInput,
        preferences: selectedConstraints,
        hungerLevel,
        maxMinutes: cookingTimeMinutes,
        servings,
      });
    if (!promptText || isSendingChat || isSendingChatRef.current) {
      return;
    }

    setChatError(null);
    const userTurn: ChatTurn = {
      id: `user-${Date.now()}`,
      role: "user",
      text: promptText,
    };
    setChatTurns((prev) => [...prev, userTurn]);
    if (!overrideMessage) {
      setChatInput("");
    }
    isSendingChatRef.current = true;
    setIsSendingChat(true);

    try {
      const payload = buildAskChefPayload({
        message: promptText,
        servings,
        selectedConstraints,
        hungerLevel,
        cookingTimeMinutes,
        chatTurns,
        recommendationMode,
      });
      const res = await apiClient.post<RecipeChatResponse>(
        "/api/v1/recipes/chat",
        payload,
        { timeoutMs: AI_CHEF_CHAT_TIMEOUT_MS },
      );
      const mapped = mapChatResponseToTurn(res);
      setLatestProviderStatus(mapped.providerStatus);
      setLatestProviderMessage(mapped.providerMessage);

      const assistantTurn: ChatTurn = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        text: mapped.text,
        providerStatus: mapped.providerStatus,
        providerMessage: mapped.providerMessage,
        followUpQuestions: mapped.followUpQuestions,
        suggestions: mapped.suggestions,
        externalLinks: mapped.externalLinks,
      };
      setChatTurns((prev) => [...prev, assistantTurn]);
    } catch (err) {
      const message = err instanceof ApiClientError
        ? err.message
        : "AI recipe chat request failed.";
      setChatError(message);
    } finally {
      isSendingChatRef.current = false;
      setIsSendingChat(false);
    }
  }, [
    apiClient,
    chatInput,
    chatTurns,
    cookingTimeMinutes,
    hungerLevel,
    isSendingChat,
    recommendationMode,
    selectedConstraints,
    servings,
  ]);

  const openRecipeDetail = useCallback((detail: RecipeDetailTarget) => {
    setSelectedRecipeDetail(detail);
    setDetailServings(detail.servings || servings || 2);
  }, [servings]);

  useEffect(() => {
    const targetId = routeParams.recipeId?.trim() || "";
    const payloadStr = routeParams.recipePayload?.trim() || "";
    const navKey = routeParams.navSeq || (targetId ? `id:${targetId}` : "");
    if (!navKey || lastHandledNavSeqRef.current === navKey) {
      return;
    }

    let resolved: RecipeDetailTarget | null = null;
    if (payloadStr) {
      try {
        resolved = JSON.parse(payloadStr) as RecipeDetailTarget;
      } catch {
        resolved = null;
      }
    }

    const applyResolved = (target: RecipeDetailTarget) => {
      lastHandledNavSeqRef.current = navKey;
      setGuidedSession(null);
      if (routeParams.confirmedKeys) {
        try {
          const keys = JSON.parse(routeParams.confirmedKeys) as string[];
          if (Array.isArray(keys) && keys.length > 0) {
            const rKey = target.recipe_id ?? target.title;
            setUserConfirmedByRecipe((prev) => ({
              ...prev,
              [rKey]: {
                ...(prev[rKey] ?? {}),
                ...Object.fromEntries(keys.map((k) => [k.trim().toLowerCase(), true])),
              },
            }));
          }
        } catch {
          // Ignore malformed confirmedKeys param
        }
      }
      openRecipeDetail(target);
    };

    if (resolved) {
      applyResolved(resolved);
      return;
    }

    if (targetId) {
      let cancelled = false;
      void apiClient
        .get<{ items?: RecipeRecommendation[] }>(
          "/api/v1/recipes/recommendations?limit=8&include_references=false&mode=progressive",
        )
        .then((res) => {
          if (cancelled) return;
          const match = (res.items ?? []).find((r) => r.recipe_id === targetId);
          if (match) {
            applyResolved(recommendationToDetail(match));
          }
        })
        .catch(() => {
          // Non-fatal fallback
        });
      return () => {
        cancelled = true;
      };
    }
  }, [
    apiClient,
    openRecipeDetail,
    routeParams.confirmedKeys,
    routeParams.navSeq,
    routeParams.recipeId,
    routeParams.recipePayload,
  ]);

  const handleStartCookingFromDetail = useCallback(
    async (detail: RecipeDetailTarget, targetServings: number) => {
      if (isStartingPlan) {
        return;
      }
      setIsStartingPlan(true);
      setActionError(null);
      try {
        const mappedItems = detail.ingredients
          .filter(
            (ing): ing is typeof ing & { canonical_item_id: string } =>
              ing.available &&
              typeof ing.canonical_item_id === "string" &&
              ing.canonical_item_id.length > 0,
          )
          .map((ing) => {
            const scaled = scaleQuantityText(
              ing.quantity,
              detail.servings || 2,
              targetServings,
            );
            const numMatch = scaled.match(/\d+(?:\.\d+)?/);
            return {
              canonical_item_id: ing.canonical_item_id,
              name: ing.name,
              planned_qty: numMatch ? numMatch[0] : "1",
              base_unit: (["g", "ml", "pcs"].includes(ing.base_unit)
                ? ing.base_unit
                : "g") as BaseUnit,
            };
          });

        const body: CreateCookingPlanRequest = {
          recipe_id: detail.recipe_id ?? undefined,
          title: detail.title,
          planned_servings: targetServings,
          meal_type: detail.meal_type ?? undefined,
          prep_minutes: detail.prep_minutes ?? undefined,
          cook_minutes: detail.cook_minutes ?? undefined,
          description: detail.description ?? undefined,
          steps: detail.instructions,
          substitutions: detail.substitutions,
          items: mappedItems.length > 0 ? mappedItems : undefined,
        };
        const createdPlan = await apiClient.post<CookingPlan>(
          "/api/v1/cooking-plans",
          body,
        );
        setRecipeDetailsByPlanId((prev) => ({
          ...prev,
          [createdPlan.id]: detail,
        }));
        setGuidedProgressByPlanId((prev) => ({
          ...prev,
          [createdPlan.id]: {
            stepIndex: 0,
            servings: targetServings,
            reviewAll: false,
          },
        }));
        setSelectedRecipeDetail(null);
        setShowFinishPrompt(false);
        setTimerRunning(false);
        setTimerRemainingSec(0);
        setGuidedSession({ plan: createdPlan, detail });
        await loadData(true);
      } catch (err) {
        const message = err instanceof ApiClientError
          ? err.message
          : "Could not start cooking plan.";
        setActionError(message);
      } finally {
        setIsStartingPlan(false);
      }
    },
    [apiClient, isStartingPlan, loadData],
  );

  const getLiveIngredientStatus = useCallback(
    (
      ing: {
        name?: string;
        canonical_item_id?: string | null;
        quantity: string;
        base_unit: string;
        available?: boolean;
      },
      scaledQtyStr: string,
      itemKey: string,
    ) => {
      const todayIso = new Date().toISOString().slice(0, 10);
      const reqNum = Number(scaledQtyStr) || 0;
      const matchingLots = activeLots.filter(
        (lot) =>
          lot.status === "active" &&
          Number(lot.remaining_qty) > 0 &&
          (!lot.expires_on || lot.expires_on >= todayIso) &&
          (ing.canonical_item_id
            ? lot.canonical_item_id === ing.canonical_item_id
            : Boolean(
              ing.name &&
                lot.item_name &&
                lot.item_name
                  .toLowerCase()
                  .includes(ing.name.trim().toLowerCase()),
            )),
      );
      const displayName = (ing.name && ing.name.trim()) ||
        matchingLots[0]?.item_name ||
        "Ingredient";

      if (acceptedSubstitutions[itemKey]) {
        return {
          displayName,
          requiredQty: reqNum,
          availableQty: 0,
          availableUnit: ing.base_unit,
          unitCompatible: true,
          missingQty: 0,
          isResolved: true,
          statusLabel: "Substitution accepted",
          isOk: true,
        };
      }

      if (matchingLots.length === 0) {
        const shopReq = shoppingRequirements[itemKey];
        return {
          displayName,
          requiredQty: reqNum,
          availableQty: 0,
          availableUnit: ing.base_unit,
          unitCompatible: true,
          missingQty: reqNum,
          isResolved: false,
          statusLabel: shopReq
            ? `Need to buy (${formatQuantityWithUnit(reqNum, ing.base_unit)})`
            : `${formatQuantityWithUnit(reqNum, ing.base_unit)} missing`,
          isOk: false,
        };
      }

      const compatibleLots = matchingLots.filter((lot) =>
        areUnitsCompatible(lot.base_unit, ing.base_unit)
      );
      if (compatibleLots.length === 0) {
        const lotUnit = matchingLots[0].base_unit;
        const rawAvail = matchingLots.reduce(
          (s, l) => s + (Number(l.remaining_qty) || 0),
          0,
        );
        return {
          displayName,
          requiredQty: reqNum,
          availableQty: rawAvail,
          availableUnit: lotUnit,
          unitCompatible: false,
          missingQty: reqNum,
          isResolved: false,
          statusLabel: `Unit mismatch (pantry ${
            formatQuantityWithUnit(rawAvail, lotUnit)
          } vs recipe ${formatQuantityWithUnit(reqNum, ing.base_unit)})`,
          isOk: false,
        };
      }

      const availInReqUnit = compatibleLots.reduce((sum, lot) => {
        const q = Number(lot.remaining_qty) || 0;
        return (
          sum +
          (convertCompatibleQty(q, lot.base_unit, ing.base_unit) ?? q)
        );
      }, 0);

      if (availInReqUnit + 1e-6 >= reqNum) {
        return {
          displayName,
          requiredQty: reqNum,
          availableQty: availInReqUnit,
          availableUnit: ing.base_unit,
          unitCompatible: true,
          missingQty: 0,
          isResolved: true,
          statusLabel: "Available",
          isOk: true,
        };
      }

      const missing = Math.max(0, reqNum - availInReqUnit);
      const sessionRep = sessionReportedAvailability[itemKey];
      if (sessionRep) {
        if (sessionRep.status === "sufficient") {
          return {
            displayName,
            requiredQty: reqNum,
            availableQty: sessionRep.availableInRequiredUnit,
            availableUnit: ing.base_unit,
            unitCompatible: true,
            missingQty: 0,
            isResolved: true,
            statusLabel: sessionRep.persistedToPantry
              ? "Available (saved to pantry)"
              : `Session-reported (${formatQuantityWithUnit(sessionRep.availableInRequiredUnit, ing.base_unit)})`,
            isOk: true,
          };
        }
        if (sessionRep.status === "insufficient") {
          return {
            displayName,
            requiredQty: reqNum,
            availableQty: sessionRep.availableInRequiredUnit,
            availableUnit: ing.base_unit,
            unitCompatible: true,
            missingQty: sessionRep.shortageQty,
            isResolved: false,
            statusLabel: `Session-reported: need ${formatQuantityWithUnit(sessionRep.shortageQty, ing.base_unit)} more`,
            isOk: false,
          };
        }
        return {
          displayName,
          requiredQty: reqNum,
          availableQty: 0,
          availableUnit: ing.base_unit,
          unitCompatible: true,
          missingQty: reqNum,
          isResolved: false,
          statusLabel:
            sessionRep.status === "unavailable"
              ? "Marked unavailable"
              : "Skipped for this session",
          isOk: false,
        };
      }
      const shopReq = shoppingRequirements[itemKey];
      return {
        displayName,
        requiredQty: reqNum,
        availableQty: availInReqUnit,
        availableUnit: ing.base_unit,
        unitCompatible: true,
        missingQty: missing,
        isResolved: false,
        statusLabel: shopReq
          ? `Need to buy (${formatQuantityWithUnit(missing, ing.base_unit)})`
          : `${formatQuantityWithUnit(missing, ing.base_unit)} missing`,
        isOk: false,
      };
    },
    [
      acceptedSubstitutions,
      activeLots,
      sessionReportedAvailability,
      shoppingRequirements,
    ],
  );

  const openMissingResolutionModal = useCallback(
    (params: {
      key: string;
      name: string;
      canonical_item_id: string | null;
      requiredQty: number;
      unit: string;
      mode: "have" | "bought";
    }) => {
      setResolveTarget(params);
      setResolveNameText(params.name);
      setResolveQtyText(
        String(params.requiredQty > 0 ? params.requiredQty : 1),
      );
      setResolveUnit(
        (["g", "kg", "ml", "l", "pcs", "dozen"].includes(params.unit)
          ? params.unit
          : "g") as ReportedUnit,
      );
      setResolvePriceText("");
      setResolveMerchantText("");
      setResolveNoteText("");
      setResolvePersistToPantry(false);
      setResolveError(null);
    },
    [],
  );

  const handleConfirmResolveIngredient = useCallback(async () => {
    if (!resolveTarget || isResolvingIngredient) {
      return;
    }
    const cleanName = resolveNameText.trim();
    const qtyNum = Number(resolveQtyText);
    if (!cleanName) {
      setResolveError("Please enter the product or ingredient name.");
      return;
    }
    if (!Number.isFinite(qtyNum) || qtyNum <= 0) {
      setResolveError("Please enter a valid positive quantity.");
      return;
    }
    setResolveError(null);
    setIsResolvingIngredient(true);

    try {
      if (resolveTarget.mode === "have") {
        const targetUnit = (
          ["g", "kg", "ml", "l", "pcs", "dozen"].includes(resolveTarget.unit)
            ? resolveTarget.unit
            : resolveUnit
        ) as BaseUnit;
        const evalRes = evaluateReportedAvailability({
          reportedQty: qtyNum,
          reportedUnit: resolveUnit,
          requiredQty: resolveTarget.requiredQty,
          requiredUnit: targetUnit,
        });
        if (!evalRes.ok) {
          setResolveError(evalRes.error);
          setIsResolvingIngredient(false);
          return;
        }

        const record = buildSessionAvailabilityRecord({
          key: resolveTarget.key,
          name: cleanName,
          canonical_item_id: resolveTarget.canonical_item_id,
          reportedQty: qtyNum,
          reportedUnit: resolveUnit,
          requiredQty: resolveTarget.requiredQty,
          requiredUnit: targetUnit,
          persistedToPantry: resolvePersistToPantry,
          note: resolveNoteText,
        });

        setSessionReportedAvailability((prev) => ({
          ...prev,
          [resolveTarget.key]: record,
        }));

        if (resolvePersistToPantry) {
          const normalized = toCanonicalBaseUnit(qtyNum, resolveUnit);
          // Check if an existing active lot matches canonical_item_id + compatible unit
          const existingLot = resolveTarget.canonical_item_id
            ? activeLots.find(
              (lot) =>
                lot.canonical_item_id === resolveTarget.canonical_item_id &&
                lot.status === "active" &&
                areUnitsCompatible(lot.base_unit, normalized.unit),
            )
            : undefined;

          if (existingLot) {
            const converted =
              convertCompatibleQty(
                normalized.qty,
                normalized.unit,
                existingLot.base_unit,
              ) ?? normalized.qty;
            await apiClient.post(
              `/api/v1/inventory/lots/${existingLot.id}/adjust`,
              {
                event_type: "ADJUSTMENT_IN",
                quantity_delta: converted.toFixed(3),
                notes: resolveNoteText.trim() || `Confirmed on-hand stock for ${cleanName}`,
              },
            );
          } else {
            let targetCanonicalId = resolveTarget.canonical_item_id;
            if (!targetCanonicalId) {
              const catRes = await apiClient.post<{ id: string }>(
                "/api/v1/catalog/items",
                {
                  name: cleanName,
                  category: "grocery",
                  default_base_unit: normalized.unit,
                },
              );
              targetCanonicalId = catRes.id;
            }
            await apiClient.post("/api/v1/inventory/lots", {
              canonical_item_id: targetCanonicalId,
              remaining_qty: normalized.qty.toFixed(3),
              base_unit: normalized.unit,
            });
          }
        }
      } else {
        // "I bought it": record manual purchase + confirm to create eligible pantry lot & purchase accounting
        const priceNum = Number(resolvePriceText || "0");
        const pricePaise = Number.isFinite(priceNum) && priceNum > 0
          ? Math.round(priceNum * 100)
          : 0;
        const todayIso = new Date().toISOString().slice(0, 10);
        const createdPurchase = await apiClient.post<{ id: string }>(
          "/api/v1/purchases",
          {
            merchant_name: resolveMerchantText.trim() || "Grocery Store",
            purchased_on: todayIso,
            receipt_category: "grocery",
            items: [
              {
                canonical_item_id: resolveTarget.canonical_item_id ?? undefined,
                display_name: cleanName,
                category: "grocery",
                qty: toCanonicalBaseUnit(qtyNum, resolveUnit).qty,
                unit: toCanonicalBaseUnit(qtyNum, resolveUnit).unit,
                unit_price_paise: pricePaise,
                line_total_paise: pricePaise,
                track_inventory: true,
              },
            ],
          },
        );
        await apiClient.post(
          `/api/v1/purchases/${createdPurchase.id}/confirm`,
          {},
        );
      }

      // Clear any shopping requirement for this item and refresh inventory + plans
      setShoppingRequirements((prev) => {
        const next = { ...prev };
        delete next[resolveTarget.key];
        return next;
      });
      setResolveTarget(null);
      await invalidateHouseholdData(queryClient, activeHouseholdId);
      await loadData(true);
    } catch (err) {
      const message = err instanceof ApiClientError
        ? err.message
        : "Unable to record ingredient stock. Please try again.";
      setResolveError(message);
    } finally {
      setIsResolvingIngredient(false);
    }
  }, [
    activeHouseholdId,
    activeLots,
    apiClient,
    isResolvingIngredient,
    loadData,
    queryClient,
    resolveMerchantText,
    resolveNameText,
    resolvePriceText,
    resolveQtyText,
    resolveTarget,
    resolveUnit,
  ]);

  const openGuidedForExistingPlan = useCallback(
    async (plan: CookingPlan) => {
      let detail: RecipeDetailTarget | undefined = recipeDetailsByPlanId[plan.id];

      if (!detail && plan.recipe_id) {
        const cachedRecs = queryClient.getQueryData<RecipeRecommendationsResponse>(
          householdQueryKeys.homeRecommendations(activeHouseholdId),
        );
        const cachedMatch = cachedRecs?.items?.find(
          (r) => r.recipe_id === plan.recipe_id,
        );
        if (cachedMatch) {
          detail = recommendationToDetail(cachedMatch);
        }
      }

      if (!detail && plan.recipe_id) {
        try {
          const fetched = await apiClient.get<RecipeRecommendation>(
            `/api/v1/recipes/catalogue/${encodeURIComponent(plan.recipe_id)}`,
          );
          detail = recommendationToDetail(fetched);
          setRecipeDetailsByPlanId((prev) => ({ ...prev, [plan.id]: detail! }));
        } catch {
          // Non-catalogue (e.g. ad-hoc AI Chef) plans fall through to safe cooking-only fallback.
        }
      }

      const resolvedDetail = detail ?? buildFallbackCookingDetail(plan, activeLots);
      setShowFinishPrompt(false);
      setGuidedSession({ plan, detail: resolvedDetail });
    },
    [activeHouseholdId, activeLots, apiClient, queryClient, recipeDetailsByPlanId],
  );

  const openCompleteMealModal = useCallback(
    (plan: CookingPlan) => {
      setCompletingPlan(plan);
      setCompleteLines(
        reconcileCompletePlanLines({
          planId: plan.id,
          planItems: plan.items ?? [],
          activeLots,
          sessionReportedAvailability,
        }),
      );
      setCompleteError(null);
    },
    [activeLots, sessionReportedAvailability],
  );

  useEffect(() => {
    if (!completingPlan) {
      return;
    }
    setCompleteLines((prev) =>
      reconcileCompletePlanLines({
        planId: completingPlan.id,
        planItems: completingPlan.items ?? [],
        activeLots,
        sessionReportedAvailability,
        existingLines: prev,
      }),
    );
  }, [activeLots, completingPlan, sessionReportedAvailability]);

  const handleCompleteMeal = useCallback(async () => {
    if (!completingPlan || isCompleting) {
      return;
    }
    setCompleteError(null);

    const combinedTotals = computeCombinedDeductionByCanonical(completeLines);
    const itemsPayload: CompleteCookingPlanItemInput[] = [];
    for (const line of completeLines) {
      if (line.sessionConfirmed) {
        itemsPayload.push({
          line_id: line.lineId,
          canonical_item_id: line.canonical_item_id,
          actual_used_qty: "0.000",
          omitted: true,
          session_confirmed: true,
          confirmed_qty:
            line.sessionConfirmedQty !== undefined
              ? line.sessionConfirmedQty.toFixed(3)
              : undefined,
          confirmed_unit: line.sessionConfirmedUnit ?? line.base_unit,
        });
        continue;
      }
      if (!line.omitted && !line.unitCompatible) {
        setCompleteError(
          `Unit mismatch for ${line.productName} (pantry in ${line.lotBaseUnit}, recipe in ${line.base_unit}). Omit this line or add stock in ${line.base_unit}.`,
        );
        return;
      }
      const usedNum = line.omitted ? 0 : Number(line.actualUsedText || "0");
      const wasteNum = line.omitted ? 0 : Number(line.wasteQtyText || "0");
      if (!Number.isFinite(usedNum) || usedNum < 0) {
        setCompleteError(`Invalid actual quantity for ${line.productName}.`);
        return;
      }
      if (!Number.isFinite(wasteNum) || wasteNum < 0) {
        setCompleteError(`Invalid waste quantity for ${line.productName}.`);
        return;
      }
      const combinedForProduct = combinedTotals[line.canonical_item_id] ?? 0;
      if (!line.omitted && combinedForProduct > line.availableQty + 1e-6) {
        const missing = Math.max(0, combinedForProduct - line.availableQty);
        setCompleteError(
          `${line.productName} is short by ${
            formatQuantityWithUnit(missing, line.base_unit)
          } (need ${
            formatQuantityWithUnit(combinedForProduct, line.base_unit)
          }, available ${
            formatQuantityWithUnit(line.availableQty, line.base_unit)
          }). Reduce quantity, omit, or add stock.`,
        );
        return;
      }
      itemsPayload.push({
        line_id: line.lineId,
        canonical_item_id: line.canonical_item_id,
        actual_used_qty: usedNum.toFixed(3),
        waste_qty: wasteNum > 0 ? wasteNum.toFixed(3) : undefined,
        omitted: line.omitted,
      });
    }

    setIsCompleting(true);
    try {
      const completedTitle = completingPlan.title ?? "this meal";
      const completeRes = await apiClient.post<CompleteCookingPlanResponse>(
        `/api/v1/cooking-plans/${completingPlan.id}/complete`,
        { items: itemsPayload },
        {
          headers: {
            "Idempotency-Key": createIdempotencyKey("complete-meal"),
          },
        },
      );
      setCompletingPlan(null);
      if (completeRes.meal_history_id) {
        setFeedbackModalMealId(completeRes.meal_history_id);
        setFeedbackModalTitle(completedTitle);
        setFeedbackRating(null);
        setFeedbackTags([]);
        setFeedbackComment("");
        setFeedbackWouldMakeAgain(null);
      }
      // Refresh cached pantry & history in background after transaction has committed
      void invalidateHouseholdData(queryClient, activeHouseholdId);
      void loadData(true);
    } catch (err) {
      const message = err instanceof ApiClientError
        ? err.message
        : "Failed to complete cooking plan.";
      setCompleteError(message);
    } finally {
      setIsCompleting(false);
    }
  }, [apiClient, completeLines, completingPlan, isCompleting, loadData]);

  const handleOpenFeedbackForMeal = useCallback((meal: MealHistoryItem) => {
    setFeedbackModalMealId(meal.id);
    setFeedbackModalTitle(meal.recipe_title);
    setFeedbackRating(meal.rating ?? null);
    setFeedbackTags(meal.feedback_tags ?? []);
    setFeedbackComment(meal.feedback_comment ?? "");
    setFeedbackWouldMakeAgain(meal.would_make_again ?? null);
  }, []);

  const handleSaveFeedback = useCallback(async () => {
    if (!feedbackModalMealId || isSavingFeedback) {
      return;
    }
    setIsSavingFeedback(true);
    try {
      const payload: MealFeedbackRequest = {
        rating: feedbackRating,
        feedback_tags: feedbackTags,
        feedback_comment: feedbackComment.trim() || null,
        would_make_again: feedbackWouldMakeAgain,
      };
      const updated = await apiClient.request<MealHistoryItem>(
        `/api/v1/meals/history/${feedbackModalMealId}/feedback`,
        { method: "PATCH", body: payload },
      );
      setFeedbackModalMealId(null);
      if (selectedMealDetail?.id === updated.id) {
        setSelectedMealDetail(updated);
      }
      await invalidateHouseholdData(queryClient, activeHouseholdId);
      await loadData(true);
    } catch (err) {
      setActionError(
        err instanceof ApiClientError
          ? err.message
          : "Unable to save meal feedback.",
      );
    } finally {
      setIsSavingFeedback(false);
    }
  }, [
    apiClient,
    feedbackComment,
    feedbackModalMealId,
    feedbackRating,
    feedbackTags,
    feedbackWouldMakeAgain,
    isSavingFeedback,
    loadData,
    selectedMealDetail,
  ]);

  const handleCancelPlan = useCallback(
    async (planId: string) => {
      if (isCancellingPlanId) {
        return;
      }
      setIsCancellingPlanId(planId);
      setActionError(null);
      try {
        await apiClient.post(
          `/api/v1/cooking-plans/${planId}/cancel`,
          {},
        );
        await loadData(true);
      } catch (err) {
        const message = err instanceof ApiClientError
          ? err.message
          : "Unable to cancel cooking plan.";
        setActionError(message);
      } finally {
        setIsCancellingPlanId(null);
      }
    },
    [apiClient, isCancellingPlanId, loadData],
  );

  if (isLoading) {
    return (
      <ScreenContainer>
        <LoadingState message="Loading recipes and cooking plans..." />
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

  const isProviderOffline = latestProviderStatus !== null &&
    latestProviderStatus !== "live";

  return (
    <ScreenContainer>
      <ScrollView
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={isRefreshing}
            onRefresh={() => void loadData(true)}
            tintColor={colors.primary}
          />
        }
        contentContainerStyle={styles.scrollContent}
      >
        <View style={styles.header}>
          <Text style={styles.title}>Recipes & AI Chef</Text>
          <Text style={styles.subtitle}>
            Grounded in your active pantry stock — zero fake ingredients
          </Text>
        </View>

        {latestProviderStatus !== null
          ? (
            <View
              style={[
                styles.providerBanner,
                isProviderOffline
                  ? styles.providerBannerOffline
                  : styles.providerBannerLive,
              ]}
            >
              <Ionicons
                name={isProviderOffline
                  ? "information-circle-outline"
                  : "sparkles-outline"}
                size={18}
                color={isProviderOffline ? colors.warning : colors.primary}
              />
              <Text
                style={[
                  styles.providerBannerText,
                  isProviderOffline
                    ? styles.providerTextOffline
                    : styles.providerTextLive,
                ]}
              >
                {isProviderOffline
                  ? latestProviderMessage ??
                    `Provider status: ${latestProviderStatus} (HF_TOKEN missing — showing pantry-grounded fallback)`
                  : "AI Chef Live (pantry-grounded)"}
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

        {/* Prominent Ask AI Chef Action */}
        <Pressable
          onPress={() => setAiChefVisible(true)}
          style={styles.sendChatButton}
          accessibilityRole="button"
          accessibilityLabel="Ask AI Chef"
        >
          <Ionicons
            name="sparkles-outline"
            size={20}
            color={colors.textOnPrimary}
          />
          <Text style={styles.sendChatButtonText}>Ask AI Chef</Text>
        </Pressable>

        {/* Active Cooking Plans */}
        {activePlans.length > 0
          ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>
                Active Cooking Plans ({activePlans.length})
              </Text>
              {activePlans.map((plan) => (
                <View key={plan.id} style={styles.activePlanCard}>
                  <View style={styles.planTopRow}>
                    <View style={styles.flex1}>
                      <Text style={styles.planTitle}>
                        {plan.title ?? "Planned Meal"}
                      </Text>
                      <Text style={styles.planMeta}>
                        {plan.planned_servings}{" "}
                        servings · Stock not deducted yet
                      </Text>
                    </View>
                    <View style={styles.plannedBadge}>
                      <Text style={styles.plannedBadgeText}>Planned</Text>
                    </View>
                  </View>

                  <View style={styles.planActionsRow}>
                    <Pressable
                      onPress={() =>
                        openGuidedForExistingPlan(plan)}
                      style={styles.completeButton}
                      accessibilityRole="button"
                      accessibilityLabel={`Resume guided cooking for ${
                        plan.title ?? ""
                      }`}
                    >
                      <Text style={styles.completeButtonText}>
                        {(guidedProgressByPlanId[plan.id]?.stepIndex ?? 0) > 0
                          ? `Resume Step ${
                            (guidedProgressByPlanId[plan.id]?.stepIndex ?? 0) +
                            1
                          }`
                          : "Guided Cooking"}
                      </Text>
                    </Pressable>
                    <Pressable
                      onPress={() => void handleCancelPlan(plan.id)}
                      disabled={isCancellingPlanId === plan.id}
                      style={styles.cancelButton}
                      accessibilityRole="button"
                      accessibilityLabel={`Cancel cooking plan ${
                        plan.title ?? ""
                      }`}
                    >
                      {isCancellingPlanId === plan.id
                        ? <ActivityIndicator color={colors.error} />
                        : <Text style={styles.cancelButtonText}>Cancel</Text>}
                    </Pressable>
                  </View>
                </View>
              ))}
            </View>
          )
          : null}

        {/* Meal History Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            Meal History ({mealHistory.length})
          </Text>
          <View style={styles.constraintsRow}>
            {(["all", "7d", "30d"] as const).map((df) => (
              <Pressable
                key={df}
                onPress={() => setHistoryDateFilter(df)}
                style={[
                  styles.constraintChip,
                  historyDateFilter === df && styles.constraintChipSelected,
                ]}
              >
                <Text
                  style={[
                    styles.constraintChipText,
                    historyDateFilter === df &&
                    styles.constraintChipTextSelected,
                  ]}
                >
                  {df === "all"
                    ? "All Dates"
                    : df === "7d"
                    ? "Last 7d"
                    : "Last 30d"}
                </Text>
              </Pressable>
            ))}
            {(["all", "breakfast", "lunch", "dinner"] as const).map((mt) => (
              <Pressable
                key={mt}
                onPress={() => setHistoryMealTypeFilter(mt)}
                style={[
                  styles.constraintChip,
                  historyMealTypeFilter === mt && styles.constraintChipSelected,
                ]}
              >
                <Text
                  style={[
                    styles.constraintChipText,
                    historyMealTypeFilter === mt &&
                    styles.constraintChipTextSelected,
                  ]}
                >
                  {mt === "all"
                    ? "All Meals"
                    : mt.charAt(0).toUpperCase() + mt.slice(1)}
                </Text>
              </Pressable>
            ))}
            {(["all", "liked", "disliked"] as const).map((rf) => (
              <Pressable
                key={rf}
                onPress={() => setHistoryRatingFilter(rf)}
                style={[
                  styles.constraintChip,
                  historyRatingFilter === rf && styles.constraintChipSelected,
                ]}
              >
                <Text
                  style={[
                    styles.constraintChipText,
                    historyRatingFilter === rf &&
                    styles.constraintChipTextSelected,
                  ]}
                >
                  {rf === "all"
                    ? "All Ratings"
                    : rf === "liked"
                    ? "4★+ Liked"
                    : "1-2★ Disliked"}
                </Text>
              </Pressable>
            ))}
          </View>

          {mealHistory.length === 0
            ? (
              <EmptyState
                icon="calendar-outline"
                title="No completed meals yet"
                body="Completed cooking sessions will appear here grouped by date. Suggestions and cancelled plans are never added."
              />
            )
            : (
              groupMealHistoryByDate(mealHistory).map((group) => (
                <View key={group.dateKey} style={{ gap: spacing.xs }}>
                  <Text style={styles.ingAvailableHeader}>
                    {group.dateLabel}
                  </Text>
                  {group.meals.map((meal) => {
                    const isFav = favouriteMeals.includes(meal.recipe_title);
                    const isDisliked = dislikedMeals.includes(
                      meal.recipe_title,
                    );
                    const isFreq = frequentlyCooked.includes(meal.recipe_title);
                    const timeStr = new Date(meal.cooked_at).toLocaleTimeString(
                      [],
                      {
                        hour: "2-digit",
                        minute: "2-digit",
                      },
                    );
                    return (
                      <Pressable
                        key={meal.id}
                        onPress={() => setSelectedMealDetail(meal)}
                        style={styles.recCard}
                        accessibilityRole="button"
                      >
                        <View style={styles.recTopRow}>
                          <View style={styles.flex1}>
                            <Text style={styles.recTitle}>
                              {meal.meal_type
                                ? `${
                                  meal.meal_type.charAt(0).toUpperCase() +
                                  meal.meal_type.slice(1)
                                } — `
                                : ""}
                              {meal.recipe_title}
                            </Text>
                            <Text style={styles.recMeta}>
                              {timeStr} · {meal.servings} servings
                              {meal.cook_count > 1
                                ? ` · Cooked ${meal.cook_count}×`
                                : ""}
                            </Text>
                          </View>
                          {isFav
                            ? (
                              <View style={styles.expiringTag}>
                                <Text style={styles.expiringTagText}>
                                  Favourite
                                </Text>
                              </View>
                            )
                            : isDisliked
                            ? (
                              <View style={styles.expiringTag}>
                                <Text style={styles.expiringTagText}>
                                  Disliked
                                </Text>
                              </View>
                            )
                            : isFreq
                            ? (
                              <View style={styles.expiringTag}>
                                <Text style={styles.expiringTagText}>
                                  Frequent
                                </Text>
                              </View>
                            )
                            : null}
                        </View>
                        <Text style={styles.planMeta}>
                          {meal.rating != null
                            ? `Rating: ${meal.rating}/5${
                              meal.feedback_tags.length
                                ? ` · ${meal.feedback_tags.join(", ")}`
                                : ""
                            }`
                            : "No feedback yet — tap to rate or view details"}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              ))
            )}
          {mealHistory.length < historyTotal
            ? (
              <Pressable
                onPress={() => setHistoryLimit((prev) => prev + 20)}
                style={styles.omitChip}
                accessibilityRole="button"
              >
                <Text style={styles.omitChipText}>Load More Meals</Text>
              </Pressable>
            )
            : null}
        </View>
      </ScrollView>

      {/* Complete Meal Modal */}
      <Modal
        visible={completingPlan !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setCompletingPlan(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Complete Meal</Text>
            <Pressable
              onPress={() => setCompletingPlan(null)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Close complete meal modal"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>

          <ScrollView
            contentContainerStyle={styles.modalContent}
            keyboardShouldPersistTaps="handled"
          >
            <Text style={styles.modalSubtitle}>
              Confirm actual quantities used for{" "}
              {completingPlan?.title ?? "this meal"}. Stock is deducted in FEFO
              order only when you confirm.
            </Text>

            {completeError
              ? (
                <View style={styles.errorBanner} accessibilityRole="alert">
                  <Ionicons
                    name="alert-circle-outline"
                    size={18}
                    color={colors.error}
                  />
                  <Text style={styles.errorBannerText}>{completeError}</Text>
                </View>
              )
              : null}

            {(() => {
              const combinedByCanonical = computeCombinedDeductionByCanonical(
                completeLines,
              );
              const lineCountByCanonical: Record<string, number> = {};
              for (const l of completeLines) {
                lineCountByCanonical[l.canonical_item_id] =
                  (lineCountByCanonical[l.canonical_item_id] ?? 0) + 1;
              }
              return completeLines.map((line) => {
                const usedNum = line.omitted
                  ? 0
                  : Math.max(0, Number(line.actualUsedText || "0") || 0);
                const wasteNum = line.omitted
                  ? 0
                  : Math.max(0, Number(line.wasteQtyText || "0") || 0);
                const willDeduct = usedNum + wasteNum;
                const combinedDeduct =
                  combinedByCanonical[line.canonical_item_id] ?? 0;
                const expectedRemaining = line.unitCompatible
                  ? Math.max(0, line.availableQty - combinedDeduct)
                  : line.availableQty;
                const hasSiblingLines =
                  (lineCountByCanonical[line.canonical_item_id] ?? 0) > 1;
                const exceedsStock = !line.omitted &&
                  line.unitCompatible &&
                  combinedDeduct > line.availableQty + 1e-6;
                const missingAmt = exceedsStock
                  ? Math.max(0, combinedDeduct - line.availableQty)
                  : 0;

                return (
                  <View key={line.lineId} style={styles.completeLineCard}>
                    <View style={styles.completeLineHeader}>
                      <View style={styles.flex1}>
                        <Text style={styles.completeLineName}>
                          {line.productName}
                        </Text>
                        {line.name !== line.productName
                          ? (
                            <Text style={styles.plannedHint}>
                              Recipe ingredient: {line.name}
                            </Text>
                          )
                          : null}
                      </View>
                      <Pressable
                        onPress={() =>
                          setCompleteLines((prev) =>
                            prev.map((item) =>
                              item.lineId === line.lineId
                                ? { ...item, omitted: !item.omitted }
                                : item
                            )
                          )}
                        style={[
                          styles.omitChip,
                          line.omitted && styles.omitChipSelected,
                        ]}
                        accessibilityRole="button"
                        accessibilityState={{ selected: line.omitted }}
                      >
                        <Text
                          style={[
                            styles.omitChipText,
                            line.omitted && styles.omitChipTextSelected,
                          ]}
                        >
                          {line.omitted ? "Omitted (0)" : "Omit"}
                        </Text>
                      </Pressable>
                    </View>

                    <Text style={styles.plannedHint}>
                      Available before cooking: {formatQuantityWithUnit(
                        line.availableQty,
                        line.lotBaseUnit,
                      )}
                      {line.lotCount > 1
                        ? ` (FEFO across ${line.lotCount} lots)`
                        : ""}
                    </Text>
                    <Text style={styles.plannedHint}>
                      Planned:{" "}
                      {formatQuantityWithUnit(line.planned_qty, line.base_unit)}
                    </Text>

                    {!line.omitted
                      ? (
                        <>
                          {line.sessionConfirmed
                            ? (
                              <View style={{ gap: 6, marginTop: 4 }}>
                                <Text style={styles.ingAvailableText}>
                                  Confirmed on-hand:{" "}
                                  {formatQuantityWithUnit(
                                    line.sessionConfirmedQty ?? line.planned_qty,
                                    line.sessionConfirmedUnit ?? line.base_unit,
                                  )}{" "}
                                  (session-only — 0 deducted from pantry)
                                </Text>
                                {!line.unitCompatible
                                  ? (
                                    <Text style={styles.plannedHint}>
                                      Existing pantry lot ({formatQuantityWithUnit(
                                        line.availableQty,
                                        line.lotBaseUnit,
                                      )}) is kept untouched.
                                    </Text>
                                  )
                                  : null}
                              </View>
                            )
                            : !line.unitCompatible
                            ? (
                              <View style={{ gap: 6, marginTop: 4 }}>
                                <Text style={styles.ingMissingText}>
                                  Unit mismatch: Pantry has{" "}
                                  {formatQuantityWithUnit(
                                    line.availableQty,
                                    line.lotBaseUnit,
                                  )}
                                  , recipe asks for {formatQuantityWithUnit(
                                    line.planned_qty,
                                    line.base_unit,
                                  )}
                                  . Units are not interchangeable — omit this
                                  line or add stock in {line.base_unit}.
                                </Text>
                                <View style={styles.constraintsRow}>
                                  <Pressable
                                    onPress={() =>
                                      openMissingResolutionModal({
                                        key: line.lineId,
                                        name: line.productName,
                                        canonical_item_id:
                                          line.canonical_item_id,
                                        requiredQty: Number(line.planned_qty) ||
                                          1,
                                        unit: line.base_unit,
                                        mode: "have",
                                      })}
                                    style={styles.constraintChip}
                                  >
                                    <Text style={styles.constraintChipText}>
                                      I have it ({line.base_unit})
                                    </Text>
                                  </Pressable>
                                </View>
                              </View>
                            )
                            : (
                              <>
                                <View style={styles.rowFields}>
                                  <View style={styles.flex1}>
                                    <Text style={styles.fieldLabel}>
                                      Actual used ({line.base_unit})
                                    </Text>
                                    <TextInput
                                      value={line.actualUsedText}
                                      onChangeText={(text) =>
                                        setCompleteLines((prev) =>
                                          prev.map((item) =>
                                            item.lineId === line.lineId
                                              ? {
                                                ...item,
                                                actualUsedText: text,
                                              }
                                              : item
                                          )
                                        )}
                                      keyboardType="decimal-pad"
                                      style={styles.input}
                                    />
                                  </View>
                                  <View style={styles.flex1}>
                                    <Text style={styles.fieldLabel}>
                                      Waste ({line.base_unit})
                                    </Text>
                                    <TextInput
                                      value={line.wasteQtyText}
                                      onChangeText={(text) =>
                                        setCompleteLines((prev) =>
                                          prev.map((item) =>
                                            item.lineId === line.lineId
                                              ? { ...item, wasteQtyText: text }
                                              : item
                                          )
                                        )}
                                      keyboardType="decimal-pad"
                                      style={styles.input}
                                    />
                                  </View>
                                </View>
                                <Text style={styles.ingAvailableHeader}>
                                  Will deduct: {formatQuantityWithUnit(
                                    willDeduct,
                                    line.base_unit,
                                  )}
                                </Text>
                                {hasSiblingLines
                                  ? (
                                    <Text style={styles.plannedHint}>
                                      Combined deduction (all lines):{" "}
                                      {formatQuantityWithUnit(
                                        combinedDeduct,
                                        line.base_unit,
                                      )}
                                    </Text>
                                  )
                                  : null}
                                <Text style={styles.plannedHint}>
                                  Expected remaining: {formatQuantityWithUnit(
                                    expectedRemaining,
                                    line.base_unit,
                                  )}
                                </Text>
                                {exceedsStock
                                  ? (
                                    <View style={{ gap: 6, marginTop: 4 }}>
                                      <Text style={styles.ingMissingText}>
                                        Missing {formatQuantityWithUnit(
                                          missingAmt,
                                          line.base_unit,
                                        )} (available: {formatQuantityWithUnit(
                                          line.availableQty,
                                          line.base_unit,
                                        )}
                                        ). Reduce quantity, omit, or add stock.
                                      </Text>
                                      <View style={styles.constraintsRow}>
                                        <Pressable
                                          onPress={() =>
                                            openMissingResolutionModal({
                                              key: line.lineId,
                                              name: line.productName,
                                              canonical_item_id:
                                                line.canonical_item_id,
                                              requiredQty: missingAmt,
                                              unit: line.base_unit,
                                              mode: "have",
                                            })}
                                          style={styles.constraintChip}
                                        >
                                          <Text
                                            style={styles.constraintChipText}
                                          >
                                            I have it
                                          </Text>
                                        </Pressable>
                                        <Pressable
                                          onPress={() =>
                                            openMissingResolutionModal({
                                              key: line.lineId,
                                              name: line.productName,
                                              canonical_item_id:
                                                line.canonical_item_id,
                                              requiredQty: missingAmt,
                                              unit: line.base_unit,
                                              mode: "bought",
                                            })}
                                          style={styles.constraintChip}
                                        >
                                          <Text
                                            style={styles.constraintChipText}
                                          >
                                            I bought it
                                          </Text>
                                        </Pressable>
                                      </View>
                                    </View>
                                  )
                                  : null}
                              </>
                            )}
                        </>
                      )
                      : (
                        <Text style={styles.plannedHint}>
                          Will not be deducted (omitted).
                          {hasSiblingLines && line.unitCompatible
                            ? ` Expected remaining after other lines: ${
                              formatQuantityWithUnit(
                                expectedRemaining,
                                line.base_unit,
                              )
                            }`
                            : ""}
                        </Text>
                      )}
                  </View>
                );
              });
            })()}

            {/* Final Deduction Summary */}
            {completeLines.length > 0
              ? (() => {
                const summaryCombined = computeCombinedDeductionByCanonical(
                  completeLines,
                );
                const toDeduct = completeLines.filter((l) => {
                  if (l.omitted || l.sessionConfirmed || !l.unitCompatible) return false;
                  const d = (Number(l.actualUsedText || "0") || 0) +
                    (Number(l.wasteQtyText || "0") || 0);
                  const comb = summaryCombined[l.canonical_item_id] ?? 0;
                  return d > 0 && comb <= l.availableQty + 1e-6;
                });
                const sessionConfirmedList = completeLines.filter(
                  (l) => !l.omitted && Boolean(l.sessionConfirmed),
                );
                const omittedList = completeLines.filter((l) => l.omitted);
                const unresolvedList = completeLines.filter((l) =>
                  isCompletePlanLineUnresolved(l, summaryCombined)
                );

                return (
                  <View style={styles.completeLineCard}>
                    <Text style={styles.completeLineName}>
                      Products to deduct ({toDeduct.length})
                    </Text>
                    {toDeduct.length === 0
                      ? (
                        <Text style={styles.plannedHint}>
                          No valid deductions selected yet.
                        </Text>
                      )
                      : (
                        toDeduct.map((line) => {
                          const used = Math.max(
                            0,
                            Number(line.actualUsedText || "0") || 0,
                          );
                          const waste = Math.max(
                            0,
                            Number(line.wasteQtyText || "0") || 0,
                          );
                          const deduct = used + waste;
                          const rem = Math.max(
                            0,
                            line.availableQty -
                              (summaryCombined[line.canonical_item_id] ?? 0),
                          );
                          return (
                            <Text
                              key={`summary-deduct-${line.lineId}`}
                              style={styles.plannedHint}
                            >
                              • {line.productName}: Used{" "}
                              {formatQuantityWithUnit(used, line.base_unit)},
                              Waste{" "}
                              {formatQuantityWithUnit(waste, line.base_unit)},
                              Total deducted{" "}
                              {formatQuantityWithUnit(deduct, line.base_unit)} →
                              {" "}
                              {formatQuantityWithUnit(rem, line.base_unit)}{" "}
                              remaining
                            </Text>
                          );
                        })
                      )}

                    {sessionConfirmedList.length > 0
                      ? (
                        <View style={{ marginTop: spacing.xs }}>
                          <Text style={styles.ingAvailableHeader}>
                            Confirmed on-hand (session-only) ({sessionConfirmedList.length})
                          </Text>
                          {sessionConfirmedList.map((line) => (
                            <Text
                              key={`summary-session-${line.lineId}`}
                              style={styles.plannedHint}
                            >
                              • {line.productName}: Confirmed{" "}
                              {formatQuantityWithUnit(
                                line.sessionConfirmedQty ?? line.planned_qty,
                                line.sessionConfirmedUnit ?? line.base_unit,
                              )}{" "}
                              (0 deducted from pantry)
                            </Text>
                          ))}
                        </View>
                      )
                      : null}

                    {omittedList.length > 0
                      ? (
                        <View style={{ marginTop: spacing.xs }}>
                          <Text style={styles.ingAvailableHeader}>
                            Omitted ingredients ({omittedList.length})
                          </Text>
                          {omittedList.map((line) => (
                            <Text
                              key={`summary-omit-${line.lineId}`}
                              style={styles.plannedHint}
                            >
                              • {line.productName}: Omitted (will not be deducted from pantry)
                            </Text>
                          ))}
                        </View>
                      )
                      : null}

                    {unresolvedList.length > 0
                      ? (
                        <View style={{ marginTop: spacing.xs }}>
                          <Text style={styles.ingMissingHeader}>
                            Unresolved ingredients ({unresolvedList.length})
                          </Text>
                          {unresolvedList.map((line) => {
                            const comb =
                              summaryCombined[line.canonical_item_id] ?? 0;
                            const shortBy = Math.max(
                              0,
                              comb - line.availableQty,
                            );
                            return (
                              <Text
                                key={`summary-unres-${line.lineId}`}
                                style={styles.ingMissingText}
                              >
                                • {line.productName}: {!line.unitCompatible
                                  ? `Unit mismatch (pantry ${line.lotBaseUnit} vs recipe ${line.base_unit}) — omit or add stock in ${line.base_unit}`
                                  : `Missing ${
                                    formatQuantityWithUnit(
                                      shortBy,
                                      line.base_unit,
                                    )
                                  } — reduce quantity, omit, or add stock`}
                              </Text>
                            );
                          })}
                        </View>
                      )
                      : null}
                  </View>
                );
              })()
              : null}
          </ScrollView>

          <View style={styles.modalFooter}>
            {(() => {
              const footerCombined = computeCombinedDeductionByCanonical(
                completeLines,
              );
              const unresolvedCount = completeLines.filter((l) =>
                isCompletePlanLineUnresolved(l, footerCombined)
              ).length;
              const blocked = isCompleting || unresolvedCount > 0;
              return (
                <View style={{ gap: 6 }}>
                  {unresolvedCount > 0
                    ? (
                      <Text style={styles.ingMissingText}>
                        Resolve or omit {unresolvedCount} unresolved ingredient
                        {unresolvedCount > 1 ? "s" : ""}{" "}
                        above to enable deduction.
                      </Text>
                    )
                    : null}
                  <Pressable
                    onPress={() => void handleCompleteMeal()}
                    disabled={blocked}
                    style={[
                      styles.confirmCompleteBtn,
                      blocked && styles.buttonDisabled,
                    ]}
                    accessibilityRole="button"
                    accessibilityLabel="Confirm cooking and deduct these ingredients"
                  >
                    {isCompleting
                      ? <ActivityIndicator color={colors.textOnPrimary} />
                      : (
                        <Text style={styles.confirmCompleteBtnText}>
                          Confirm cooking and deduct these ingredients
                        </Text>
                      )}
                  </Pressable>
                </View>
              );
            })()}
          </View>
        </View>
      </Modal>

      {/* Meal Detail Modal */}
      <Modal
        visible={selectedMealDetail !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setSelectedMealDetail(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>
              {selectedMealDetail?.recipe_title ?? "Meal Details"}
            </Text>
            <Pressable
              onPress={() => setSelectedMealDetail(null)}
              style={styles.closeButton}
              accessibilityRole="button"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.modalContent}>
            {selectedMealDetail
              ? (
                <View>
                  <Text style={styles.modalSubtitle}>
                    Cooked on{" "}
                    {new Date(selectedMealDetail.cooked_at).toLocaleString()} ·
                    {" "}
                    {selectedMealDetail.servings} servings
                    {selectedMealDetail.meal_type
                      ? ` · ${selectedMealDetail.meal_type}`
                      : ""}
                  </Text>
                  <Text style={styles.ingAvailableHeader}>
                    Ingredients Used:
                  </Text>
                  {selectedMealDetail.ingredients_used.map((ing, i) => (
                    <Text key={i} style={styles.planMeta}>
                      • {ing.name || ing.canonical_item_id}:{" "}
                      {ing.actual_qty ?? ing.planned_qty ?? "—"}{" "}
                      {ing.base_unit ?? ""}
                    </Text>
                  ))}
                  {selectedMealDetail.substitutions.length > 0
                    ? (
                      <View style={{ marginTop: spacing.sm }}>
                        <Text style={styles.ingAvailableHeader}>
                          Substitutions:
                        </Text>
                        {selectedMealDetail.substitutions.map((s, i) => (
                          <Text key={i} style={styles.planMeta}>• {s}</Text>
                        ))}
                      </View>
                    )
                    : null}
                  {selectedMealDetail.recipe_snapshot?.steps &&
                      selectedMealDetail.recipe_snapshot.steps.length > 0
                    ? (
                      <View style={{ marginTop: spacing.sm }}>
                        <Text style={styles.ingAvailableHeader}>
                          Recipe Steps:
                        </Text>
                        {selectedMealDetail.recipe_snapshot.steps.map((
                          st,
                          i,
                        ) => (
                          <Text key={i} style={styles.planMeta}>
                            {i + 1}. {st}
                          </Text>
                        ))}
                      </View>
                    )
                    : null}
                  <View style={{ marginTop: spacing.md }}>
                    <Text style={styles.ingAvailableHeader}>
                      Your Feedback:
                    </Text>
                    <Text style={styles.planMeta}>
                      {selectedMealDetail.rating != null
                        ? `Rating: ${selectedMealDetail.rating}/5`
                        : "Not rated yet"}
                    </Text>
                    {selectedMealDetail.feedback_tags.length > 0
                      ? (
                        <Text style={styles.planMeta}>
                          Tags: {selectedMealDetail.feedback_tags.join(", ")}
                        </Text>
                      )
                      : null}
                    {selectedMealDetail.feedback_comment
                      ? (
                        <Text style={styles.planMeta}>
                          Comment: {selectedMealDetail.feedback_comment}
                        </Text>
                      )
                      : null}
                    <Pressable
                      onPress={() =>
                        handleOpenFeedbackForMeal(selectedMealDetail)}
                      style={[styles.startCookingBtn, {
                        marginTop: spacing.sm,
                      }]}
                      accessibilityRole="button"
                    >
                      <Text style={styles.startCookingBtnText}>
                        {selectedMealDetail.rating != null
                          ? "Edit Feedback"
                          : "Add Feedback"}
                      </Text>
                    </Pressable>
                  </View>
                </View>
              )
              : null}
          </ScrollView>
        </View>
      </Modal>

      {/* Meal Feedback Modal */}
      <Modal
        visible={feedbackModalMealId !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setFeedbackModalMealId(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>How did you like this meal?</Text>
            <Pressable
              onPress={() => setFeedbackModalMealId(null)}
              style={styles.closeButton}
              accessibilityRole="button"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>
          <ScrollView
            contentContainerStyle={styles.modalContent}
            keyboardShouldPersistTaps="handled"
          >
            <Text style={styles.modalSubtitle}>{feedbackModalTitle}</Text>
            {[
              { value: 1, label: "1. Didn't like it." },
              { value: 2, label: "2. Not really my taste." },
              { value: 3, label: "3. It was okay." },
              { value: 4, label: "4. Liked it." },
              { value: 5, label: "5. Loved it." },
            ].map((opt) => (
              <Pressable
                key={opt.value}
                onPress={() => setFeedbackRating(opt.value)}
                style={[
                  styles.constraintChip,
                  { marginBottom: spacing.xs },
                  feedbackRating === opt.value && styles.constraintChipSelected,
                ]}
                accessibilityRole="button"
              >
                <Text
                  style={[
                    styles.constraintChipText,
                    feedbackRating === opt.value &&
                    styles.constraintChipTextSelected,
                  ]}
                >
                  {opt.label}
                </Text>
              </Pressable>
            ))}

            <Text style={[styles.fieldLabel, { marginTop: spacing.sm }]}>
              Quick Comments (Optional)
            </Text>
            <View style={styles.constraintsRow}>
              {[
                "Too spicy",
                "Not spicy enough",
                "Too oily",
                "Too time-consuming",
                "Texture wasn't good",
                "Loved the taste",
                "Would like to make it again",
              ].map((tag) => {
                const selected = feedbackTags.includes(tag);
                return (
                  <Pressable
                    key={tag}
                    onPress={() => {
                      setFeedbackTags((prev) =>
                        prev.includes(tag)
                          ? prev.filter((t) => t !== tag)
                          : [...prev, tag]
                      );
                      if (tag === "Would like to make it again") {
                        setFeedbackWouldMakeAgain(!selected);
                      }
                    }}
                    style={[
                      styles.constraintChip,
                      selected && styles.constraintChipSelected,
                    ]}
                  >
                    <Text
                      style={[
                        styles.constraintChipText,
                        selected && styles.constraintChipTextSelected,
                      ]}
                    >
                      {tag}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            <Text style={styles.fieldLabel}>Notes (Optional)</Text>
            <TextInput
              value={feedbackComment}
              onChangeText={setFeedbackComment}
              placeholder="Add any notes about taste, spice, or substitutions..."
              placeholderTextColor={colors.textMuted}
              multiline
              style={[styles.input, { minHeight: 72 }]}
            />
          </ScrollView>
          <View style={styles.modalFooter}>
            <Pressable
              onPress={() => void handleSaveFeedback()}
              disabled={isSavingFeedback}
              style={[
                styles.confirmCompleteBtn,
                isSavingFeedback && styles.buttonDisabled,
              ]}
              accessibilityRole="button"
            >
              <Text style={styles.confirmCompleteBtnText}>
                {isSavingFeedback ? "Saving..." : "Save Feedback"}
              </Text>
            </Pressable>
            <Pressable
              onPress={() => setFeedbackModalMealId(null)}
              style={[styles.omitChip, {
                marginTop: spacing.xs,
                alignSelf: "center",
              }]}
              accessibilityRole="button"
            >
              <Text style={styles.omitChipText}>Skip for Now</Text>
            </Pressable>
          </View>
        </View>
      </Modal>

      {/* Recipe Detail Modal */}
      <Modal
        visible={selectedRecipeDetail !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setSelectedRecipeDetail(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle} numberOfLines={2}>
              {selectedRecipeDetail?.title ?? "Recipe Details"}
            </Text>
            <Pressable
              onPress={() => setSelectedRecipeDetail(null)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Close recipe detail"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.modalContent}>
            {selectedRecipeDetail
              ? (
                <View>
                  <View style={styles.expiringTag}>
                    <Text style={styles.expiringTagText}>
                      {selectedRecipeDetail.source_label}
                      {selectedRecipeDetail.recommendation_type
                        ? ` · ${
                          selectedRecipeDetail.recommendation_type ===
                              "COOK_NOW"
                            ? "Cook Now"
                            : selectedRecipeDetail.recommendation_type ===
                                "COOK_WITH_SUBSTITUTIONS"
                            ? "With Substitutions"
                            : "Requires Shopping"
                        }`
                        : ""}
                    </Text>
                  </View>

                  {selectedRecipeDetail.description
                    ? (
                      <Text
                        style={[styles.modalSubtitle, {
                          marginTop: spacing.xs,
                        }]}
                      >
                        {selectedRecipeDetail.description}
                      </Text>
                    )
                    : null}

                  <Text style={styles.recMeta}>
                    {selectedRecipeDetail.meal_type
                      ? `${selectedRecipeDetail.meal_type} · `
                      : ""}
                    {selectedRecipeDetail.difficulty
                      ? `${selectedRecipeDetail.difficulty} · `
                      : ""}
                    {selectedRecipeDetail.prep_minutes
                      ? `Prep ${selectedRecipeDetail.prep_minutes}m`
                      : ""}
                    {selectedRecipeDetail.cook_minutes
                      ? ` · Cook ${selectedRecipeDetail.cook_minutes}m`
                      : ""}
                  </Text>

                  {/* Servings Selector */}
                  <View
                    style={[styles.servingsRow, { marginVertical: spacing.sm }]}
                  >
                    <Text style={styles.servingsLabel}>Servings:</Text>
                    <View style={styles.stepper}>
                      <Pressable
                        onPress={() =>
                          setDetailServings((s) => Math.max(1, s - 1))}
                        style={styles.stepperBtn}
                        accessibilityRole="button"
                      >
                        <Ionicons
                          name="remove"
                          size={16}
                          color={colors.textPrimary}
                        />
                      </Pressable>
                      <Text style={styles.stepperValue}>{detailServings}</Text>
                      <Pressable
                        onPress={() =>
                          setDetailServings((s) => Math.min(12, s + 1))}
                        style={styles.stepperBtn}
                        accessibilityRole="button"
                      >
                        <Ionicons
                          name="add"
                          size={16}
                          color={colors.textPrimary}
                        />
                      </Pressable>
                    </View>
                  </View>

                  <Text style={styles.ingAvailableHeader}>Ingredients:</Text>
                  {selectedRecipeDetail.ingredients.map((ing, idx) => {
                    const scaledQty = scaleQuantityText(
                      ing.quantity,
                      selectedRecipeDetail.servings || 2,
                      detailServings,
                    );
                    const itemKey = `detail-${selectedRecipeDetail.recipe_id ?? selectedRecipeDetail.title}-${idx}`;
                    const recipeConfirmKey = selectedRecipeDetail.recipe_id ?? selectedRecipeDetail.title;
                    const isUserConfirmed = Boolean(
                      userConfirmedByRecipe[recipeConfirmKey]?.[ing.name.trim().toLowerCase()],
                    );
                    const live = getLiveIngredientStatus(ing, scaledQty, itemKey);
                    const isEffectivelyOk = live.isOk || isUserConfirmed;
                    return (
                      <View
                        key={itemKey}
                        style={[
                          styles.completeLineCard,
                          { marginTop: spacing.xs },
                        ]}
                      >
                        <Text style={styles.completeLineName}>
                          {live.displayName}
                        </Text>
                        <Text style={styles.plannedHint}>
                          Required: {formatQuantityWithUnit(
                            live.requiredQty,
                            ing.base_unit,
                          )} · Available: {formatQuantityWithUnit(
                            live.availableQty,
                            live.availableUnit,
                          )}
                        </Text>
                        <Text
                          style={
                            isEffectivelyOk
                              ? styles.ingAvailableText
                              : styles.ingMissingText
                          }
                        >
                          Status: {isUserConfirmed ? "Confirmed by you (temporary)" : live.statusLabel}
                        </Text>
                        {isUserConfirmed ? (
                          <View style={[styles.constraintsRow, { marginTop: spacing.xs }]}>
                            <Pressable
                              onPress={() => toggleRecipeUserConfirmed(recipeConfirmKey, ing.name)}
                              style={styles.constraintChip}
                            >
                              <Text style={styles.constraintChipText}>Undo confirmation</Text>
                            </Pressable>
                          </View>
                        ) : !live.isOk ? (
                          <View
                            style={[
                              styles.constraintsRow,
                              { marginTop: spacing.xs },
                            ]}
                          >
                            <Pressable
                              onPress={() => toggleRecipeUserConfirmed(recipeConfirmKey, ing.name)}
                              style={styles.constraintChip}
                            >
                              <Text style={styles.constraintChipText}>
                                I have this
                              </Text>
                            </Pressable>
                            <Pressable
                              onPress={() =>
                                openMissingResolutionModal({
                                  key: itemKey,
                                  name: live.displayName,
                                  canonical_item_id:
                                    ing.canonical_item_id ?? null,
                                  requiredQty: live.missingQty,
                                  unit: ing.base_unit,
                                  mode: "bought",
                                })}
                              style={styles.constraintChip}
                            >
                              <Text style={styles.constraintChipText}>
                                I bought it
                              </Text>
                            </Pressable>
                            <Pressable
                              onPress={() =>
                                setShoppingRequirements((prev) => ({
                                  ...prev,
                                  [itemKey]: {
                                    name: live.displayName,
                                    missingQty: String(live.missingQty),
                                    unit: ing.base_unit,
                                  },
                                }))}
                              style={[
                                styles.constraintChip,
                                Boolean(shoppingRequirements[itemKey]) &&
                                  styles.constraintChipSelected,
                              ]}
                            >
                                <Text
                                  style={[
                                    styles.constraintChipText,
                                    Boolean(shoppingRequirements[itemKey]) &&
                                    styles.constraintChipTextSelected,
                                  ]}
                                >
                                  Need to buy
                                </Text>
                              </Pressable>
                              {selectedRecipeDetail.substitutions.length > 0
                                ? (
                                  <Pressable
                                    onPress={() =>
                                      setAcceptedSubstitutions((prev) => ({
                                        ...prev,
                                        [itemKey]: true,
                                      }))}
                                    style={styles.constraintChip}
                                  >
                                    <Text style={styles.constraintChipText}>
                                      Use substitution
                                    </Text>
                                  </Pressable>
                                )
                                : null}
                            </View>
                          )
                          : null}
                      </View>
                    );
                  })}

                  <View style={{ marginTop: spacing.md }}>
                    <Text style={styles.ingAvailableHeader}>
                      Cooking Instructions:
                    </Text>
                    {(selectedRecipeDetail.structured_steps?.length ?? 0) > 0
                      ? selectedRecipeDetail.structured_steps!.map((st, idx) => {
                        const metaParts = [
                          st.duration_minutes ? `~${st.duration_minutes}m` : null,
                          st.heat_level ? `Heat: ${st.heat_level}` : null,
                          st.equipment?.length ? `Equipment: ${st.equipment.join(", ")}` : null,
                        ].filter(Boolean);
                        const qtyEntries = Object.entries(st.ingredient_quantities ?? {});
                        return (
                          <View key={idx} style={{ marginTop: 8 }}>
                            <Text style={styles.planTitle}>
                              {st.step_number || idx + 1}. {st.title}
                            </Text>
                            {metaParts.length > 0 ? (
                              <Text style={styles.planMeta}>{metaParts.join(" · ")}</Text>
                            ) : null}
                            <Text style={[styles.planMeta, { marginTop: 2 }]}>
                              {st.instruction}
                            </Text>
                            {qtyEntries.length > 0 ? (
                              <Text style={styles.planMeta}>
                                Quantities: {qtyEntries.map(([k, v]) => `${k} (${v})`).join(", ")}
                              </Text>
                            ) : null}
                            {st.completion_cue ? (
                              <Text style={styles.planMeta}>
                                Look for: {st.completion_cue}
                              </Text>
                            ) : null}
                            {st.caution ? (
                              <Text style={styles.planMeta}>
                                Caution: {st.caution}
                              </Text>
                            ) : null}
                          </View>
                        );
                      })
                      : selectedRecipeDetail.instructions.map((step, idx) => (
                        <Text
                          key={idx}
                          style={[styles.planMeta, { marginTop: 4 }]}
                        >
                          {idx + 1}. {step}
                        </Text>
                      ))}
                  </View>

                  {selectedRecipeDetail.substitutions.length > 0
                    ? (
                      <View style={{ marginTop: spacing.md }}>
                        <Text style={styles.ingAvailableHeader}>
                          Substitutions:
                        </Text>
                        {selectedRecipeDetail.substitutions.map((sub, idx) => (
                          <Text key={idx} style={styles.planMeta}>• {sub}</Text>
                        ))}
                      </View>
                    )
                    : null}

                  {(selectedRecipeDetail.equipment_required?.length ?? 0) > 0
                    ? (
                      <View style={{ marginTop: spacing.md }}>
                        <Text style={styles.ingAvailableHeader}>
                          Equipment Needed:
                        </Text>
                        <Text style={styles.planMeta}>
                          {selectedRecipeDetail.equipment_required!.join(" · ")}
                        </Text>
                      </View>
                    )
                    : null}

                  {(selectedRecipeDetail.preparation_steps?.length ?? 0) > 0
                    ? (
                      <View style={{ marginTop: spacing.md }}>
                        <Text style={styles.ingAvailableHeader}>
                          Before You Start (Prep):
                        </Text>
                        {selectedRecipeDetail.preparation_steps!.map((prep, idx) => (
                          <Text key={idx} style={styles.planMeta}>• {prep}</Text>
                        ))}
                      </View>
                    )
                    : null}

                  {selectedRecipeDetail.cooking_tips.length > 0
                    ? (
                      <View style={{ marginTop: spacing.md }}>
                        <Text style={styles.ingAvailableHeader}>
                          Cooking Tips:
                        </Text>
                        {selectedRecipeDetail.cooking_tips.map((tip, idx) => (
                          <Text key={idx} style={styles.planMeta}>• {tip}</Text>
                        ))}
                      </View>
                    )
                    : null}

                  {(selectedRecipeDetail.troubleshooting_tips?.length ?? 0) > 0
                    ? (
                      <View style={{ marginTop: spacing.md }}>
                        <Text style={styles.ingAvailableHeader}>
                          Troubleshooting:
                        </Text>
                        {selectedRecipeDetail.troubleshooting_tips!.map((tip, idx) => (
                          <Text key={idx} style={styles.planMeta}>• {tip}</Text>
                        ))}
                      </View>
                    )
                    : null}

                  {selectedRecipeDetail.serving_result
                    ? (
                      <View style={{ marginTop: spacing.md }}>
                        <Text style={styles.ingAvailableHeader}>
                          Expected Result:
                        </Text>
                        <Text style={styles.planMeta}>
                          {selectedRecipeDetail.serving_result}
                        </Text>
                      </View>
                    )
                    : null}

                  {selectedRecipeDetail.external_links.length > 0
                    ? (
                      <View style={{ marginTop: spacing.md }}>
                        <Text style={styles.ingAvailableHeader}>
                          Verified External References:
                        </Text>
                        {selectedRecipeDetail.external_links.map((
                          link,
                          idx,
                        ) => (
                          <Pressable
                            key={idx}
                            onPress={() =>
                              void handleOpenVerifiedLink(link.url)}
                            style={[styles.verifiedLinkButton, {
                              marginTop: 4,
                            }]}
                            accessibilityRole="link"
                          >
                            <Ionicons
                              name="open-outline"
                              size={14}
                              color={colors.primary}
                            />
                            <Text style={styles.verifiedLinkText}>
                              [{link.link_type === "video"
                                ? "Video"
                                : "Written Recipe"}] {link.title}
                              {link.source ? ` (${link.source})` : ""}
                            </Text>
                          </Pressable>
                        ))}
                      </View>
                    )
                    : null}
                </View>
              )
              : null}
          </ScrollView>
          <View style={styles.modalFooter}>
            {selectedRecipeDetail
              ? (() => {
                const recipeConfirmKey = selectedRecipeDetail.recipe_id ?? selectedRecipeDetail.title;
                const unresolvedDetail = selectedRecipeDetail.ingredients.filter(
                  (ing, idx) => {
                    const scaledQty = scaleQuantityText(
                      ing.quantity,
                      selectedRecipeDetail.servings || 2,
                      detailServings,
                    );
                    const itemKey = `detail-${selectedRecipeDetail.recipe_id ?? selectedRecipeDetail.title}-${idx}`;
                    const isUserConfirmed = Boolean(
                      userConfirmedByRecipe[recipeConfirmKey]?.[ing.name.trim().toLowerCase()],
                    );
                    return (
                      !isUserConfirmed &&
                      !getLiveIngredientStatus(ing, scaledQty, itemKey).isResolved
                    );
                  },
                );
                const cookBlocked =
                  isStartingPlan || unresolvedDetail.length > 0;
                return (
                  <View style={{ gap: 6 }}>
                    {unresolvedDetail.length > 0
                      ? (
                        <Text style={styles.ingMissingText}>
                          Resolve {unresolvedDetail.length}{" "}
                          missing or mismatched
                          ingredient{unresolvedDetail.length > 1
                            ? "s"
                            : ""}{" "}
                          above (I have it / I bought it / Use substitution)
                          before starting guided cooking.
                        </Text>
                      )
                      : null}
                    <Pressable
                      onPress={() =>
                        void handleStartCookingFromDetail(
                          selectedRecipeDetail,
                          detailServings,
                        )}
                      disabled={cookBlocked}
                      style={[
                        styles.confirmCompleteBtn,
                        cookBlocked && styles.buttonDisabled,
                      ]}
                      accessibilityRole="button"
                    >
                      <Text style={styles.confirmCompleteBtnText}>
                        {isStartingPlan
                          ? "Starting..."
                          : "Start Guided Cooking (No Stock Deducted)"}
                      </Text>
                    </Pressable>
                  </View>
                );
              })()
              : null}
          </View>
        </View>
      </Modal>

      {/* Guided Cooking Mode Modal */}
      <Modal
        visible={guidedSession !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => {
          setTimerRunning(false);
          setGuidedSession(null);
        }}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <View style={{ flex: 1 }}>
              <Text style={styles.modalTitle} numberOfLines={2}>
                {guidedSession?.detail.title ?? "Guided Cooking"}
              </Text>
              {guidedSession
                ? (() => {
                  const { plan, detail } = guidedSession;
                  const prog = guidedProgressByPlanId[plan.id];
                  const stepDispHdr = resolveCookingStep(detail, prog?.stepIndex ?? 0);
                  return (
                    <Text style={styles.planMeta}>
                      Step {stepDispHdr.stepNumber} of {stepDispHdr.totalSteps}
                    </Text>
                  );
                })()
                : null}
            </View>
            <Pressable
              onPress={() => {
                setTimerRunning(false);
                setGuidedSession(null);
              }}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Save progress and exit guided cooking"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>
          {guidedSession
            ? (() => {
              const { plan, detail } = guidedSession;
              const prog = guidedProgressByPlanId[plan.id] ?? {
                stepIndex: 0,
                servings: plan.planned_servings || detail.servings || 2,
                reviewAll: false,
                showIngredients: false,
              };
              const stepDisp = resolveCookingStep(detail, prog.stepIndex, prog.servings);
              const totalSteps = stepDisp.totalSteps;
              const safeStepIdx = stepDisp.stepNumber - 1;
              const progressPct = computeCookingProgress(safeStepIdx, totalSteps);
              const steps =
                (detail.structured_steps?.length ?? 0) > 0
                  ? detail.structured_steps!.map((s) => `${s.title}: ${s.instruction}`)
                  : detail.instructions.length > 0
                  ? detail.instructions
                  : ["Prepare and cook ingredients."];
              const stepMins = stepDisp.durationMinutes;
              const minsDisplay = Math.floor(timerRemainingSec / 60);
              const secsDisplay = String(timerRemainingSec % 60).padStart(
                2,
                "0",
              );

              return (
                <>
                  <View style={styles.cookingProgressWrap}>
                    <View style={styles.cookingProgressTrack}>
                      <View
                        style={[
                          styles.cookingProgressFill,
                          { width: `${progressPct}%` },
                        ]}
                      />
                    </View>
                    <Text style={styles.cookingProgressLabel}>
                      {progressPct}% complete
                    </Text>
                  </View>

                  <ScrollView contentContainerStyle={styles.modalContent}>
                    {/* Step Controls: View Ingredients (hidden by default) & Review All Steps */}
                    <View style={styles.constraintsRow}>
                      <Pressable
                        onPress={() =>
                          setGuidedProgressByPlanId((prev) => ({
                            ...prev,
                            [plan.id]: {
                              ...prog,
                              showIngredients: !prog.showIngredients,
                            },
                          }))}
                        style={[
                          styles.constraintChip,
                          Boolean(prog.showIngredients) && styles.constraintChipSelected,
                        ]}
                        accessibilityRole="button"
                      >
                        <Text
                          style={[
                            styles.constraintChipText,
                            Boolean(prog.showIngredients) &&
                              styles.constraintChipTextSelected,
                          ]}
                        >
                          {prog.showIngredients ? "Hide ingredients" : "View ingredients"}
                        </Text>
                      </Pressable>
                      <Pressable
                        onPress={() =>
                          setGuidedProgressByPlanId((prev) => ({
                            ...prev,
                            [plan.id]: { ...prog, reviewAll: !prog.reviewAll },
                          }))}
                        style={[
                          styles.constraintChip,
                          prog.reviewAll && styles.constraintChipSelected,
                        ]}
                        accessibilityRole="button"
                      >
                        <Text
                          style={[
                            styles.constraintChipText,
                            prog.reviewAll && styles.constraintChipTextSelected,
                          ]}
                        >
                          {prog.reviewAll ? "Focus One Step" : "Review All Steps"}
                        </Text>
                      </Pressable>
                    </View>

                    {prog.showIngredients ? (
                      <>
                    {/* Serving Adjuster */}
                    <View style={styles.servingsRow}>
                      <Text style={styles.servingsLabel}>Servings:</Text>
                      <View style={styles.stepper}>
                        <Pressable
                          onPress={() =>
                            setGuidedProgressByPlanId((prev) => ({
                              ...prev,
                              [plan.id]: {
                                ...prog,
                                servings: Math.max(1, prog.servings - 1),
                              },
                            }))}
                          style={styles.stepperBtn}
                        >
                          <Ionicons
                            name="remove"
                            size={16}
                            color={colors.textPrimary}
                          />
                        </Pressable>
                        <Text style={styles.stepperValue}>{prog.servings}</Text>
                        <Pressable
                          onPress={() =>
                            setGuidedProgressByPlanId((prev) => ({
                              ...prev,
                              [plan.id]: {
                                ...prog,
                                servings: Math.min(12, prog.servings + 1),
                              },
                            }))}
                          style={styles.stepperBtn}
                        >
                          <Ionicons
                            name="add"
                            size={16}
                            color={colors.textPrimary}
                          />
                        </Pressable>
                      </View>
                    </View>

                    {/* Scaled Ingredients */}
                    <View
                      style={[styles.completeLineCard, {
                        marginTop: spacing.sm,
                      }]}
                    >
                      <Text style={styles.ingAvailableHeader}>
                        Ingredients:
                      </Text>
                      {detail.ingredients.map((ing, i) => {
                        const scaledQty = scaleQuantityText(
                          ing.quantity,
                          detail.servings || 2,
                          prog.servings,
                        );
                        const gKey = `guided-${plan.id}-${i}`;
                        const live = getLiveIngredientStatus(
                          ing,
                          scaledQty,
                          gKey,
                        );
                        return (
                          <View
                            key={gKey}
                            style={{
                              paddingVertical: 6,
                              borderBottomWidth: 1,
                              borderBottomColor: colors.border,
                            }}
                          >
                            <Text style={styles.completeLineName}>
                              {live.displayName}
                            </Text>
                            <Text style={styles.plannedHint}>
                              Required: {formatQuantityWithUnit(
                                live.requiredQty,
                                ing.base_unit,
                              )} · Available: {formatQuantityWithUnit(
                                live.availableQty,
                                live.availableUnit,
                              )}
                            </Text>
                            <Text
                              style={live.isOk
                                ? styles.ingAvailableText
                                : styles.ingMissingText}
                            >
                              Status: {live.statusLabel}
                            </Text>
                            {!live.isOk
                              ? (
                                <View
                                  style={[
                                    styles.constraintsRow,
                                    { marginTop: 4 },
                                  ]}
                                >
                                  <Pressable
                                    onPress={() =>
                                      openMissingResolutionModal({
                                        key: gKey,
                                        name: live.displayName,
                                        canonical_item_id:
                                          ing.canonical_item_id ?? null,
                                        requiredQty: live.missingQty,
                                        unit: ing.base_unit,
                                        mode: "have",
                                      })}
                                    style={styles.constraintChip}
                                  >
                                    <Text style={styles.constraintChipText}>
                                      I have it
                                    </Text>
                                  </Pressable>
                                  <Pressable
                                    onPress={() =>
                                      openMissingResolutionModal({
                                        key: gKey,
                                        name: live.displayName,
                                        canonical_item_id:
                                          ing.canonical_item_id ?? null,
                                        requiredQty: live.missingQty,
                                        unit: ing.base_unit,
                                        mode: "bought",
                                      })}
                                    style={styles.constraintChip}
                                  >
                                    <Text style={styles.constraintChipText}>
                                      I bought it
                                    </Text>
                                  </Pressable>
                                  <Pressable
                                    onPress={() =>
                                      setShoppingRequirements((prev) => ({
                                        ...prev,
                                        [gKey]: {
                                          name: live.displayName,
                                          missingQty: String(live.missingQty),
                                          unit: ing.base_unit,
                                        },
                                      }))}
                                    style={styles.constraintChip}
                                  >
                                    <Text style={styles.constraintChipText}>
                                      Need to buy
                                    </Text>
                                  </Pressable>
                                </View>
                              )
                              : null}
                          </View>
                        );
                      })}
                      {detail.substitutions.length > 0
                        ? (
                          <Text style={[styles.planMeta, { marginTop: 4 }]}>
                            Substitutions: {detail.substitutions.join(" | ")}
                          </Text>
                        )
                        : null}
                    </View>
                      </>
                    ) : null}

                    {prog.reviewAll
                      ? (
                        <View style={styles.completeLineCard}>
                          {steps.map((st, idx) => (
                            <Text
                              key={idx}
                              style={[styles.planMeta, { marginBottom: 6 }]}
                            >
                              {idx + 1}. {st}
                            </Text>
                          ))}
                        </View>
                      )
                      : (
                        <View style={[styles.completeLineCard, { marginTop: spacing.sm }]}>
                          <Text style={styles.planMeta}>
                            Step {stepDisp.stepNumber} of {totalSteps}
                          </Text>
                          <Text style={[styles.completeLineName, { fontSize: 17, marginTop: 2 }]}>
                            {stepDisp.title}
                          </Text>
                          <View style={styles.stepMetaChipsRow}>
                            {stepDisp.durationMinutes ? (
                              <View style={styles.stepMetaChip}>
                                <Text style={styles.stepMetaChipText}>
                                  ~{stepDisp.durationMinutes} min
                                </Text>
                              </View>
                            ) : null}
                            {stepDisp.heatLevel ? (
                              <View style={styles.stepMetaChip}>
                                <Text style={styles.stepMetaChipText}>
                                  Heat: {stepDisp.heatLevel}
                                </Text>
                              </View>
                            ) : null}
                            {stepDisp.technique ? (
                              <View style={styles.stepMetaChip}>
                                <Text style={styles.stepMetaChipText}>
                                  {stepDisp.technique}
                                </Text>
                              </View>
                            ) : null}
                          </View>
                          <Text
                            style={[styles.chatBubbleText, {
                              marginTop: spacing.sm,
                              lineHeight: 22,
                            }]}
                          >
                            {stepDisp.instruction}
                          </Text>

                          {stepDisp.ingredientsUsed.length > 0 ? (
                            <View style={{ marginTop: spacing.sm }}>
                              <Text style={styles.fieldLabel}>
                                Ingredients for this step
                              </Text>
                              <View style={styles.stepMetaChipsRow}>
                                {stepDisp.ingredientsUsed.map((ingName, idx) => {
                                  const qtyText = stepDisp.ingredientQuantities[ingName];
                                  return (
                                    <View key={idx} style={styles.stepMetaChip}>
                                      <Text style={styles.stepMetaChipText}>
                                        {qtyText ? `${ingName} (${qtyText})` : ingName}
                                      </Text>
                                    </View>
                                  );
                                })}
                              </View>
                            </View>
                          ) : null}

                          {stepDisp.equipment.length > 0 ? (
                            <Text style={[styles.plannedHint, { marginTop: spacing.xs }]}>
                              Equipment: {stepDisp.equipment.join(", ")}
                            </Text>
                          ) : null}

                          {stepDisp.completionCue ? (
                            <View style={styles.completionCueBox}>
                              <Text style={styles.completionCueText}>
                                Look for: {stepDisp.completionCue}
                              </Text>
                            </View>
                          ) : null}

                          {stepDisp.caution ? (
                            <View style={[styles.completionCueBox, { marginTop: spacing.xs }]}>
                              <Text style={styles.completionCueText}>
                                Caution: {stepDisp.caution}
                              </Text>
                            </View>
                          ) : null}

                          {stepDisp.nextAction ? (
                            <Text style={[styles.plannedHint, { marginTop: spacing.xs }]}>
                              Next: {stepDisp.nextAction}
                            </Text>
                          ) : null}

                          {/* Optional Step-Specific Cooking Timer */}
                          {stepMins ? (
                            <View
                              style={[styles.constraintsRow, {
                                marginTop: spacing.md,
                              }]}
                            >
                              <Pressable
                                onPress={() => {
                                  setTimerRemainingSec(stepMins * 60);
                                  setTimerRunning(true);
                                }}
                                style={styles.constraintChip}
                              >
                                <Text style={styles.constraintChipText}>
                                  Start {stepMins}m Step Timer
                                </Text>
                              </Pressable>
                            </View>
                          ) : null}
                          {timerRemainingSec > 0
                            ? (
                              <View
                                style={[styles.planActionsRow, {
                                  marginTop: spacing.xs,
                                }]}
                              >
                                <Text style={styles.completeLineName}>
                                  Timer: {minsDisplay}:{secsDisplay}
                                </Text>
                                <Pressable
                                  onPress={() => setTimerRunning((r) => !r)}
                                  style={styles.omitChip}
                                >
                                  <Text style={styles.omitChipText}>
                                    {timerRunning ? "Pause" : "Resume"}
                                  </Text>
                                </Pressable>
                                <Pressable
                                  onPress={() => {
                                    setTimerRunning(false);
                                    setTimerRemainingSec(0);
                                  }}
                                  style={styles.omitChip}
                                >
                                  <Text style={styles.omitChipText}>Reset</Text>
                                </Pressable>
                              </View>
                            )
                            : null}
                        </View>
                      )}

                    {showFinishPrompt
                      ? (
                        <View
                          style={[styles.completeLineCard, {
                            marginTop: spacing.md,
                          }]}
                        >
                          <Text style={styles.completeLineName}>
                            Did you actually complete cooking {detail.title}?
                          </Text>
                          <Text style={styles.planMeta}>
                            Confirming lets you adjust actual quantities used
                            before deducting pantry stock via FEFO.
                          </Text>
                          <View
                            style={[styles.planActionsRow, {
                              marginTop: spacing.sm,
                            }]}
                          >
                            <Pressable
                              onPress={() => {
                                setTimerRunning(false);
                                setGuidedSession(null);
                                openCompleteMealModal(plan);
                              }}
                              style={styles.completeButton}
                            >
                              <Text style={styles.completeButtonText}>
                                Yes, Confirm Quantities
                              </Text>
                            </Pressable>
                            <Pressable
                              onPress={() => {
                                setTimerRunning(false);
                                setGuidedSession(null);
                                void handleCancelPlan(plan.id);
                              }}
                              style={styles.cancelButton}
                            >
                              <Text style={styles.cancelButtonText}>
                                Abandon (No Deduction)
                              </Text>
                            </Pressable>
                          </View>
                        </View>
                      )
                      : null}
                  </ScrollView>

                  <View style={styles.modalFooter}>
                    <View style={styles.planActionsRow}>
                      <Pressable
                        onPress={() =>
                          setGuidedProgressByPlanId((prev) => ({
                            ...prev,
                            [plan.id]: {
                              ...prog,
                              stepIndex: Math.max(0, safeStepIdx - 1),
                            },
                          }))}
                        disabled={safeStepIdx === 0}
                        style={[
                          styles.cancelButton,
                          safeStepIdx === 0 && styles.buttonDisabled,
                        ]}
                      >
                        <Text style={styles.cancelButtonText}>
                          Previous Step
                        </Text>
                      </Pressable>
                      {safeStepIdx < steps.length - 1
                        ? (
                          <Pressable
                            onPress={() =>
                              setGuidedProgressByPlanId((prev) => ({
                                ...prev,
                                [plan.id]: {
                                  ...prog,
                                  stepIndex: Math.min(
                                    steps.length - 1,
                                    safeStepIdx + 1,
                                  ),
                                },
                              }))}
                            style={styles.completeButton}
                          >
                            <Text style={styles.completeButtonText}>
                              Next Step
                            </Text>
                          </Pressable>
                        )
                        : (
                          <Pressable
                            onPress={() => {
                              const rid = detail.recipe_id ?? plan.recipe_id;
                              if (rid) {
                                void apiClient.post("/api/v1/recipes/feedback", {
                                  recipe_id: rid,
                                  recipe_title: detail.title,
                                  action: "cooked",
                                }).catch(() => undefined);
                              }
                              setShowFinishPrompt(true);
                            }}
                            style={styles.completeButton}
                          >
                            <Text style={styles.completeButtonText}>
                              Finish Cooking
                            </Text>
                          </Pressable>
                        )}
                    </View>
                  </View>
                </>
              );
            })()
            : null}
        </View>
      </Modal>

      {/* Dedicated Full-Screen AI Chef Modal */}
      <Modal
        visible={aiChefVisible}
        animationType="slide"
        presentationStyle="fullScreen"
        onRequestClose={() => setAiChefVisible(false)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Pressable
              onPress={() => setAiChefVisible(false)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Back to Meal History"
            >
              <Ionicons
                name="arrow-back"
                size={24}
                color={colors.textPrimary}
              />
            </Pressable>
            <Text style={[styles.modalTitle, { marginLeft: spacing.sm }]}>
              AI Chef
            </Text>
          </View>

          <ScrollView
            contentContainerStyle={styles.modalContent}
            keyboardShouldPersistTaps="handled"
          >
            {/* Quick Preference Selections */}
            <Text style={styles.ingAvailableHeader}>
              Preferences (optional):
            </Text>
            <View style={styles.constraintsRow}>
              {CONSTRAINT_OPTIONS.map((opt) => {
                const selected = selectedConstraints.includes(opt);
                return (
                  <Pressable
                    key={opt}
                    onPress={() => toggleConstraint(opt)}
                    style={[
                      styles.constraintChip,
                      selected && styles.constraintChipSelected,
                    ]}
                    accessibilityRole="button"
                    accessibilityState={{ selected }}
                  >
                    <Text
                      style={[
                        styles.constraintChipText,
                        selected && styles.constraintChipTextSelected,
                      ]}
                    >
                      {opt}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            {/* Hunger Level */}
            <Text
              style={[styles.ingAvailableHeader, { marginTop: spacing.sm }]}
            >
              Hunger Level:
            </Text>
            <View style={styles.constraintsRow}>
              {HUNGER_LEVELS.map((hl) => {
                const selected = hungerLevel === hl;
                return (
                  <Pressable
                    key={hl}
                    onPress={() => setHungerLevel(selected ? null : hl)}
                    style={[
                      styles.constraintChip,
                      selected && styles.constraintChipSelected,
                    ]}
                  >
                    <Text
                      style={[
                        styles.constraintChipText,
                        selected && styles.constraintChipTextSelected,
                      ]}
                    >
                      {hl}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            {/* 10-Recipe Personalisation Prompt & Mode Selector */}
            {showPersonalisationPrompt
              ? (
                <View style={styles.completeLineCard}>
                  <Text style={styles.completeLineName}>
                    You've tried 10 recipes! What would you like AI Chef to suggest next?
                  </Text>
                  <View style={[styles.constraintsRow, { marginTop: spacing.xs }]}>
                    {(
                      [
                        { id: "enjoyed", label: "Something I've enjoyed" },
                        { id: "new", label: "Something new" },
                        {
                          id: "balanced",
                          label: "No preference / Balanced recommendations",
                        },
                      ] as const
                    ).map((opt) => (
                      <Pressable
                        key={opt.id}
                        disabled={isSavingPrefMode}
                        onPress={() => void handleUpdateRecommendationMode(opt.id)}
                        style={[
                          styles.constraintChip,
                          recommendationMode === opt.id &&
                            styles.constraintChipSelected,
                        ]}
                      >
                        <Text
                          style={[
                            styles.constraintChipText,
                            recommendationMode === opt.id &&
                              styles.constraintChipTextSelected,
                          ]}
                        >
                          {opt.label}
                        </Text>
                      </Pressable>
                    ))}
                    <Pressable
                      disabled={isSavingPrefMode}
                      onPress={() => void handleUpdateRecommendationMode(null, true)}
                      style={styles.constraintChip}
                    >
                      <Text style={styles.constraintChipText}>Dismiss</Text>
                    </Pressable>
                  </View>
                </View>
              )
              : (
                <View style={{ marginBottom: spacing.sm }}>
                  <Text style={styles.ingAvailableHeader}>
                    Suggestion Style:
                  </Text>
                  <View style={styles.constraintsRow}>
                    {(
                      [
                        { id: "enjoyed", label: "Something I've enjoyed" },
                        { id: "new", label: "Something new" },
                        { id: "balanced", label: "Balanced" },
                      ] as const
                    ).map((opt) => {
                      const selected = recommendationMode === opt.id;
                      return (
                        <Pressable
                          key={opt.id}
                          disabled={isSavingPrefMode}
                          onPress={() => void handleUpdateRecommendationMode(opt.id)}
                          style={[
                            styles.constraintChip,
                            selected && styles.constraintChipSelected,
                          ]}
                        >
                          <Text
                            style={[
                              styles.constraintChipText,
                              selected && styles.constraintChipTextSelected,
                            ]}
                          >
                            {opt.label}
                          </Text>
                        </Pressable>
                      );
                    })}
                  </View>
                </View>
              )}

            {/* Cooking Time */}
            <Text
              style={[styles.ingAvailableHeader, { marginTop: spacing.sm }]}
            >
              Cooking Time:
            </Text>
            <View style={styles.constraintsRow}>
              {COOKING_TIME_OPTIONS.map((ct) => {
                const selected = cookingTimeMinutes === ct.minutes;
                return (
                  <Pressable
                    key={ct.minutes}
                    onPress={() =>
                      setCookingTimeMinutes(selected ? null : ct.minutes)}
                    style={[
                      styles.constraintChip,
                      selected && styles.constraintChipSelected,
                    ]}
                  >
                    <Text
                      style={[
                        styles.constraintChipText,
                        selected && styles.constraintChipTextSelected,
                      ]}
                    >
                      {ct.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            {/* Servings */}
            <View style={[styles.servingsRow, { marginVertical: spacing.sm }]}>
              <Text style={styles.servingsLabel}>Servings:</Text>
              <View style={styles.stepper}>
                <Pressable
                  onPress={() => setServings((s) => Math.max(1, s - 1))}
                  style={styles.stepperBtn}
                >
                  <Ionicons
                    name="remove"
                    size={16}
                    color={colors.textPrimary}
                  />
                </Pressable>
                <Text style={styles.stepperValue}>{servings}</Text>
                <Pressable
                  onPress={() => setServings((s) => Math.min(12, s + 1))}
                  style={styles.stepperBtn}
                >
                  <Ionicons name="add" size={16} color={colors.textPrimary} />
                </Pressable>
              </View>
            </View>

            {/* Custom Input + Selection-Only Submit */}
            <TextInput
              value={chatInput}
              onChangeText={setChatInput}
              placeholder="Optional: e.g. 'Replace paneer with tofu' or leave blank to use selections"
              placeholderTextColor={colors.textMuted}
              multiline
              style={styles.chatInput}
            />
            <Pressable
              onPress={() => void handleSendChat()}
              disabled={isSendingChat}
              style={[
                styles.sendChatButton,
                { marginTop: spacing.sm },
                isSendingChat && styles.buttonDisabled,
              ]}
              accessibilityRole="button"
            >
              {isSendingChat
                ? <ActivityIndicator color={colors.textOnPrimary} />
                : (
                  <>
                    <Ionicons
                      name="sparkles-outline"
                      size={18}
                      color={colors.textOnPrimary}
                    />
                    <Text style={styles.sendChatButtonText}>
                      Get Suggestions
                    </Text>
                  </>
                )}
            </Pressable>

            {isProviderOffline
              ? (
                <View
                  style={[
                    styles.providerBanner,
                    styles.providerBannerOffline,
                    { marginTop: spacing.sm },
                  ]}
                >
                  <Ionicons
                    name="alert-circle-outline"
                    size={16}
                    color={colors.warning}
                  />
                  <Text
                    style={[
                      styles.providerBannerText,
                      styles.providerTextOffline,
                    ]}
                  >
                    {latestProviderMessage ??
                      `Provider status: ${latestProviderStatus}`}
                  </Text>
                </View>
              )
              : null}

            {chatError
              ? (
                <View
                  style={[styles.errorBanner, { marginTop: spacing.sm }]}
                  accessibilityRole="alert"
                >
                  <Ionicons
                    name="alert-circle-outline"
                    size={18}
                    color={colors.error}
                  />
                  <Text style={styles.errorBannerText}>{chatError}</Text>
                </View>
              )
              : null}

            {/* Conversation Turns */}
            <View style={{ marginTop: spacing.md, gap: spacing.sm }}>
              {isSendingChat
                ? (
                  <View style={[styles.chatBubble, styles.assistantBubble]}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                      <ActivityIndicator size="small" color={colors.primary} />
                      <Text style={styles.chatBubbleText}>
                        AI Chef is preparing suggestions...
                      </Text>
                    </View>
                  </View>
                )
                : null}
              {chatTurns.map((turn) => (
                <View
                  key={turn.id}
                  style={[
                    styles.chatBubble,
                    turn.role === "user"
                      ? styles.userBubble
                      : styles.assistantBubble,
                  ]}
                >
                  <Text style={styles.chatRoleLabel}>
                    {turn.role === "user" ? "You" : "AI Chef"}
                  </Text>
                  <Text style={styles.chatBubbleText}>{turn.text}</Text>

                  {turn.suggestions?.map((sug, sIdx) => {
                    const sugKey = `${turn.id}-${sIdx}`;
                    const resolution = missingResolutions[sugKey];
                    const hasMissing = sug.missing_ingredients.length > 0;
                    const effectiveType = resolution === "has_unrecorded"
                      ? "COOK_NOW"
                      : resolution === "willing_to_buy"
                      ? "REQUIRES_SHOPPING"
                      : sug.recommendation_type;

                    return (
                      <View key={sugKey} style={styles.suggestionCard}>
                        <Text style={styles.suggestionTitle}>{sug.title}</Text>
                        <Text style={styles.suggestionMeta}>
                          {effectiveType === "COOK_NOW"
                            ? "Cook Now"
                            : effectiveType === "COOK_WITH_SUBSTITUTIONS"
                            ? "With Substitutions"
                            : "Requires Shopping"}
                          {` · ${sug.servings} servings`}
                          {sug.prep_minutes
                            ? ` · Prep ${sug.prep_minutes}m`
                            : ""}
                          {sug.cook_minutes
                            ? ` · Cook ${sug.cook_minutes}m`
                            : ""}
                          {` · ${sug.source_label || "AI-generated recipe"}`}
                        </Text>

                        {sug.available_ingredients.length > 0
                          ? (
                            <Text style={styles.ingAvailableText}>
                              ✓ In Pantry:{" "}
                              {sug.available_ingredients.map((a) => a.name)
                                .join(", ")}
                            </Text>
                          )
                          : null}

                        {hasMissing && resolution !== "has_unrecorded"
                          ? (
                            <View style={{ gap: 6, marginTop: 4 }}>
                              <Text style={styles.ingMissingText}>
                                Missing: {sug.missing_ingredients
                                  .map((m) =>
                                    `${m.name} (${m.quantity} ${m.base_unit})`
                                  )
                                  .join(", ")}
                              </Text>
                              {!resolution
                                ? (
                                  <View style={styles.completeLineCard}>
                                    <Text style={styles.planMeta}>
                                      Do you have this ingredient, or would you
                                      be willing to buy it?
                                    </Text>
                                    <View style={styles.constraintsRow}>
                                      <Pressable
                                        onPress={() =>
                                          setMissingResolutions((prev) => ({
                                            ...prev,
                                            [sugKey]: "has_unrecorded",
                                          }))}
                                        style={styles.constraintChip}
                                      >
                                        <Text style={styles.constraintChipText}>
                                          I have it (not in pantry)
                                        </Text>
                                      </Pressable>
                                      <Pressable
                                        onPress={() =>
                                          setMissingResolutions((prev) => ({
                                            ...prev,
                                            [sugKey]: "willing_to_buy",
                                          }))}
                                        style={styles.constraintChip}
                                      >
                                        <Text style={styles.constraintChipText}>
                                          Willing to buy
                                        </Text>
                                      </Pressable>
                                      <Pressable
                                        onPress={() =>
                                          void handleSendChat(
                                            `Suggest another recipe without ${
                                              sug.missing_ingredients
                                                .map((m) => m.name)
                                                .join(", ")
                                            } using only my pantry.`,
                                          )}
                                        style={styles.constraintChip}
                                      >
                                        <Text style={styles.constraintChipText}>
                                          Suggest another without it
                                        </Text>
                                      </Pressable>
                                    </View>
                                  </View>
                                )
                                : null}
                            </View>
                          )
                          : null}

                        <Pressable
                          onPress={() => {
                            const detail = suggestionToDetail(sug);
                            if (resolution === "has_unrecorded") {
                              detail.ingredients = detail.ingredients.map((
                                i,
                              ) => ({
                                ...i,
                                available: true,
                              }));
                            }
                            setAiChefVisible(false);
                            openRecipeDetail(detail);
                          }}
                          style={styles.startCookingBtn}
                          accessibilityRole="button"
                        >
                          <Text style={styles.startCookingBtnText}>
                            View Recipe & Cook
                          </Text>
                        </Pressable>
                      </View>
                    );
                  })}
                </View>
              ))}
            </View>
          </ScrollView>
        </View>
      </Modal>

      {/* Missing Ingredient Resolution Modal ("I have it" / "I bought it") */}
      <Modal
        visible={resolveTarget !== null}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setResolveTarget(null)}
      >
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>
              {resolveTarget?.mode === "have"
                ? "Confirm On-Hand Stock"
                : "Record Purchased Ingredient"}
            </Text>
            <Pressable
              onPress={() => setResolveTarget(null)}
              style={styles.closeButton}
              accessibilityRole="button"
              accessibilityLabel="Close ingredient resolution modal"
            >
              <Ionicons name="close" size={24} color={colors.textPrimary} />
            </Pressable>
          </View>
          <ScrollView
            contentContainerStyle={styles.modalContent}
            keyboardShouldPersistTaps="handled"
          >
            <Text style={styles.modalSubtitle}>
              {resolveTarget?.mode === "have"
                ? `What is the approximate total amount of ${resolveTarget.name} you currently have on hand? (Recipe needs ${formatQuantityWithUnit(resolveTarget.requiredQty, resolveTarget.unit)})`
                : "Record this purchase to create an eligible pantry lot and update purchase accounting."}
            </Text>

            {resolveError
              ? (
                <View style={styles.errorBanner} accessibilityRole="alert">
                  <Ionicons
                    name="alert-circle-outline"
                    size={18}
                    color={colors.error}
                  />
                  <Text style={styles.errorBannerText}>{resolveError}</Text>
                </View>
              )
              : null}

            <Text style={styles.fieldLabel}>Product / Ingredient Name</Text>
            <TextInput
              value={resolveNameText}
              onChangeText={setResolveNameText}
              placeholder="e.g. Whole Milk"
              placeholderTextColor={colors.textMuted}
              style={styles.input}
            />

            <View style={[styles.rowFields, { marginTop: spacing.sm }]}>
              <View style={styles.flex1}>
                <Text style={styles.fieldLabel}>
                  {resolveTarget?.mode === "have"
                    ? "Available Quantity"
                    : "Quantity Purchased"}
                </Text>
                <TextInput
                  value={resolveQtyText}
                  onChangeText={setResolveQtyText}
                  keyboardType="decimal-pad"
                  style={styles.input}
                />
              </View>
              <View style={styles.flex1}>
                <Text style={styles.fieldLabel}>Unit</Text>
                <View style={styles.constraintsRow}>
                  {(resolveTarget?.mode === "have"
                    ? getCompatibleUnitsForUnit((resolveTarget.unit as BaseUnit) || resolveUnit)
                    : (["g", "kg", "ml", "l", "pcs", "dozen"] as const)
                  ).map((u) => (
                    <Pressable
                      key={u}
                      onPress={() => setResolveUnit(u)}
                      style={[
                        styles.constraintChip,
                        resolveUnit === u && styles.constraintChipSelected,
                      ]}
                    >
                      <Text
                        style={[
                          styles.constraintChipText,
                          resolveUnit === u &&
                          styles.constraintChipTextSelected,
                        ]}
                      >
                        {u}
                      </Text>
                    </Pressable>
                  ))}
                </View>
              </View>
            </View>

            {resolveTarget?.mode === "have"
              ? (() => {
                const parsedQty = Number(resolveQtyText);
                const targetUnit = (
                  ["g", "kg", "ml", "l", "pcs", "dozen"].includes(resolveTarget.unit)
                    ? resolveTarget.unit
                    : resolveUnit
                ) as BaseUnit;
                const preview = Number.isFinite(parsedQty) && parsedQty > 0
                  ? evaluateReportedAvailability({
                    reportedQty: parsedQty,
                    reportedUnit: resolveUnit,
                    requiredQty: resolveTarget.requiredQty,
                    requiredUnit: targetUnit,
                  })
                  : null;
                return (
                  <View style={{ marginTop: spacing.sm, gap: spacing.xs }}>
                    {preview && preview.ok
                      ? (
                        <Text
                          style={preview.status === "sufficient"
                            ? styles.ingAvailableText
                            : styles.ingMissingText}
                        >
                          {preview.status === "sufficient"
                            ? `Sufficient for this recipe (~${formatQuantityWithUnit(preview.remainingAfterRecipe, targetUnit)} left after cooking).`
                            : `Insufficient for this recipe (still short by ${formatQuantityWithUnit(preview.shortageQty, targetUnit)}).`}
                        </Text>
                      )
                      : preview && !preview.ok
                      ? <Text style={styles.ingMissingText}>{preview.error}</Text>
                      : null}

                    <Text style={styles.fieldLabel}>Note (optional)</Text>
                    <TextInput
                      value={resolveNoteText}
                      onChangeText={setResolveNoteText}
                      placeholder="e.g. Half pack open in fridge"
                      placeholderTextColor={colors.textMuted}
                      style={styles.input}
                    />

                    <Pressable
                      onPress={() => setResolvePersistToPantry((p) => !p)}
                      style={[
                        styles.constraintChip,
                        resolvePersistToPantry && styles.constraintChipSelected,
                        { alignSelf: "flex-start", marginTop: spacing.xs },
                      ]}
                      accessibilityRole="checkbox"
                      accessibilityState={{ checked: resolvePersistToPantry }}
                    >
                      <Text
                        style={[
                          styles.constraintChipText,
                          resolvePersistToPantry && styles.constraintChipTextSelected,
                        ]}
                      >
                        {resolvePersistToPantry
                          ? "Also save to persistent Pantry: ON"
                          : "Session-only by default (tap to also save to Pantry)"}
                      </Text>
                    </Pressable>

                    <View style={[styles.constraintsRow, { marginTop: spacing.xs }]}>
                      <Pressable
                        onPress={() => {
                          if (!resolveTarget) return;
                          const rec = buildSessionAvailabilityRecord({
                            key: resolveTarget.key,
                            name: resolveNameText.trim() || resolveTarget.name,
                            canonical_item_id: resolveTarget.canonical_item_id,
                            reportedQty: 0,
                            reportedUnit: resolveUnit,
                            requiredQty: resolveTarget.requiredQty,
                            requiredUnit: targetUnit,
                            persistedToPantry: false,
                            note: resolveNoteText,
                            mode: "unavailable",
                          });
                          setSessionReportedAvailability((prev) => ({
                            ...prev,
                            [resolveTarget.key]: rec,
                          }));
                          setResolveTarget(null);
                        }}
                        style={styles.constraintChip}
                      >
                        <Text style={styles.constraintChipText}>
                          Mark Unavailable
                        </Text>
                      </Pressable>
                      <Pressable
                        onPress={() => {
                          if (!resolveTarget) return;
                          const rec = buildSessionAvailabilityRecord({
                            key: resolveTarget.key,
                            name: resolveNameText.trim() || resolveTarget.name,
                            canonical_item_id: resolveTarget.canonical_item_id,
                            reportedQty: 0,
                            reportedUnit: resolveUnit,
                            requiredQty: resolveTarget.requiredQty,
                            requiredUnit: targetUnit,
                            persistedToPantry: false,
                            note: resolveNoteText,
                            mode: "skipped",
                          });
                          setSessionReportedAvailability((prev) => ({
                            ...prev,
                            [resolveTarget.key]: rec,
                          }));
                          setResolveTarget(null);
                        }}
                        style={styles.constraintChip}
                      >
                        <Text style={styles.constraintChipText}>
                          Skip for Session
                        </Text>
                      </Pressable>
                    </View>
                  </View>
                );
              })()
              : null}

            {resolveTarget?.mode === "bought"
              ? (
                <View style={[styles.rowFields, { marginTop: spacing.sm }]}>
                  <View style={styles.flex1}>
                    <Text style={styles.fieldLabel}>Price (₹ optional)</Text>
                    <TextInput
                      value={resolvePriceText}
                      onChangeText={setResolvePriceText}
                      placeholder="0"
                      placeholderTextColor={colors.textMuted}
                      keyboardType="decimal-pad"
                      style={styles.input}
                    />
                  </View>
                  <View style={styles.flex1}>
                    <Text style={styles.fieldLabel}>Merchant (optional)</Text>
                    <TextInput
                      value={resolveMerchantText}
                      onChangeText={setResolveMerchantText}
                      placeholder="Grocery Store"
                      placeholderTextColor={colors.textMuted}
                      style={styles.input}
                    />
                  </View>
                </View>
              )
              : null}
          </ScrollView>
          <View style={styles.modalFooter}>
            <Pressable
              onPress={() => void handleConfirmResolveIngredient()}
              disabled={isResolvingIngredient}
              style={[
                styles.confirmCompleteBtn,
                isResolvingIngredient && styles.buttonDisabled,
              ]}
              accessibilityRole="button"
            >
              {isResolvingIngredient
                ? <ActivityIndicator color={colors.textOnPrimary} />
                : (
                  <Text style={styles.confirmCompleteBtnText}>
                    {resolveTarget?.mode === "have"
                      ? resolvePersistToPantry
                        ? "Confirm & Save to Pantry"
                        : "Confirm for This Session"
                      : "Confirm Purchase & Add to Pantry"}
                  </Text>
                )}
            </Pressable>
          </View>
        </View>
      </Modal>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  scrollContent: {
    paddingBottom: spacing.xxl,
    gap: spacing.md,
  },
  header: {
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
  providerBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radii.md,
    borderWidth: 1,
  },
  providerBannerOffline: {
    backgroundColor: colors.warningBg,
    borderColor: colors.warning,
  },
  providerBannerLive: {
    backgroundColor: colors.primaryLight,
    borderColor: colors.primary,
  },
  providerBannerText: {
    flex: 1,
    fontSize: 13,
    fontWeight: "600",
  },
  providerTextOffline: {
    color: colors.warning,
  },
  providerTextLive: {
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
  },
  errorBannerText: {
    flex: 1,
    fontSize: 13,
    color: colors.error,
  },
  section: {
    gap: spacing.sm,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.textSecondary,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  activePlanCard: {
    backgroundColor: colors.surface,
    borderColor: colors.primary,
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.md,
  },
  planTopRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: spacing.sm,
  },
  flex1: {
    flex: 1,
  },
  planTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  planMeta: {
    fontSize: 13,
    color: colors.textSecondary,
    marginTop: 2,
  },
  plannedBadge: {
    backgroundColor: colors.primaryLight,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.full,
  },
  plannedBadgeText: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.primary,
  },
  planActionsRow: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  completeButton: {
    flex: 1.5,
    minHeight: layout.minTapTarget,
    backgroundColor: colors.primary,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
  },
  completeButtonText: {
    color: colors.textOnPrimary,
    fontSize: 14,
    fontWeight: "600",
  },
  cancelButton: {
    flex: 1,
    minHeight: layout.minTapTarget,
    backgroundColor: colors.errorBg,
    borderColor: colors.errorBorder,
    borderWidth: 1,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
  },
  cancelButtonText: {
    color: colors.error,
    fontSize: 14,
    fontWeight: "600",
  },
  chatInput: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.md,
    padding: spacing.md,
    minHeight: 72,
    fontSize: 15,
    color: colors.textPrimary,
    textAlignVertical: "top",
  },
  servingsRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  servingsLabel: {
    fontSize: 14,
    fontWeight: "600",
    color: colors.textSecondary,
  },
  stepper: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
  },
  stepperBtn: {
    width: 36,
    height: 36,
    borderRadius: radii.full,
    borderColor: colors.border,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surfaceMuted,
  },
  stepperValue: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.textPrimary,
    minWidth: 20,
    textAlign: "center",
  },
  constraintsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
  },
  constraintChip: {
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.full,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    backgroundColor: colors.surfaceMuted,
  },
  constraintChipSelected: {
    borderColor: colors.primary,
    backgroundColor: colors.primaryLight,
  },
  constraintChipText: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.textSecondary,
  },
  constraintChipTextSelected: {
    color: colors.primary,
  },
  sendChatButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    backgroundColor: colors.primary,
    minHeight: layout.minTapTarget,
    borderRadius: radii.md,
  },
  sendChatButtonText: {
    color: colors.textOnPrimary,
    fontSize: 15,
    fontWeight: "600",
  },
  buttonDisabled: {
    opacity: 0.55,
  },
  chatBubble: {
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.sm,
    borderWidth: 1,
  },
  userBubble: {
    backgroundColor: colors.primaryLight,
    borderColor: colors.primary,
  },
  assistantBubble: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
  },
  chatRoleLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.textMuted,
    textTransform: "uppercase",
  },
  chatBubbleText: {
    fontSize: 15,
    lineHeight: 22,
    color: colors.textPrimary,
  },
  suggestionCard: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.md,
    padding: spacing.md,
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  suggestionTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  suggestionMeta: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  ingGroup: {
    gap: 2,
  },
  ingAvailableHeader: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.primary,
  },
  ingAvailableText: {
    fontSize: 13,
    color: colors.textPrimary,
  },
  ingMissingHeader: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.warning,
  },
  ingMissingText: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  linksRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
  },
  verifiedLinkButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    backgroundColor: colors.primaryLight,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radii.full,
  },
  verifiedLinkText: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.primary,
  },
  startCookingBtn: {
    minHeight: layout.minTapTarget,
    backgroundColor: colors.primary,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
    marginTop: spacing.xs,
  },
  startCookingBtnText: {
    color: colors.textOnPrimary,
    fontSize: 14,
    fontWeight: "600",
  },
  recCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  recTopRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: spacing.sm,
  },
  recTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  recMeta: {
    fontSize: 13,
    color: colors.textSecondary,
    marginTop: 2,
  },
  expiringTag: {
    backgroundColor: colors.warningBg,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.full,
  },
  expiringTagText: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.warning,
  },
  missingInlineText: {
    fontSize: 13,
    color: colors.warning,
  },
  allInStockText: {
    fontSize: 13,
    fontWeight: "600",
    color: colors.primary,
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
  modalContent: {
    padding: spacing.lg,
    gap: spacing.md,
  },
  modalSubtitle: {
    fontSize: 14,
    lineHeight: 20,
    color: colors.textSecondary,
  },
  completeLineCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.md,
    padding: spacing.md,
    gap: spacing.sm,
  },
  completeLineHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  completeLineName: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.textPrimary,
  },
  omitChip: {
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.full,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    backgroundColor: colors.surfaceMuted,
  },
  omitChipSelected: {
    borderColor: colors.warning,
    backgroundColor: colors.warningBg,
  },
  omitChipText: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.textSecondary,
  },
  omitChipTextSelected: {
    color: colors.warning,
  },
  plannedHint: {
    fontSize: 13,
    color: colors.textSecondary,
  },
  rowFields: {
    flexDirection: "row",
    gap: spacing.md,
  },
  fieldLabel: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.textSecondary,
    marginBottom: 4,
  },
  input: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.md,
    minHeight: layout.minTapTarget,
    fontSize: 15,
    color: colors.textPrimary,
  },
  modalFooter: {
    padding: spacing.lg,
    backgroundColor: colors.surface,
    borderTopColor: colors.border,
    borderTopWidth: 1,
  },
  confirmCompleteBtn: {
    minHeight: layout.minTapTarget,
    backgroundColor: colors.primary,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
  },
  confirmCompleteBtnText: {
    color: colors.textOnPrimary,
    fontSize: 15,
    fontWeight: "600",
  },
  cookingProgressWrap: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    gap: 4,
  },
  cookingProgressTrack: {
    height: 6,
    borderRadius: radii.full,
    backgroundColor: colors.surfaceMuted,
    overflow: "hidden",
  },
  cookingProgressFill: {
    height: "100%",
    backgroundColor: colors.primary,
    borderRadius: radii.full,
  },
  cookingProgressLabel: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.textMuted,
  },
  stepMetaChipsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  stepMetaChip: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  stepMetaChipText: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.textSecondary,
  },
  completionCueBox: {
    marginTop: spacing.sm,
    padding: spacing.sm,
    borderRadius: radii.sm,
    backgroundColor: "#DCFCE7",
  },
  completionCueText: {
    fontSize: 12,
    fontWeight: "600",
    color: "#15803D",
  },
});
