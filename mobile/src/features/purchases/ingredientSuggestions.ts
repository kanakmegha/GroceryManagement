/**
 * Pure helper for ranking inline canonical ingredient suggestions and applying
 * explicit user selections without ever silently overwriting custom display names.
 */

export interface BackendIngredientSuggestion {
  variant_id: string;
  variant_name: string;
  family_id: string;
  family_name: string;
  canonical_ingredient_id: string | null;
  score: number;
}

export interface CatalogIngredientOption {
  id: string;
  name: string;
  category: string;
  standard_unit: string;
}

export interface RankedIngredientSuggestion {
  key: string;
  label: string;
  canonical_ingredient_id: string | null;
  variant_id: string | null;
  family_name: string | null;
  category: string | null;
  score: number;
  isBestMatch: boolean;
  source: "resolver" | "catalog";
}

function normalizeQuery(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
}

function scoreCatalogMatch(query: string, candidateName: string): number {
  const q = normalizeQuery(query);
  const c = normalizeQuery(candidateName);
  if (!q || !c) return 0;
  if (q === c) return 100;
  if (c.startsWith(q) || q.startsWith(c)) return 88;
  if (c.includes(q) || q.includes(c)) return 76;

  const qTokens = q.split(" ").filter((t) => t.length >= 2);
  const cTokens = c.split(" ").filter((t) => t.length >= 2);
  if (qTokens.length === 0 || cTokens.length === 0) return 0;

  let matched = 0;
  for (const qt of qTokens) {
    if (cTokens.some((ct) => ct === qt || ct.startsWith(qt) || qt.startsWith(ct))) {
      matched += 1;
    }
  }
  if (matched === 0) return 0;
  return Math.round(50 + (matched / Math.max(qTokens.length, cTokens.length)) * 35);
}

/**
 * Combine backend resolver suggestions with live ranked matches from the canonical
 * ingredient catalogue. Returns at most `limit` (default 4) unique suggestions
 * sorted by descending confidence score, tagging the top item as `isBestMatch`.
 */
export function buildRankedIngredientSuggestions(params: {
  query: string;
  backendSuggestions?: BackendIngredientSuggestion[] | null;
  catalog?: CatalogIngredientOption[] | null;
  limit?: number;
}): RankedIngredientSuggestion[] {
  const limit = params.limit ?? 4;
  const seenIds = new Set<string>();
  const seenLabels = new Set<string>();
  const merged: RankedIngredientSuggestion[] = [];

  for (const sug of params.backendSuggestions ?? []) {
    const label = (sug.variant_name || "").trim();
    if (!label) continue;
    const normLabel = label.toLowerCase();
    const idKey = sug.canonical_ingredient_id || sug.variant_id || normLabel;
    if (seenIds.has(idKey) || seenLabels.has(normLabel)) continue;
    seenIds.add(idKey);
    seenLabels.add(normLabel);
    merged.push({
      key: `res-${sug.variant_id || idKey}`,
      label,
      canonical_ingredient_id: sug.canonical_ingredient_id,
      variant_id: sug.variant_id,
      family_name: sug.family_name || null,
      category: null,
      score: Math.max(0, Math.min(100, Math.round(sug.score))),
      isBestMatch: false,
      source: "resolver",
    });
  }

  const trimmedQuery = params.query.trim();
  if (trimmedQuery.length >= 2) {
    for (const opt of params.catalog ?? []) {
      const score = scoreCatalogMatch(trimmedQuery, opt.name);
      if (score < 50) continue;
      const normLabel = opt.name.trim().toLowerCase();
      if (seenIds.has(opt.id) || seenLabels.has(normLabel)) continue;
      seenIds.add(opt.id);
      seenLabels.add(normLabel);
      merged.push({
        key: `cat-${opt.id}`,
        label: opt.name.trim(),
        canonical_ingredient_id: opt.id,
        variant_id: null,
        family_name: null,
        category: opt.category || null,
        score,
        isBestMatch: false,
        source: "catalog",
      });
    }
  }

  merged.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label));
  const top = merged.slice(0, limit);
  if (top.length > 0) {
    top[0] = { ...top[0], isBestMatch: true };
  }
  return top;
}

/**
 * Produce the explicit line-item state patch when a user intentionally clicks
 * a canonical suggestion. Replaces `display_name` ONLY because the user clicked.
 */
export function buildExplicitSelectionPatch(suggestion: RankedIngredientSuggestion) {
  return {
    display_name: suggestion.label,
    standardized_name: suggestion.label,
    ingredient_family_name: suggestion.family_name,
    ingredient_variant_id: suggestion.variant_id,
    canonical_ingredient_id: suggestion.canonical_ingredient_id,
    ...(suggestion.category ? { ingredient_category: suggestion.category } : {}),
    ingredient_confidence_tier: "HIGH" as const,
    ingredient_confidence_score: 100,
    user_approved_ingredient: true,
    ingredient_review_reason: null,
  };
}
