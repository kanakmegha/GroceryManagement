"""Idempotent, reversible CanonicalIngredient merge service (Phase 4A).

Preserves all CanonicalItem identities, IngredientMappings, and inventory quantities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.catalog.ingredient_identity_service import are_ingredients_incompatible
from app.modules.inventory.models import CanonicalIngredient, IngredientAlias, IngredientMapping


@dataclass(frozen=True)
class IngredientMergeDryRun:
    survivor_id: UUID
    survivor_name: str
    duplicate_id: UUID
    duplicate_name: str
    compatible: bool
    mappings_to_reassign: int
    mappings_to_deduplicate: int
    aliases_to_reassign: int


async def dry_run_ingredient_merge(
    db: AsyncSession,
    *,
    survivor_id: UUID,
    duplicate_id: UUID,
) -> IngredientMergeDryRun:
    """Read-only dry-run report before merging two CanonicalIngredients."""
    if survivor_id == duplicate_id:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="Cannot merge ingredient into itself.")
    survivor = await db.get(CanonicalIngredient, survivor_id)
    duplicate = await db.get(CanonicalIngredient, duplicate_id)
    if survivor is None or duplicate is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="CanonicalIngredient not found.")
    if are_ingredients_incompatible(survivor.normalized_name, duplicate.normalized_name):
        raise AppError(
            status_code=409,
            code="INCOMPATIBLE_INGREDIENTS",
            message=f"Incompatible ingredients: {duplicate.name} != {survivor.name}.",
        )

    dup_maps = list(
        (
            await db.execute(
                select(IngredientMapping).where(
                    IngredientMapping.canonical_ingredient_id == duplicate_id
                )
            )
        )
        .scalars()
        .all()
    )
    surv_keys = {
        (m.household_id, m.canonical_item_id)
        for m in (
            await db.execute(
                select(IngredientMapping).where(
                    IngredientMapping.canonical_ingredient_id == survivor_id
                )
            )
        )
        .scalars()
        .all()
    }
    dedup_cnt = sum(1 for m in dup_maps if (m.household_id, m.canonical_item_id) in surv_keys)
    al_cnt = int(
        (
            await db.execute(
                select(func.count(IngredientAlias.id)).where(
                    IngredientAlias.canonical_ingredient_id == duplicate_id
                )
            )
        ).scalar_one()
    )
    return IngredientMergeDryRun(
        survivor_id=survivor_id,
        survivor_name=survivor.name,
        duplicate_id=duplicate_id,
        duplicate_name=duplicate.name,
        compatible=True,
        mappings_to_reassign=len(dup_maps) - dedup_cnt,
        mappings_to_deduplicate=dedup_cnt,
        aliases_to_reassign=al_cnt,
    )


@dataclass
class IngredientMergeRecord:
    survivor_id: UUID
    duplicate_id: UUID
    reassigned_mapping_ids: list[UUID] = field(default_factory=list)
    reassigned_alias_ids: list[UUID] = field(default_factory=list)
    deleted_redundant_mapping_ids: list[UUID] = field(default_factory=list)
    reversed: bool = False


async def merge_canonical_ingredients(
    db: AsyncSession,
    *,
    survivor_id: UUID,
    duplicate_id: UUID,
) -> IngredientMergeRecord:
    """Soft-merge duplicate CanonicalIngredient into survivor_id without touching CanonicalItems or lots."""
    if survivor_id == duplicate_id:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="Cannot merge ingredient into itself.")

    survivor = await db.get(CanonicalIngredient, survivor_id)
    duplicate = await db.get(CanonicalIngredient, duplicate_id)
    if survivor is None or duplicate is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="CanonicalIngredient not found.")
    if not survivor.is_active:
        raise AppError(status_code=409, code="ALREADY_MERGED", message="Survivor ingredient is inactive.")
    if not duplicate.is_active:
        raise AppError(status_code=409, code="ALREADY_MERGED", message="Duplicate ingredient was already merged.")
    if are_ingredients_incompatible(survivor.normalized_name, duplicate.normalized_name):
        raise AppError(
            status_code=409,
            code="INCOMPATIBLE_INGREDIENTS",
            message=(
                f"Cannot merge incompatible ingredients: "
                f"{duplicate.name} ({duplicate.normalized_name}) != "
                f"{survivor.name} ({survivor.normalized_name})."
            ),
        )

    dup_mappings = list(
        (
            await db.execute(
                select(IngredientMapping).where(
                    IngredientMapping.canonical_ingredient_id == duplicate_id
                )
            )
        )
        .scalars()
        .all()
    )

    reassigned: list[UUID] = []
    for dm in dup_mappings:
        # Check if survivor already has a mapping for the same (household_id, canonical_item_id)
        if dm.household_id is None:
            stmt = select(IngredientMapping).where(
                IngredientMapping.household_id.is_(None),
                IngredientMapping.canonical_item_id == dm.canonical_item_id,
                IngredientMapping.canonical_ingredient_id == survivor_id,
            )
        else:
            stmt = select(IngredientMapping).where(
                IngredientMapping.household_id == dm.household_id,
                IngredientMapping.canonical_item_id == dm.canonical_item_id,
                IngredientMapping.canonical_ingredient_id == survivor_id,
            )
        existing_surv = (await db.execute(stmt)).scalar_one_or_none()
        if existing_surv is not None:
            if dm.user_confirmed and not existing_surv.user_confirmed:
                existing_surv.user_confirmed = True
                existing_surv.mapping_source = dm.mapping_source
            await db.delete(dm)
        else:
            dm.canonical_ingredient_id = survivor_id
            reassigned.append(dm.id)

    dup_aliases = list(
        (
            await db.execute(
                select(IngredientAlias).where(
                    IngredientAlias.canonical_ingredient_id == duplicate_id
                )
            )
        )
        .scalars()
        .all()
    )
    reassigned_aliases: list[UUID] = []
    for da in dup_aliases:
        da.canonical_ingredient_id = survivor_id
        reassigned_aliases.append(da.id)

    duplicate.is_active = False
    await db.flush()
    return IngredientMergeRecord(
        survivor_id=survivor_id,
        duplicate_id=duplicate_id,
        reassigned_mapping_ids=reassigned,
        reassigned_alias_ids=reassigned_aliases,
    )


async def rollback_canonical_ingredient_merge(
    db: AsyncSession,
    *,
    record: IngredientMergeRecord,
) -> None:
    """Restore a soft-merged CanonicalIngredient, its IngredientMappings, and IngredientAliases."""
    if record.reversed:
        raise AppError(status_code=409, code="ALREADY_REVERSED", message="Merge already reversed.")
    duplicate = await db.get(CanonicalIngredient, record.duplicate_id)
    if duplicate is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Duplicate ingredient not found.")

    for mid in record.reassigned_mapping_ids:
        m = await db.get(IngredientMapping, mid)
        if m is not None:
            m.canonical_ingredient_id = record.duplicate_id

    for aid in record.reassigned_alias_ids:
        a = await db.get(IngredientAlias, aid)
        if a is not None:
            a.canonical_ingredient_id = record.duplicate_id

    duplicate.is_active = True
    record.reversed = True
    await db.flush()
