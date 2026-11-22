"""Read-only deterministic pantry-based dish matching and ranking service (Phase 2)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.discovery.catalogue_seed import is_verified_substitution
from app.modules.discovery.models import Dish, DishFeedback, DishIngredient, SavedDish
from app.modules.discovery.schemas import (
    DiscoveryBucket,
    DishMatchOut,
    DishMatchResponse,
    IngredientMatchOut,
    MatchedSubstitutionOut,
    MatchReason,
    MatchStatus,
)
from app.modules.inventory.ingredient_aggregation_service import (
    convert_to_target_unit,
    get_ingredient_inventory_summary,
)
from app.modules.inventory.models import CanonicalIngredient
from app.modules.inventory.schemas import IngredientSummaryOut
from app.modules.recipes.models import HouseholdRecipePreference

ALMOST_THERE_MAX_MISSING = 3
EXPIRING_SOON_DAYS = 5
DEFAULT_HOUSEHOLD_TIMEZONE = "Asia/Kolkata"


@dataclass(frozen=True)
class _PantryCandidate:
    canonical_ingredient_id: UUID
    variant_id: str | None
    family_id: str | None
    is_unspecified_variant: bool
    summary: IngredientSummaryOut


@dataclass(frozen=True)
class _EvalCandidate:
    status: MatchStatus
    candidate: _PantryCandidate | None
    available_qty: Decimal | None
    available_unit: str | None
    reason: MatchReason | None
    coverage_ratio: Decimal


class _PantryIndex:
    """In-memory index of active, in-stock household pantry ingredients."""

    def __init__(self) -> None:
        self.by_canonical_id: dict[UUID, _PantryCandidate] = {}
        self.by_variant_id: dict[str, _PantryCandidate] = {}
        self.by_family_id: dict[str, list[_PantryCandidate]] = defaultdict(list)

    def add(self, entry: _PantryCandidate) -> None:
        self.by_canonical_id[entry.canonical_ingredient_id] = entry
        if entry.variant_id:
            self.by_variant_id[entry.variant_id] = entry
        if entry.family_id:
            self.by_family_id[entry.family_id].append(entry)


def _household_today(prefs: HouseholdRecipePreference | None) -> date:
    tz_name = (prefs.timezone if prefs and prefs.timezone else None) or DEFAULT_HOUSEHOLD_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_HOUSEHOLD_TIMEZONE)
    return datetime.now(tz).date()


def _has_positive_stock(summary: IngredientSummaryOut) -> bool:
    if summary.lot_count <= 0:
        return False
    return any(g.total_qty > Decimal("0") for g in summary.unit_groups)


def _first_positive_group(summary: IngredientSummaryOut) -> tuple[Decimal | None, str | None]:
    for group in summary.unit_groups:
        if group.total_qty > Decimal("0"):
            return group.total_qty, group.unit
    return None, None


def _is_expiring_soon(summary: IngredientSummaryOut, ref_today: date) -> bool:
    if summary.earliest_expires_on is None:
        return False
    return summary.earliest_expires_on <= ref_today + timedelta(days=EXPIRING_SOON_DAYS)


def _select_candidates_for_requirement(
    req: DishIngredient,
    pantry: _PantryIndex,
) -> list[_PantryCandidate]:
    """Select eligible pantry candidates strictly by explicit requirement_mode."""
    if req.requirement_mode == "exact_variant":
        if not req.variant_id:
            return []
        cand = pantry.by_variant_id.get(req.variant_id)
        if cand is None or cand.is_unspecified_variant:
            return []
        return [cand]

    if req.requirement_mode == "compatible_family":
        if not req.family_id:
            return []
        family_cands = pantry.by_family_id.get(req.family_id, [])
        if req.compatible_variant_ids:
            allowed = set(req.compatible_variant_ids)
            return [c for c in family_cands if c.variant_id in allowed]
        return list(family_cands)

    # generic_ingredient
    seen: set[UUID] = set()
    out: list[_PantryCandidate] = []
    if req.canonical_ingredient_id is not None:
        direct = pantry.by_canonical_id.get(req.canonical_ingredient_id)
        if direct is not None:
            seen.add(direct.canonical_ingredient_id)
            out.append(direct)
    if req.family_id:
        for cand in pantry.by_family_id.get(req.family_id, []):
            if cand.canonical_ingredient_id not in seen:
                seen.add(cand.canonical_ingredient_id)
                out.append(cand)
    return out


def _evaluate_candidate_quantity(
    cand: _PantryCandidate,
    req_qty: Decimal | None,
    req_unit: str | None,
) -> _EvalCandidate:
    """Evaluate one pantry candidate independently (never sums across variants)."""
    if req_qty is None:
        avail_qty, avail_unit = _first_positive_group(cand.summary)
        return _EvalCandidate(
            status="available",
            candidate=cand,
            available_qty=avail_qty,
            available_unit=avail_unit,
            reason=None,
            coverage_ratio=Decimal("1"),
        )

    comparable_total = Decimal("0")
    has_comparable = False
    for group in cand.summary.unit_groups:
        if group.total_qty <= Decimal("0"):
            continue
        converted, _ = convert_to_target_unit(group.total_qty, group.unit, req_unit)
        if converted is not None:
            has_comparable = True
            comparable_total += converted

    if has_comparable:
        if comparable_total >= req_qty:
            return _EvalCandidate(
                status="available",
                candidate=cand,
                available_qty=comparable_total.quantize(Decimal("0.001")),
                available_unit=req_unit,
                reason=None,
                coverage_ratio=Decimal("1"),
            )
        ratio = (comparable_total / req_qty) if req_qty > Decimal("0") else Decimal("0")
        return _EvalCandidate(
            status="partial",
            candidate=cand,
            available_qty=comparable_total.quantize(Decimal("0.001")),
            available_unit=req_unit,
            reason="INSUFFICIENT_QUANTITY",
            coverage_ratio=ratio,
        )

    raw_qty, raw_unit = _first_positive_group(cand.summary)
    return _EvalCandidate(
        status="partial",
        candidate=cand,
        available_qty=raw_qty,
        available_unit=raw_unit,
        reason="INCOMPARABLE_UNIT",
        coverage_ratio=Decimal("0"),
    )


def _pick_best_evaluation(
    candidates: list[_PantryCandidate],
    req_qty: Decimal | None,
    req_unit: str | None,
    ref_today: date,
) -> _EvalCandidate | None:
    if not candidates:
        return None
    evals = [_evaluate_candidate_quantity(c, req_qty, req_unit) for c in candidates]
    status_rank = {"available": 2, "partial": 1, "missing": 0}
    reason_rank = {"INSUFFICIENT_QUANTITY": 1, "INCOMPARABLE_UNIT": 0}
    return max(
        evals,
        key=lambda ev: (
            status_rank[ev.status],
            ev.coverage_ratio,
            reason_rank.get(ev.reason or "", 2),
            1 if (ev.candidate and _is_expiring_soon(ev.candidate.summary, ref_today)) else 0,
        ),
    )


def _select_substitution_candidates(
    sub: dict[str, Any],
    pantry: _PantryIndex,
) -> list[_PantryCandidate]:
    sub_vid = sub.get("substitute_variant_id")
    sub_fid = sub.get("substitute_family_id")
    if sub_vid:
        cand = pantry.by_variant_id.get(str(sub_vid))
        return [cand] if cand is not None else []
    if sub_fid:
        return list(pantry.by_family_id.get(str(sub_fid), []))
    return []


def _try_verified_substitutions(
    req: DishIngredient,
    dish_slug: str,
    pantry: _PantryIndex,
    ref_today: date,
) -> tuple[_EvalCandidate, MatchedSubstitutionOut] | None:
    """Try verified substitutions; never assume 1:1 when ratio/unit is absent."""
    for sub in req.substitutions_json:
        if not is_verified_substitution(sub):
            continue
        applicable_slug = sub.get("applicable_dish_slug")
        if applicable_slug is not None and applicable_slug != dish_slug:
            continue

        cands = _select_substitution_candidates(sub, pantry)
        if not cands:
            continue

        sub_req_qty: Decimal | None = None
        sub_req_unit: str | None = None
        if req.quantity is not None:
            raw_ratio = sub.get("conversion_ratio")
            raw_unit = sub.get("conversion_unit")
            if raw_ratio is None or not raw_unit:
                # Cannot safely evaluate quantity without explicit ratio & unit
                continue
            sub_req_qty = (req.quantity * Decimal(str(raw_ratio))).quantize(Decimal("0.001"))
            sub_req_unit = str(raw_unit)

        best = _pick_best_evaluation(cands, sub_req_qty, sub_req_unit, ref_today)
        if best is not None and best.status == "available":
            ratio_dec = (
                Decimal(str(sub["conversion_ratio"]))
                if sub.get("conversion_ratio") is not None
                else None
            )
            sub_out = MatchedSubstitutionOut(
                substitute_name=str(sub.get("substitute_name", "")),
                substitute_variant_id=sub.get("substitute_variant_id"),
                substitute_family_id=sub.get("substitute_family_id"),
                conversion_ratio=ratio_dec,
                conversion_unit=sub.get("conversion_unit"),
                notes=sub.get("notes"),
            )
            return best, sub_out

    return None


def _is_diet_compatible(dish: Dish, prefs: HouseholdRecipePreference | None) -> bool:
    if prefs is None:
        return True
    tags = {t.strip().lower() for t in dish.diet_tags}
    diet = (prefs.diet_type or "any").strip().lower()

    if diet == "vegan" and "vegan" not in tags:
        return False
    if diet == "vegetarian" and not ({"vegetarian", "vegan"} & tags):
        return False
    if diet == "eggetarian" and not ({"vegetarian", "vegan", "eggetarian"} & tags):
        return False

    for restriction in prefs.dietary_restrictions or []:
        norm = restriction.strip().lower()
        if norm and norm not in tags:
            return False
    return True


def _classify_bucket(missing_or_partial: int) -> DiscoveryBucket:
    if missing_or_partial == 0:
        return "can_make_now"
    if missing_or_partial <= ALMOST_THERE_MAX_MISSING:
        return "almost_there"
    return "explore_more"


def _ranking_key(
    dish_out: DishMatchOut,
    prefs: HouseholdRecipePreference | None,
) -> tuple[Any, ...]:
    bucket_rank = {"can_make_now": 2, "almost_there": 1, "explore_more": 0}[dish_out.bucket]
    missing_or_partial = dish_out.required_missing + dish_out.required_partial
    ratio = (
        Decimal(dish_out.required_available) / Decimal(dish_out.required_total)
        if dish_out.required_total > 0
        else Decimal("0")
    )

    feedback_score = (
        1
        if dish_out.user_feedback_action == "like"
        else (-1 if dish_out.user_feedback_action == "dislike" else 0)
    )
    saved_score = 1 if dish_out.is_saved else 0

    cuisine_match = 0
    meal_match = 0
    prep_ok = 0
    if prefs is not None:
        pref_cuisines = {c.strip().lower() for c in (prefs.preferred_cuisines or [])}
        if pref_cuisines and (
            dish_out.cuisine.lower() in pref_cuisines or dish_out.region.lower() in pref_cuisines
        ):
            cuisine_match = 1
        pref_meals = {m.strip().lower() for m in (prefs.preferred_meal_types or [])}
        if pref_meals and ({m.lower() for m in dish_out.meal_types} & pref_meals):
            meal_match = 1
        if (
            prefs.max_prep_minutes is not None
            and dish_out.prep_minutes is not None
            and dish_out.prep_minutes <= prefs.max_prep_minutes
        ):
            prep_ok = 1

    return (
        -bucket_rank,
        missing_or_partial,
        -ratio,
        -len(dish_out.expiring_matches),
        -feedback_score,
        -saved_score,
        -cuisine_match,
        -meal_match,
        -prep_ok,
        dish_out.canonical_name.lower(),
        str(dish_out.dish_id),
    )


async def _load_pantry_index(
    db: AsyncSession,
    household_id: UUID,
    ref_today: date,
) -> _PantryIndex:
    summary_resp = await get_ingredient_inventory_summary(db, household_id, today=ref_today)
    in_stock = [s for s in summary_resp.ingredients if _has_positive_stock(s)]
    if not in_stock:
        return _PantryIndex()

    ing_ids = [s.canonical_ingredient_id for s in in_stock]
    canon_rows = (
        await db.execute(
            select(
                CanonicalIngredient.id,
                CanonicalIngredient.variant_id,
                CanonicalIngredient.family_id,
                CanonicalIngredient.is_unspecified_variant,
            ).where(CanonicalIngredient.id.in_(ing_ids))
        )
    ).all()
    meta_by_id = {row[0]: (row[1], row[2], bool(row[3])) for row in canon_rows}

    index = _PantryIndex()
    for item_summary in in_stock:
        variant_id, family_id, is_unspecified = meta_by_id.get(
            item_summary.canonical_ingredient_id, (None, None, False)
        )
        index.add(
            _PantryCandidate(
                canonical_ingredient_id=item_summary.canonical_ingredient_id,
                variant_id=variant_id,
                family_id=family_id,
                is_unspecified_variant=is_unspecified,
                summary=item_summary,
            )
        )
    return index


async def match_dishes_for_household(
    db: AsyncSession,
    household_id: UUID,
    include_hidden: bool = False,
) -> DishMatchResponse:
    """
    Read-only deterministic pantry-based dish matching and ranking for a household.

    Reads inventory exclusively from `get_ingredient_inventory_summary` and never
    mutates inventory, cooking plans, ledger entries, or discovery user state.
    """
    prefs = await db.get(HouseholdRecipePreference, household_id)
    ref_today = _household_today(prefs)
    pantry = await _load_pantry_index(db, household_id, ref_today)

    dishes = (
        (
            await db.execute(
                select(Dish)
                .options(selectinload(Dish.ingredients))
                .where(Dish.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )

    saved_dish_ids = set(
        (
            await db.execute(
                select(SavedDish.dish_id).where(SavedDish.household_id == household_id)
            )
        )
        .scalars()
        .all()
    )

    feedback_rows = (
        (
            await db.execute(
                select(DishFeedback)
                .where(DishFeedback.household_id == household_id)
                .order_by(DishFeedback.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    latest_feedback_by_dish: dict[UUID, str] = {}
    hidden_dish_ids: set[UUID] = set()
    for fb in feedback_rows:
        if fb.action == "hide":
            hidden_dish_ids.add(fb.dish_id)
        else:
            latest_feedback_by_dish[fb.dish_id] = fb.action

    matched_dishes: list[DishMatchOut] = []
    for dish in dishes:
        if not include_hidden and dish.id in hidden_dish_ids:
            continue
        if not _is_diet_compatible(dish, prefs):
            continue

        req_outs: list[IngredientMatchOut] = []
        required_total = 0
        required_available = 0
        required_partial = 0
        required_missing = 0
        missing_names: list[str] = []
        expiring_matches: list[str] = []

        for req in dish.ingredients:
            candidates = _select_candidates_for_requirement(req, pantry)
            best = _pick_best_evaluation(candidates, req.quantity, req.unit, ref_today)

            matched_via = None
            matched_sub: MatchedSubstitutionOut | None = None
            final_eval = best

            if final_eval is None or final_eval.status != "available":
                sub_hit = _try_verified_substitutions(req, dish.slug, pantry, ref_today)
                if sub_hit is not None:
                    final_eval, matched_sub = sub_hit
                    matched_via = "verified_substitution"

            if final_eval is None:
                status: MatchStatus = "missing"
                reason: MatchReason | None = "MISSING_IN_PANTRY"
                cand = None
                avail_qty = None
                avail_unit = None
            else:
                status = final_eval.status
                reason = final_eval.reason
                cand = final_eval.candidate
                avail_qty = final_eval.available_qty
                avail_unit = final_eval.available_unit
                if matched_via is None:
                    matched_via = "primary"

            exp_soon = cand is not None and _is_expiring_soon(cand.summary, ref_today)
            earliest_exp = cand.summary.earliest_expires_on if cand is not None else None

            if req.requirement_type == "required":
                required_total += 1
                if status == "available":
                    required_available += 1
                    if exp_soon:
                        expiring_matches.append(req.ingredient_name)
                elif status == "partial":
                    required_partial += 1
                    missing_names.append(req.ingredient_name)
                else:
                    required_missing += 1
                    missing_names.append(req.ingredient_name)

            req_outs.append(
                IngredientMatchOut(
                    dish_ingredient_id=req.id,
                    ingredient_name=req.ingredient_name,
                    requirement_mode=req.requirement_mode,  # type: ignore[arg-type]
                    requirement_type=req.requirement_type,  # type: ignore[arg-type]
                    required_variant_id=req.variant_id,
                    required_family_id=req.family_id,
                    status=status,
                    matched_via=matched_via,
                    matched_canonical_ingredient_id=(
                        cand.canonical_ingredient_id if cand is not None else None
                    ),
                    matched_variant_id=cand.variant_id if cand is not None else None,
                    available_qty=avail_qty,
                    available_unit=avail_unit,
                    required_qty=req.quantity,
                    required_unit=req.unit,
                    reason=reason,
                    expiring_soon=exp_soon,
                    earliest_expires_on=earliest_exp,
                    matched_substitution=matched_sub,
                )
            )

        bucket = _classify_bucket(required_missing + required_partial)
        matched_dishes.append(
            DishMatchOut(
                dish_id=dish.id,
                slug=dish.slug,
                canonical_name=dish.canonical_name,
                alternative_names=list(dish.alternative_names),
                cuisine=dish.cuisine,
                region=dish.region,
                meal_types=list(dish.meal_types),
                diet_tags=list(dish.diet_tags),
                prep_minutes=dish.prep_minutes,
                cook_minutes=dish.cook_minutes,
                default_servings=dish.default_servings,
                bucket=bucket,
                required_total=required_total,
                required_available=required_available,
                required_partial=required_partial,
                required_missing=required_missing,
                missing_ingredient_names=missing_names,
                expiring_matches=expiring_matches,
                is_saved=dish.id in saved_dish_ids,
                user_feedback_action=latest_feedback_by_dish.get(dish.id),
                requirements=req_outs,
            )
        )

    matched_dishes.sort(key=lambda d: _ranking_key(d, prefs))

    can_make_now = [d for d in matched_dishes if d.bucket == "can_make_now"]
    almost_there = [d for d in matched_dishes if d.bucket == "almost_there"]
    explore_more = [d for d in matched_dishes if d.bucket == "explore_more"]

    return DishMatchResponse(
        can_make_now=can_make_now,
        almost_there=almost_there,
        explore_more=explore_more,
        counts={
            "can_make_now": len(can_make_now),
            "almost_there": len(almost_there),
            "explore_more": len(explore_more),
        },
    )
