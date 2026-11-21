export interface IngredientReviewLineLike {
  display_name: string;
  raw_text?: string;
  track_inventory: boolean;
  budgetOnly: boolean;
  budget_treatment?: string | null;
  ingredient_confidence_tier?: string | null;
  ingredient_confidence_score?: number | null;
  user_approved_ingredient?: boolean;
}

export function getIngredientConfidenceLabel(line: IngredientReviewLineLike): string {
  if (line.user_approved_ingredient) {
    return "Approved";
  }
  switch (line.ingredient_confidence_tier) {
    case "HIGH":
      return line.ingredient_confidence_score != null
        ? `High (${line.ingredient_confidence_score}%)`
        : "High";
    case "MEDIUM":
      return line.ingredient_confidence_score != null
        ? `Verify (${line.ingredient_confidence_score}%)`
        : "Verify";
    case "LOW":
      return "Low match";
    case "NON_PANTRY":
      return "Non-pantry";
    default:
      return "Unmatched";
  }
}

export function findUnresolvedIngredientLines<T extends IngredientReviewLineLike>(
  lines: T[],
): T[] {
  return lines.filter(
    (l) =>
      (l.track_inventory || l.budget_treatment === "requires_user_review") &&
      !l.budgetOnly &&
      (l.ingredient_confidence_tier === "LOW" ||
        l.ingredient_confidence_tier === "UNMATCHED") &&
      !l.user_approved_ingredient,
  );
}
