"""Curated Indian Household Ingredient Catalogue loader, cache, and validator (Phase 7.2).

Single source of truth backed by:
`backend/app/modules/inventory/data/indian_household_ingredient_catalogue.json`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

CATALOGUE_JSON_PATH = (
    Path(__file__).resolve().parent.parent
    / "inventory"
    / "data"
    / "indian_household_ingredient_catalogue.json"
)

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize_ingredient_text(text: str | None) -> str:
    """Apply Unicode NFKC, casefold, punctuation normalization, and whitespace collapsing."""
    if not text:
        return ""
    nfkc = unicodedata.normalize("NFKC", str(text))
    folded = nfkc.casefold()
    no_punct = _PUNCT_RE.sub(" ", folded.replace("_", " ").replace("-", " "))
    return _SPACE_RE.sub(" ", no_punct).strip()


@dataclass(frozen=True)
class CatalogueVariant:
    variant_id: str
    family_id: str
    family_name: str
    canonical_name: str
    variant_status: str
    is_unspecified_variant: bool
    category: str
    aliases: tuple[str, ...]
    attributes: dict[str, Any]
    measurement: dict[str, Any]
    allowed_recipe_substitutions: tuple[str, ...]
    learning: dict[str, Any]


@dataclass(frozen=True)
class CatalogueFamily:
    family_id: str
    canonical_name: str
    category: str
    aliases: tuple[str, ...]
    measurement: dict[str, Any]
    variants: tuple[CatalogueVariant, ...]
    unspecified_variant: CatalogueVariant | None
    learning: dict[str, Any]


@dataclass
class CatalogueValidationResult:
    valid: bool
    family_count: int
    variant_count: int
    curated_alias_count: int
    declared_family_count: int
    declared_variant_count: int
    declared_alias_count: int
    errors: list[str] = field(default_factory=list)
    alias_collisions: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class IngredientCatalogue:
    dataset_id: str
    dataset_name: str
    schema_version: str
    catalogue_version: str
    categories: dict[str, str]
    matching_policy: dict[str, Any]
    measurement_policy: dict[str, Any]
    learning_policy: dict[str, Any]
    ui_policy: dict[str, Any]
    validation_meta: dict[str, Any]
    families_by_id: dict[str, CatalogueFamily]
    variants_by_id: dict[str, CatalogueVariant]
    variants_by_norm_name: dict[str, list[CatalogueVariant]]
    families_by_norm_name: dict[str, list[CatalogueFamily]]
    variant_aliases: dict[str, list[CatalogueVariant]]
    family_aliases: dict[str, list[CatalogueFamily]]
    validation: CatalogueValidationResult

    def get_family(self, family_id: str) -> CatalogueFamily | None:
        return self.families_by_id.get(family_id)

    def get_variant(self, variant_id: str) -> CatalogueVariant | None:
        return self.variants_by_id.get(variant_id)

    def get_by_canonical_name(self, name: str) -> CatalogueVariant | None:
        norm = normalize_ingredient_text(name)
        matches = self.variants_by_norm_name.get(norm)
        if matches:
            return matches[0]
        fams = self.families_by_norm_name.get(norm)
        if fams and fams[0].unspecified_variant is not None:
            return fams[0].unspecified_variant
        return None

    def lookup_by_alias(self, alias: str) -> list[CatalogueVariant]:
        norm = normalize_ingredient_text(alias)
        out: list[CatalogueVariant] = []
        seen: set[str] = set()
        for v in self.variant_aliases.get(norm, []):
            if v.variant_id not in seen:
                seen.add(v.variant_id)
                out.append(v)
        for fam in self.family_aliases.get(norm, []):
            v = fam.unspecified_variant
            if v is not None and v.variant_id not in seen:
                seen.add(v.variant_id)
                out.append(v)
        return out

    def get_measurement_policy(self) -> dict[str, Any]:
        return self.measurement_policy

    def get_categories(self) -> dict[str, str]:
        return self.categories

    def get_measurement_rules_for_variant(self, variant_id: str) -> dict[str, Any] | None:
        v = self.variants_by_id.get(variant_id)
        return v.measurement if v is not None else None


def validate_catalogue_dict(raw: dict[str, Any]) -> CatalogueValidationResult:
    required_top = (
        "dataset_id",
        "dataset_name",
        "schema_version",
        "catalogue_version",
        "categories",
        "ingredient_families",
        "matching_policy",
        "measurement_policy",
        "learning_policy",
        "ui_policy",
        "validation",
    )
    errors: list[str] = []
    for k in required_top:
        if k not in raw:
            errors.append(f"Missing top-level field: {k}")

    cat_ids = {str(c.get("id") or "") for c in (raw.get("categories") or []) if c.get("id")}
    meas_policy = raw.get("measurement_policy") or {}
    dims = meas_policy.get("dimensions") or {}
    supported_dims = set(dims.keys())
    supported_units = set(meas_policy.get("unit_aliases", {}).keys()) | set(
        meas_policy.get("package_units") or []
    )
    for d in dims.values():
        for u in d.get("units") or []:
            supported_units.add(u)

    seen_fams: set[str] = set()
    seen_vars: set[str] = set()
    alias_owners: dict[str, list[str]] = {}
    alias_count = 0

    families = raw.get("ingredient_families") or []
    for fam in families:
        fid = str(fam.get("id") or "").strip()
        fname = str(fam.get("canonical_name") or "").strip()
        fcat = str(fam.get("category") or "").strip()
        if not fid or not fname:
            errors.append(f"Family missing id or canonical_name: {fid!r}")
        if fid in seen_fams:
            errors.append(f"Duplicate family id: {fid}")
        seen_fams.add(fid)
        if fcat not in cat_ids:
            errors.append(f"Family {fid} references unknown category {fcat!r}")
        if "learning" not in fam:
            errors.append(f"Family {fid} missing learning metadata")

        fmeas = fam.get("measurement") or {}
        if fmeas.get("default_dimension") not in supported_dims:
            errors.append(f"Family {fid} has unsupported dimension {fmeas.get('default_dimension')!r}")
        for u in fmeas.get("supported_units") or []:
            if u not in supported_units:
                errors.append(f"Family {fid} has unsupported unit {u!r}")

        for a in fam.get("aliases") or []:
            alias_count += 1
            na = normalize_ingredient_text(a)
            if na:
                alias_owners.setdefault(na, []).append(f"family:{fid}")

        unspec_cnt = 0
        for var in fam.get("variants") or []:
            vid = str(var.get("id") or "").strip()
            vname = str(var.get("canonical_name") or "").strip()
            if not vid or not vname:
                errors.append(f"Variant in {fid} missing id or canonical_name")
            if vid in seen_vars:
                errors.append(f"Duplicate variant id: {vid}")
            seen_vars.add(vid)
            if not vid.startswith(f"{fid}__"):
                errors.append(f"Variant {vid} does not belong to family {fid}")
            if var.get("is_unspecified_variant"):
                unspec_cnt += 1
                if var.get("variant_status") != "unspecified":
                    errors.append(f"Unspecified variant {vid} has status {var.get('variant_status')!r}")
            if "learning" not in var or "learned_aliases" not in (var.get("learning") or {}):
                errors.append(f"Variant {vid} missing learning.learned_aliases")
            for a in var.get("aliases") or []:
                alias_count += 1
                na = normalize_ingredient_text(a)
                if na:
                    alias_owners.setdefault(na, []).append(f"variant:{vid}")

        if unspec_cnt != 1:
            errors.append(f"Family {fid} has {unspec_cnt} unspecified variants (expected 1)")

    collisions = {k: sorted(set(v)) for k, v in alias_owners.items() if len(set(v)) > 1}
    val_meta = raw.get("validation") or {}
    dec_fams = int(val_meta.get("family_count") or 0)
    dec_vars = int(val_meta.get("variant_count_including_unspecified") or 0)
    dec_aliases = int(val_meta.get("curated_alias_count") or 0)
    if dec_fams and len(families) != dec_fams:
        errors.append(f"Family count {len(families)} != declared {dec_fams}")
    if dec_vars and len(seen_vars) != dec_vars:
        errors.append(f"Variant count {len(seen_vars)} != declared {dec_vars}")
    if dec_aliases and alias_count != dec_aliases:
        errors.append(f"Alias count {alias_count} != declared {dec_aliases}")

    return CatalogueValidationResult(
        valid=len(errors) == 0,
        family_count=len(families),
        variant_count=len(seen_vars),
        curated_alias_count=alias_count,
        declared_family_count=dec_fams,
        declared_variant_count=dec_vars,
        declared_alias_count=dec_aliases,
        errors=errors,
        alias_collisions=collisions,
    )


@lru_cache(maxsize=1)
def get_ingredient_catalogue() -> IngredientCatalogue:
    """Load, validate, and cache the curated Indian Household Ingredient Catalogue."""
    raw = json.loads(CATALOGUE_JSON_PATH.read_text(encoding="utf-8"))
    validation = validate_catalogue_dict(raw)

    categories = {
        str(c["id"]): str(c["name"])
        for c in (raw.get("categories") or [])
        if c.get("id") and c.get("name")
    }
    families_by_id: dict[str, CatalogueFamily] = {}
    variants_by_id: dict[str, CatalogueVariant] = {}
    variants_by_norm_name: dict[str, list[CatalogueVariant]] = {}
    families_by_norm_name: dict[str, list[CatalogueFamily]] = {}
    variant_aliases: dict[str, list[CatalogueVariant]] = {}
    family_aliases: dict[str, list[CatalogueFamily]] = {}

    for fam in raw.get("ingredient_families") or []:
        fid = str(fam["id"])
        fname = str(fam["canonical_name"])
        fcat = str(fam["category"])
        fmeas = dict(fam.get("measurement") or {})
        built_vars: list[CatalogueVariant] = []
        unspec_var: CatalogueVariant | None = None

        for var in fam.get("variants") or []:
            vid = str(var["id"])
            vname = str(var["canonical_name"])
            vmeas = dict(var.get("measurement_override") or fmeas)
            cv = CatalogueVariant(
                variant_id=vid,
                family_id=fid,
                family_name=fname,
                canonical_name=vname,
                variant_status=str(var.get("variant_status") or "specified"),
                is_unspecified_variant=bool(var.get("is_unspecified_variant", False)),
                category=fcat,
                aliases=tuple(str(a) for a in (var.get("aliases") or [])),
                attributes=dict(var.get("attributes") or {}),
                measurement=vmeas,
                allowed_recipe_substitutions=tuple(
                    str(s) for s in (var.get("allowed_recipe_substitutions") or [])
                ),
                learning=dict(var.get("learning") or {}),
            )
            built_vars.append(cv)
            variants_by_id[vid] = cv
            if cv.is_unspecified_variant:
                unspec_var = cv
            vnorm = normalize_ingredient_text(vname)
            if vnorm:
                variants_by_norm_name.setdefault(vnorm, []).append(cv)
            for a in cv.aliases:
                anorm = normalize_ingredient_text(a)
                if anorm:
                    variant_aliases.setdefault(anorm, []).append(cv)

        cf = CatalogueFamily(
            family_id=fid,
            canonical_name=fname,
            category=fcat,
            aliases=tuple(str(a) for a in (fam.get("aliases") or [])),
            measurement=fmeas,
            variants=tuple(built_vars),
            unspecified_variant=unspec_var,
            learning=dict(fam.get("learning") or {}),
        )
        families_by_id[fid] = cf
        fnorm = normalize_ingredient_text(fname)
        if fnorm:
            families_by_norm_name.setdefault(fnorm, []).append(cf)
        for a in cf.aliases:
            anorm = normalize_ingredient_text(a)
            if anorm:
                family_aliases.setdefault(anorm, []).append(cf)

    return IngredientCatalogue(
        dataset_id=str(raw["dataset_id"]),
        dataset_name=str(raw["dataset_name"]),
        schema_version=str(raw["schema_version"]),
        catalogue_version=str(raw["catalogue_version"]),
        categories=categories,
        matching_policy=dict(raw.get("matching_policy") or {}),
        measurement_policy=dict(raw.get("measurement_policy") or {}),
        learning_policy=dict(raw.get("learning_policy") or {}),
        ui_policy=dict(raw.get("ui_policy") or {}),
        validation_meta=dict(raw.get("validation") or {}),
        families_by_id=families_by_id,
        variants_by_id=variants_by_id,
        variants_by_norm_name=variants_by_norm_name,
        families_by_norm_name=families_by_norm_name,
        variant_aliases=variant_aliases,
        family_aliases=family_aliases,
        validation=validation,
    )
