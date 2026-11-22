from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

RequirementMode = Literal["exact_variant", "compatible_family", "generic_ingredient"]
RequirementType = Literal["required", "optional"]


class DishSubstitutionRule(BaseModel):
    """Validated substitution rule stored in dish_ingredients.substitutions_json."""

    original_variant_id: str | None = None
    substitute_variant_id: str | None = None
    substitute_family_id: str | None = None
    substitute_name: str = Field(min_length=1)
    verified: bool
    applicable_dish_slug: str | None = None
    conversion_ratio: float | None = Field(default=None, gt=0)
    conversion_unit: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> DishSubstitutionRule:
        if not self.substitute_variant_id and not self.substitute_family_id:
            raise ValueError(
                "At least one of substitute_variant_id or substitute_family_id must be set."
            )
        return self


class DishIngredientSpec(BaseModel):
    """Curated dish ingredient requirement definition."""

    ingredient_name: str = Field(min_length=1)
    requirement_mode: RequirementMode
    requirement_type: RequirementType = "required"
    variant_id: str | None = None
    family_id: str | None = None
    compatible_variant_ids: list[str] = Field(default_factory=list)
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str | None = None
    substitutions: list[DishSubstitutionRule] = Field(default_factory=list)
    sort_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_mode_fields(self) -> DishIngredientSpec:
        if self.requirement_mode == "exact_variant" and not self.variant_id:
            raise ValueError("exact_variant requirement requires variant_id.")
        if self.requirement_mode == "compatible_family" and not self.family_id:
            raise ValueError("compatible_family requirement requires family_id.")
        if (
            self.requirement_mode == "generic_ingredient"
            and not self.family_id
            and not self.variant_id
        ):
            raise ValueError(
                "generic_ingredient requirement requires family_id or variant_id."
            )
        return self


class DishCatalogueEntry(BaseModel):
    """Curated canonical Indian dish catalogue entry."""

    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    canonical_name: str = Field(min_length=1)
    alternative_names: list[str] = Field(default_factory=list)
    regional_names: dict[str, list[str]] = Field(default_factory=dict)
    cuisine: str = "Indian"
    region: str = "Pan-Indian"
    meal_types: list[str] = Field(default_factory=list)
    diet_tags: list[str] = Field(default_factory=list)
    description: str = ""
    prep_minutes: int | None = Field(default=None, ge=0)
    cook_minutes: int | None = Field(default=None, ge=0)
    default_servings: int = Field(default=2, ge=1)
    search_terms: list[str] = Field(default_factory=list)
    catalogue_version: str = "1.0.0"
    ingredients: list[DishIngredientSpec] = Field(min_length=1)


@dataclass(frozen=True)
class CatalogueSeedResult:
    """Summary of an idempotent transactional dish catalogue seed run."""

    inserted: int
    updated: int
    unchanged: int
    deactivated: int


@dataclass(frozen=True)
class RefreshLeaseResult:
    """Result of attempting to acquire a distributed dish_video_cache refresh lease."""

    acquired: bool
    cache_hit: bool
    in_cooldown: bool
    lock_token: UUID | None
    video_record_ids: list[str]


@dataclass(frozen=True)
class QuotaReservationResult:
    """Result of an atomic YouTube quota reservation attempt."""

    reserved: bool
    quota_date: date
    quota_project_key: str
    units_used: int
    daily_budget: int
