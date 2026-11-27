import {
  buildExplicitSelectionPatch,
  buildRankedIngredientSuggestions,
  type BackendIngredientSuggestion,
  type CatalogIngredientOption,
} from "../ingredientSuggestions";

const CATALOG: CatalogIngredientOption[] = [
  { id: "ing-sugar", name: "Sugar", category: "Pantry", standard_unit: "g" },
  { id: "ing-brown-sugar", name: "Brown Sugar", category: "Pantry", standard_unit: "g" },
  { id: "ing-jaggery", name: "Jaggery", category: "Pantry", standard_unit: "g" },
  { id: "ing-milk", name: "Whole Milk", category: "Dairy", standard_unit: "ml" },
];

const BACKEND_SUGGESTIONS: BackendIngredientSuggestion[] = [
  {
    variant_id: "var-sugar",
    variant_name: "Sugar",
    family_id: "fam-sweet",
    family_name: "Sweeteners",
    canonical_ingredient_id: "ing-sugar",
    score: 94,
  },
  {
    variant_id: "var-brown-sugar",
    variant_name: "Brown Sugar",
    family_id: "fam-sweet",
    family_name: "Sweeteners",
    canonical_ingredient_id: "ing-brown-sugar",
    score: 78,
  },
  {
    variant_id: "var-jaggery",
    variant_name: "Jaggery",
    family_id: "fam-sweet",
    family_name: "Sweeteners",
    canonical_ingredient_id: "ing-jaggery",
    score: 65,
  },
];

describe("buildRankedIngredientSuggestions", () => {
  it("ranks resolver and catalog suggestions by confidence and marks best match", () => {
    const ranked = buildRankedIngredientSuggestions({
      query: "chini df",
      backendSuggestions: BACKEND_SUGGESTIONS,
      catalog: CATALOG,
      limit: 4,
    });

    expect(ranked.map((s) => s.label)).toEqual(["Sugar", "Brown Sugar", "Jaggery"]);
    expect(ranked[0].isBestMatch).toBe(true);
    expect(ranked[1].isBestMatch).toBe(false);
    expect(ranked[0].canonical_ingredient_id).toBe("ing-sugar");
    expect(ranked[0].variant_id).toBe("var-sugar");
  });

  it("matches catalog ingredients live when user types without backend suggestions", () => {
    const ranked = buildRankedIngredientSuggestions({
      query: "sugar",
      backendSuggestions: [],
      catalog: CATALOG,
      limit: 4,
    });

    expect(ranked.length).toBe(2);
    expect(ranked[0].label).toBe("Sugar");
    expect(ranked[0].isBestMatch).toBe(true);
    expect(ranked[1].label).toBe("Brown Sugar");
  });

  it("explicit selection populates display_name, canonical IDs, and approval", () => {
    const ranked = buildRankedIngredientSuggestions({
      query: "chini df",
      backendSuggestions: BACKEND_SUGGESTIONS,
      catalog: CATALOG,
    });

    const patch = buildExplicitSelectionPatch(ranked[0]);
    expect(patch.display_name).toBe("Sugar");
    expect(patch.standardized_name).toBe("Sugar");
    expect(patch.canonical_ingredient_id).toBe("ing-sugar");
    expect(patch.ingredient_variant_id).toBe("var-sugar");
    expect(patch.user_approved_ingredient).toBe(true);
    expect(patch.ingredient_confidence_tier).toBe("HIGH");
  });

  it("returns no misleading suggestions for a completely unrelated name", () => {
    const ranked = buildRankedIngredientSuggestions({
      query: "zxqvw random gadget",
      backendSuggestions: [],
      catalog: CATALOG,
    });
    expect(ranked).toEqual([]);
  });

  it("updates suggestions immediately as user changes input", () => {
    const first = buildRankedIngredientSuggestions({
      query: "milk",
      backendSuggestions: [],
      catalog: CATALOG,
    });
    const second = buildRankedIngredientSuggestions({
      query: "jaggery",
      backendSuggestions: [],
      catalog: CATALOG,
    });
    expect(first.map((s) => s.label)).toEqual(["Whole Milk"]);
    expect(second.map((s) => s.label)).toEqual(["Jaggery"]);
  });
});
