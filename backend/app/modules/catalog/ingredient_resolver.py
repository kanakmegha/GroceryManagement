"""Central authoritative IngredientResolver service (Phase 7.2).

Follows the matching priority in `indian_household_ingredient_catalogue.json`:
1. Confirmed household product mapping / confirmed household alias
2. Exact variant name
3. Exact variant alias
4. Longest and most specific phrase (boundary-aware, preferring variants over generic families)
5. Family alias (resolving to `<family>__unspecified`)
6. Unresolved review when ambiguous or unknown (fuzzy matches suggest only, never auto-assign)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.ingredient_catalogue import (
    CatalogueVariant,
    IngredientCatalogue,
    get_ingredient_catalogue,
    normalize_ingredient_text,
)
from app.modules.inventory.models import CanonicalIngredient, IngredientAlias, IngredientMapping

_PACK_NOISE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kg|kgs|g|gm|gms|gram|grams|l|ltr|litre|litres|liter|liters|ml|pcs|pc|pack|packs|pkt|packet|packets|bottle|bottles|btl|dozen|doz)\b",
    re.IGNORECASE,
)
_BRAND_NOISE_TOKENS = frozenset(
    {
        "amul", "aavin", "nandini", "mother", "dairy", "heritage", "milky", "mist",
        "country", "delight", "akshayakalpa", "nestle", "britannia", "gowardhan",
        "india", "gate", "daawat", "kohinoor", "fortune", "saffola", "dhara",
        "sundrop", "tata", "sampann", "aashirvaad", "pillsbury", "everest", "mdh",
        "catch", "mtr", "aachi", "sakthi", "fresho", "royal", "fresh", "pure",
        "classic", "premium", "select", "special", "taaza", "udhaiyam", "eastern",
        "prabhat", "generic", "local", "brand", "another",
    }
)


@dataclass
class IngredientResolutionResult:
    family_id: str | None
    family_name: str | None
    variant_id: str | None
    variant_name: str | None
    variant_status: str | None
    is_unspecified_variant: bool
    category: str | None
    measurement: dict[str, Any] | None
    match_source: str
    confidence: Decimal
    requires_review: bool
    original_receipt_text: str
    normalized_text: str
    suggestions: list[dict[str, str]] = field(default_factory=list)
    reason: str = ""
    canonical_ingredient_id: UUID | None = None


def _phrase_boundary_match(phrase_norm: str, haystack_norm: str) -> bool:
    if not phrase_norm or not haystack_norm:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(phrase_norm)}(?!\w)", haystack_norm))


def _strip_brand_and_pack_noise(text_norm: str) -> str:
    cleaned = _PACK_NOISE_RE.sub(" ", text_norm)
    tokens = [t for t in cleaned.split() if t not in _BRAND_NOISE_TOKENS]
    return " ".join(tokens).strip()


class IngredientResolver:
    """Authoritative family + variant ingredient resolver."""

    def __init__(self, catalogue: IngredientCatalogue | None = None) -> None:
        self.catalogue = catalogue or get_ingredient_catalogue()

    def resolve_text(
        self,
        receipt_text: str,
        *,
        household_aliases: dict[str, CatalogueVariant] | None = None,
        confirmed_variant: CatalogueVariant | None = None,
    ) -> IngredientResolutionResult:
        """Resolve a receipt description deterministically without DB I/O."""
        orig = str(receipt_text or "").strip()
        norm = normalize_ingredient_text(orig)
        stripped = _strip_brand_and_pack_noise(norm)
        candidates_to_test = [t for t in (norm, stripped) if t]

        # 1. Confirmed product mapping (if supplied by caller)
        if confirmed_variant is not None:
            return self._build_result(
                confirmed_variant,
                match_source="confirmed_product_mapping",
                confidence=Decimal("1.000"),
                requires_review=False,
                orig=orig,
                norm=norm,
                reason="confirmed_product_mapping",
            )

        # 1b. Confirmed household-learned alias
        if household_aliases:
            for candidate_str in candidates_to_test:
                if candidate_str in household_aliases:
                    v = household_aliases[candidate_str]
                    return self._build_result(
                        v,
                        match_source="household_learned_alias",
                        confidence=Decimal("0.980"),
                        requires_review=False,
                        orig=orig,
                        norm=norm,
                        reason=f"household_alias:{candidate_str}",
                    )

        # 2. Exact variant name (prefer specified variants)
        for candidate_str in candidates_to_test:
            exact_vars = self.catalogue.variants_by_norm_name.get(candidate_str) or []
            specified_exact = [v for v in exact_vars if not v.is_unspecified_variant]
            if len(specified_exact) == 1:
                return self._build_result(
                    specified_exact[0],
                    match_source="exact_variant_name",
                    confidence=Decimal("0.990"),
                    requires_review=False,
                    orig=orig,
                    norm=norm,
                    reason=f"exact_variant_name:{candidate_str}",
                )

        # 3. Exact variant alias
        for candidate_str in candidates_to_test:
            alias_vars = self.catalogue.variant_aliases.get(candidate_str) or []
            if len(alias_vars) == 1:
                return self._build_result(
                    alias_vars[0],
                    match_source="exact_variant_alias",
                    confidence=Decimal("0.960"),
                    requires_review=False,
                    orig=orig,
                    norm=norm,
                    reason=f"exact_variant_alias:{candidate_str}",
                )
            if len(alias_vars) > 1:
                return self._build_unresolved(
                    orig=orig,
                    norm=norm,
                    reason=f"ambiguous_variant_alias:{candidate_str}",
                    suggestions=alias_vars,
                )

        # 4. Longest and most specific phrase (boundary-aware)
        # Collect all matching (phrase_len, priority_rank, variant)
        phrase_hits: list[tuple[int, int, str, CatalogueVariant]] = []
        seen_hit_keys: set[tuple[str, str]] = set()
        search_space = stripped or norm

        for v in self.catalogue.variants_by_id.values():
            if not v.is_unspecified_variant:
                vnorm = normalize_ingredient_text(v.canonical_name)
                if vnorm and _phrase_boundary_match(vnorm, norm):
                    key = (v.variant_id, vnorm)
                    if key not in seen_hit_keys:
                        seen_hit_keys.add(key)
                        phrase_hits.append((len(vnorm.split()), 3, vnorm, v))
                for al in v.aliases:
                    anorm = normalize_ingredient_text(al)
                    if anorm and _phrase_boundary_match(anorm, norm):
                        key = (v.variant_id, anorm)
                        if key not in seen_hit_keys:
                            seen_hit_keys.add(key)
                            phrase_hits.append((len(anorm.split()), 2, anorm, v))

        for fam in self.catalogue.families_by_id.values():
            unspec = fam.unspecified_variant
            if unspec is None:
                continue
            fnorm = normalize_ingredient_text(fam.canonical_name)
            if fnorm and _phrase_boundary_match(fnorm, search_space):
                key = (unspec.variant_id, fnorm)
                if key not in seen_hit_keys:
                    seen_hit_keys.add(key)
                    phrase_hits.append((len(fnorm.split()), 1, fnorm, unspec))
            for al in fam.aliases:
                anorm = normalize_ingredient_text(al)
                if anorm and _phrase_boundary_match(anorm, search_space):
                    key = (unspec.variant_id, anorm)
                    if key not in seen_hit_keys:
                        seen_hit_keys.add(key)
                        phrase_hits.append((len(anorm.split()), 1, anorm, unspec))

        if phrase_hits:
            # Prefer longest token span, then higher priority (variant name > variant alias > family alias), then char length
            phrase_hits.sort(key=lambda h: (h[0], h[1], len(h[2])), reverse=True)
            top_tok_len, top_prio, top_phrase, top_var = phrase_hits[0]
            # Check if another distinct variant tied at the exact same top specificity
            tied_variants: list[CatalogueVariant] = []
            seen_tied: set[str] = set()
            for tok_len, prio, phr, var in phrase_hits:
                if tok_len == top_tok_len and prio == top_prio and len(phr) == len(top_phrase):
                    if var.variant_id not in seen_tied:
                        seen_tied.add(var.variant_id)
                        tied_variants.append(var)
            if len(tied_variants) > 1:
                return self._build_unresolved(
                    orig=orig,
                    norm=norm,
                    reason=f"ambiguous_phrase:{top_phrase}",
                    suggestions=tied_variants,
                )
            source = "longest_specific_phrase" if not top_var.is_unspecified_variant else "family_alias"
            conf = Decimal("0.940") if not top_var.is_unspecified_variant else Decimal("0.910")
            return self._build_result(
                top_var,
                match_source=source,
                confidence=conf,
                requires_review=False,
                orig=orig,
                norm=norm,
                reason=f"{source}:{top_phrase}",
            )

        # 5. Fuzzy fallback -> suggestions ONLY, never auto-assign!
        fuzzy_suggestions: list[tuple[float, CatalogueVariant]] = []
        if search_space:
            for v in self.catalogue.variants_by_id.values():
                vnorm = normalize_ingredient_text(v.canonical_name)
                ratio = SequenceMatcher(None, search_space, vnorm).ratio()
                if ratio >= 0.72:
                    fuzzy_suggestions.append((ratio, v))
            fuzzy_suggestions.sort(key=lambda x: x[0], reverse=True)

        return self._build_unresolved(
            orig=orig,
            norm=norm,
            reason="unresolved_review",
            suggestions=[v for _, v in fuzzy_suggestions[:3]],
        )

    async def resolve_for_household(
        self,
        db: AsyncSession,
        *,
        household_id: UUID | None,
        receipt_text: str,
        canonical_item_id: UUID | None = None,
    ) -> IngredientResolutionResult:
        """Resolve a receipt description using DB confirmed mappings + household aliases + catalogue."""
        confirmed_variant: CatalogueVariant | None = None
        if canonical_item_id is not None:
            stmt = (
                select(IngredientMapping, CanonicalIngredient)
                .join(
                    CanonicalIngredient,
                    CanonicalIngredient.id == IngredientMapping.canonical_ingredient_id,
                )
                .where(
                    IngredientMapping.canonical_item_id == canonical_item_id,
                    IngredientMapping.user_confirmed.is_(True),
                    (IngredientMapping.household_id == household_id)
                    | (IngredientMapping.household_id.is_(None)),
                )
                .order_by(IngredientMapping.household_id.is_(None))
            )
            row = (await db.execute(stmt)).first()
            if row is not None:
                _, c_ing = row
                if c_ing.variant_id and c_ing.variant_id in self.catalogue.variants_by_id:
                    confirmed_variant = self.catalogue.variants_by_id[c_ing.variant_id]
                else:
                    confirmed_variant = self.catalogue.get_by_canonical_name(c_ing.name)
                if confirmed_variant is not None:
                    res = self._build_result(
                        confirmed_variant,
                        match_source="confirmed_product_mapping",
                        confidence=Decimal("1.000"),
                        requires_review=False,
                        orig=receipt_text,
                        norm=normalize_ingredient_text(receipt_text),
                        reason="confirmed_product_mapping",
                    )
                    res.canonical_ingredient_id = c_ing.id
                    return res

        # Load household-confirmed learned aliases
        household_aliases: dict[str, CatalogueVariant] = {}
        if household_id is not None:
            alias_rows = (
                await db.execute(
                    select(IngredientAlias, CanonicalIngredient)
                    .join(
                        CanonicalIngredient,
                        CanonicalIngredient.id == IngredientAlias.canonical_ingredient_id,
                    )
                    .where(
                        IngredientAlias.household_id == household_id,
                        IngredientAlias.is_approved.is_(True),
                    )
                )
            ).all()
            for al_obj, c_ing in alias_rows:
                target_v = (
                    self.catalogue.variants_by_id.get(al_obj.target_variant_id or "")
                    or (self.catalogue.variants_by_id.get(c_ing.variant_id or ""))
                    or self.catalogue.get_by_canonical_name(c_ing.name)
                )
                if target_v is not None:
                    household_aliases[normalize_ingredient_text(al_obj.normalized_alias)] = target_v

        res = self.resolve_text(receipt_text, household_aliases=household_aliases)
        if res.variant_id:
            db_ing = (
                await db.execute(
                    select(CanonicalIngredient).where(
                        CanonicalIngredient.variant_id == res.variant_id
                    )
                )
            ).scalar_one_or_none()
            if db_ing is None and res.variant_name:
                db_ing = (
                    await db.execute(
                        select(CanonicalIngredient).where(
                            CanonicalIngredient.normalized_name
                            == normalize_ingredient_text(res.variant_name).replace(" ", "_")
                        )
                    )
                ).scalar_one_or_none()
            if db_ing is not None:
                res.canonical_ingredient_id = db_ing.id
        return res

    @staticmethod
    def _build_result(
        variant: CatalogueVariant,
        *,
        match_source: str,
        confidence: Decimal,
        requires_review: bool,
        orig: str,
        norm: str,
        reason: str,
    ) -> IngredientResolutionResult:
        return IngredientResolutionResult(
            family_id=variant.family_id,
            family_name=variant.family_name,
            variant_id=variant.variant_id,
            variant_name=variant.canonical_name,
            variant_status=variant.variant_status,
            is_unspecified_variant=variant.is_unspecified_variant,
            category=variant.category,
            measurement=variant.measurement,
            match_source=match_source,
            confidence=confidence,
            requires_review=requires_review,
            original_receipt_text=orig,
            normalized_text=norm,
            reason=reason,
        )

    @staticmethod
    def _build_unresolved(
        *,
        orig: str,
        norm: str,
        reason: str,
        suggestions: list[CatalogueVariant],
    ) -> IngredientResolutionResult:
        return IngredientResolutionResult(
            family_id=None,
            family_name=None,
            variant_id=None,
            variant_name=None,
            variant_status=None,
            is_unspecified_variant=False,
            category=None,
            measurement=None,
            match_source="unresolved_review",
            confidence=Decimal("0.000"),
            requires_review=True,
            original_receipt_text=orig,
            normalized_text=norm,
            suggestions=[
                {
                    "family_id": v.family_id,
                    "variant_id": v.variant_id,
                    "variant_name": v.canonical_name,
                    "variant_status": v.variant_status,
                }
                for v in suggestions
            ],
            reason=reason,
        )
