from contextlib import suppress
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.cooking.models import CookingPlan, MealHistory
from app.modules.cooking.schemas import (
    CompleteCookingPlanRequest,
    CookingPlanOut,
    CreateCookingPlanRequest,
    MealFeedbackRequest,
    MealHistoryListResponse,
    MealHistoryOut,
)
from app.modules.inventory.service import BatchFEFORequest, batch_allocate_fefo


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _to_plan_out(
    plan: CookingPlan,
    canonical_names: dict[str, str] | None = None,
) -> CookingPlanOut:
    out = CookingPlanOut.model_validate(plan)
    names_map = canonical_names or {}
    normalized_ings = [
        {
            **ing,
            "line_id": str(
                ing.get("line_id") or f"{plan.id}:{ing.get('canonical_item_id', '')}:{idx}"
            ),
            "name": (
                str(ing.get("name") or "").strip()
                or names_map.get(str(ing.get("canonical_item_id", "")), "")
                or "Ingredient"
            ),
        }
        for idx, ing in enumerate(plan.ingredients or [])
    ]
    out.ingredients = normalized_ings
    out.cooking_plan_id = plan.id
    out.title = plan.recipe_title
    out.items = normalized_ings
    out.created_at = plan.started_at
    return out


async def _load_canonical_name_map(db: AsyncSession, plans: list[CookingPlan]) -> dict[str, str]:
    from app.modules.inventory.models import CanonicalItem

    cids: set[UUID] = set()
    for p in plans:
        for ing in p.ingredients or []:
            if not str(ing.get("name") or "").strip():
                with suppress(KeyError, ValueError, TypeError):
                    cids.add(UUID(str(ing["canonical_item_id"])))
    if not cids:
        return {}
    rows = (
        await db.execute(
            select(CanonicalItem.id, CanonicalItem.name).where(CanonicalItem.id.in_(cids))
        )
    ).all()
    return {str(cid): str(cname) for cid, cname in rows}


async def create_cooking_plan(
    db: AsyncSession,
    *,
    household_id: UUID,
    user_id: UUID,
    payload: CreateCookingPlanRequest,
) -> CookingPlanOut:
    """Create a cooking plan in 'planned' status with ZERO stock deduction."""
    raw_ingredients = (
        payload.ingredients if payload.ingredients is not None else (payload.items or [])
    )
    recipe_title = (payload.recipe_title or payload.title or "Cooking Plan").strip()
    ingredients_data = [
        {
            "line_id": str(item.line_id or uuid4()),
            "canonical_item_id": str(item.canonical_item_id),
            "name": item.name or "",
            "planned_qty": str(item.planned_qty),
            "actual_qty": None,
            "base_unit": item.base_unit,
        }
        for item in raw_ingredients
    ]
    snapshot = None
    if payload.description or payload.steps or payload.substitutions:
        snapshot = {
            "description": payload.description,
            "steps": payload.steps or [],
            "substitutions": payload.substitutions or [],
        }
    plan = CookingPlan(
        household_id=household_id,
        created_by=user_id,
        recipe_id=payload.recipe_id,
        recipe_title=recipe_title,
        status="planned",
        planned_servings=payload.planned_servings,
        ingredients=ingredients_data,
        meal_type=payload.meal_type,
        cuisine=payload.cuisine,
        prep_minutes=payload.prep_minutes,
        cook_minutes=payload.cook_minutes,
        recipe_snapshot=snapshot,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return _to_plan_out(plan)


async def list_cooking_plans(
    db: AsyncSession,
    household_id: UUID,
    *,
    status_filter: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[CookingPlanOut], int]:
    base_where = [CookingPlan.household_id == household_id]
    if status_filter:
        base_where.append(CookingPlan.status == status_filter)

    total = int(
        (
            await db.execute(select(func.count()).select_from(CookingPlan).where(*base_where))
        ).scalar_one()
    )
    stmt = (
        select(CookingPlan)
        .where(*base_where)
        .order_by(CookingPlan.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    names_map = await _load_canonical_name_map(db, rows)
    return [_to_plan_out(row, names_map) for row in rows], total


async def get_cooking_plan(db: AsyncSession, household_id: UUID, plan_id: UUID) -> CookingPlanOut:
    stmt = select(CookingPlan).where(
        CookingPlan.id == plan_id, CookingPlan.household_id == household_id
    )
    plan = (await db.execute(stmt)).scalar_one_or_none()
    if plan is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Cooking plan not found.")
    names_map = await _load_canonical_name_map(db, [plan])
    return _to_plan_out(plan, names_map)


async def complete_cooking_plan(
    db: AsyncSession,
    *,
    household_id: UUID,
    user_id: UUID,
    plan_id: UUID,
    payload: CompleteCookingPlanRequest | None = None,
) -> CookingPlanOut:
    """Complete a cooking plan by deducting confirmed actual_qty via FEFO."""
    stmt = select(CookingPlan).where(
        CookingPlan.id == plan_id, CookingPlan.household_id == household_id
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    plan = (await db.execute(stmt)).scalar_one_or_none()
    if plan is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Cooking plan not found.")
    if plan.status not in ("planned", "in_progress"):
        raise AppError(
            status_code=409,
            code="INVALID_STATE_TRANSITION",
            message=f"Cannot complete cooking plan in '{plan.status}' status.",
        )

    overrides_by_line: dict[str, Decimal] = {}
    waste_by_line: dict[str, Decimal] = {}
    omitted_by_line: set[str] = set()
    session_confirmed_by_line: dict[str, tuple[Decimal | None, str | None]] = {}
    overrides_by_cid: dict[str, Decimal] = {}
    waste_by_cid: dict[str, Decimal] = {}
    omitted_by_cid: set[str] = set()
    if payload and payload.items is not None:
        for item in payload.items:
            val = item.actual_qty if item.actual_qty is not None else item.actual_used_qty
            if item.line_id:
                if item.omitted:
                    omitted_by_line.add(item.line_id)
                if item.session_confirmed:
                    session_confirmed_by_line[item.line_id] = (
                        item.confirmed_qty,
                        item.confirmed_unit,
                    )
                if val is not None:
                    overrides_by_line[item.line_id] = val
                if item.waste_qty is not None:
                    waste_by_line[item.line_id] = item.waste_qty
            else:
                cid_key = str(item.canonical_item_id)
                if item.omitted:
                    omitted_by_cid.add(cid_key)
                if val is not None:
                    overrides_by_cid[cid_key] = val
                if item.waste_qty is not None:
                    waste_by_cid[cid_key] = item.waste_qty

    updated_ingredients: list[dict] = []
    all_deductions: list[dict] = []
    batch_requests: list[BatchFEFORequest] = []

    for idx, ing in enumerate(plan.ingredients):
        line_id = str(ing.get("line_id") or f"{plan.id}:{ing['canonical_item_id']}:{idx}")
        cid_str = str(ing["canonical_item_id"])
        cid = UUID(cid_str)
        planned_qty = Decimal(str(ing["planned_qty"]))
        line_unit = str(ing.get("base_unit") or "").strip() or None

        is_session_confirmed = line_id in session_confirmed_by_line
        is_omitted = is_session_confirmed or line_id in omitted_by_line or (
            line_id not in overrides_by_line and cid_str in omitted_by_cid
        )
        if is_omitted:
            actual_qty = Decimal("0")
            waste_qty = Decimal("0")
        else:
            actual_qty = overrides_by_line.get(line_id, overrides_by_cid.get(cid_str, planned_qty))
            waste_qty = waste_by_line.get(line_id, waste_by_cid.get(cid_str, Decimal("0")))

        if actual_qty > Decimal("0"):
            batch_requests.append(
                BatchFEFORequest(
                    canonical_item_id=cid,
                    required_qty=actual_qty,
                    base_unit=line_unit,
                    event_type="COOK_OUT",
                    notes=f"Cooked {plan.recipe_title}",
                )
            )
        if waste_qty > Decimal("0"):
            batch_requests.append(
                BatchFEFORequest(
                    canonical_item_id=cid,
                    required_qty=waste_qty,
                    base_unit=line_unit,
                    event_type="WASTE_OUT",
                    notes=f"Waste while cooking {plan.recipe_title}",
                )
            )

        updated_entry: dict[str, Any] = {
            **ing,
            "line_id": line_id,
            "actual_qty": str(actual_qty),
            "waste_qty": str(waste_qty),
            "total_deducted_qty": str(actual_qty + waste_qty),
            "omitted": is_omitted,
        }
        if is_session_confirmed:
            conf_qty, conf_unit = session_confirmed_by_line[line_id]
            updated_entry["session_confirmed"] = True
            if conf_qty is not None:
                updated_entry["confirmed_qty"] = str(conf_qty)
            if conf_unit:
                updated_entry["confirmed_unit"] = conf_unit
        updated_ingredients.append(updated_entry)

    if batch_requests:
        batch_results = await batch_allocate_fefo(
            db,
            household_id=household_id,
            requests=batch_requests,
            actor_user_id=user_id,
            reference_type="cooking_plan",
            reference_id=plan.id,
        )
        for req, deductions in batch_results:
            for d in deductions:
                all_deductions.append(
                    {
                        "canonical_item_id": str(d.canonical_item_id),
                        "lot_id": str(d.lot_id),
                        "deducted_qty": str(d.deducted_qty),
                        "base_unit": d.base_unit,
                        "ledger_entry_id": str(d.ledger_entry_id),
                        "event_type": req.event_type,
                    }
                )

    completed_at = _utcnow()
    plan.ingredients = updated_ingredients
    plan.deductions = all_deductions
    plan.status = "completed"
    plan.completed_at = completed_at
    if payload and payload.meal_type:
        plan.meal_type = payload.meal_type
    if payload and payload.cook_minutes is not None:
        plan.cook_minutes = payload.cook_minutes

    snapshot = dict(plan.recipe_snapshot or {})
    if payload and payload.modifications:
        snapshot["modifications"] = payload.modifications
    substitutions = list(
        (payload.substitutions if payload and payload.substitutions is not None else None)
        or snapshot.get("substitutions")
        or []
    )

    has_feedback = bool(
        payload
        and (
            payload.rating is not None
            or payload.feedback_tags
            or payload.feedback_comment
            or payload.would_make_again is not None
        )
    )
    meal_entry = MealHistory(
        household_id=household_id,
        cooking_plan_id=plan.id,
        cooked_by=user_id,
        recipe_id=plan.recipe_id,
        recipe_title=plan.recipe_title,
        meal_type=plan.meal_type,
        cuisine=plan.cuisine,
        servings=plan.planned_servings,
        prep_minutes=plan.prep_minutes,
        cook_minutes=plan.cook_minutes,
        ingredients_used=updated_ingredients,
        substitutions=substitutions,
        recipe_snapshot=snapshot or None,
        cooked_at=completed_at,
        rating=payload.rating if payload else None,
        feedback_tags=list(payload.feedback_tags or []) if payload else [],
        feedback_comment=payload.feedback_comment if payload else None,
        would_make_again=payload.would_make_again if payload else None,
        feedback_updated_at=completed_at if has_feedback else None,
    )
    db.add(meal_entry)
    await db.commit()
    await db.refresh(plan)
    await db.refresh(meal_entry)
    out = _to_plan_out(plan)
    out.meal_history_id = meal_entry.id
    return out


async def cancel_cooking_plan(
    db: AsyncSession,
    *,
    household_id: UUID,
    plan_id: UUID,
) -> CookingPlanOut:
    """Cancel a cooking plan with ZERO stock deduction."""
    stmt = select(CookingPlan).where(
        CookingPlan.id == plan_id, CookingPlan.household_id == household_id
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    plan = (await db.execute(stmt)).scalar_one_or_none()
    if plan is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Cooking plan not found.")
    if plan.status not in ("planned", "in_progress"):
        raise AppError(
            status_code=409,
            code="INVALID_STATE_TRANSITION",
            message=f"Cannot cancel cooking plan in '{plan.status}' status.",
        )

    plan.status = "cancelled"
    plan.cancelled_at = _utcnow()
    await db.commit()
    await db.refresh(plan)
    return _to_plan_out(plan)


async def list_meal_history(
    db: AsyncSession,
    household_id: UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    meal_type: str | None = None,
    min_rating: int | None = None,
    max_rating: int | None = None,
    would_make_again: bool | None = None,
    limit: int = 30,
    offset: int = 0,
) -> MealHistoryListResponse:
    from collections import Counter

    all_rows = (
        (
            await db.execute(
                select(MealHistory)
                .where(MealHistory.household_id == household_id)
                .order_by(MealHistory.cooked_at.desc())
            )
        )
        .scalars()
        .all()
    )

    title_counts = Counter(r.recipe_title for r in all_rows)
    frequently_cooked = [t for t, c in title_counts.most_common(10) if c >= 2]
    favourite_meals: list[str] = []
    disliked_meals: list[str] = []
    for r in all_rows:
        if (
            (r.rating is not None and r.rating >= 4) or r.would_make_again is True
        ) and r.recipe_title not in favourite_meals:
            favourite_meals.append(r.recipe_title)
        if (
            (r.rating is not None and r.rating <= 2) or r.would_make_again is False
        ) and r.recipe_title not in disliked_meals:
            disliked_meals.append(r.recipe_title)

    filtered: list[MealHistory] = []
    for r in all_rows:
        cooked_day = r.cooked_at.date()
        if date_from and cooked_day < date_from:
            continue
        if date_to and cooked_day > date_to:
            continue
        if meal_type and (r.meal_type or "").lower() != meal_type.strip().lower():
            continue
        if min_rating is not None and (r.rating is None or r.rating < min_rating):
            continue
        if max_rating is not None and (r.rating is None or r.rating > max_rating):
            continue
        if would_make_again is not None and r.would_make_again is not would_make_again:
            continue
        filtered.append(r)

    page = filtered[offset : offset + limit]
    items_out = []
    for row in page:
        out = MealHistoryOut.model_validate(row)
        out.cook_count = title_counts[row.recipe_title]
        items_out.append(out)

    return MealHistoryListResponse(
        items=items_out,
        total=len(filtered),
        frequently_cooked=frequently_cooked,
        favourite_meals=favourite_meals[:10],
        disliked_meals=disliked_meals[:10],
    )


async def get_meal_history_detail(
    db: AsyncSession, household_id: UUID, meal_id: UUID
) -> MealHistoryOut:
    row = (
        await db.execute(
            select(MealHistory).where(
                MealHistory.id == meal_id,
                MealHistory.household_id == household_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Meal history record not found.")
    count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(MealHistory)
                .where(
                    MealHistory.household_id == household_id,
                    MealHistory.recipe_title == row.recipe_title,
                )
            )
        ).scalar_one()
    )
    out = MealHistoryOut.model_validate(row)
    out.cook_count = count
    return out


async def update_meal_feedback(
    db: AsyncSession,
    *,
    household_id: UUID,
    meal_id: UUID,
    payload: MealFeedbackRequest,
) -> MealHistoryOut:
    row = (
        await db.execute(
            select(MealHistory).where(
                MealHistory.id == meal_id,
                MealHistory.household_id == household_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Meal history record not found.")

    row.rating = payload.rating
    row.feedback_tags = list(payload.feedback_tags or [])
    row.feedback_comment = payload.feedback_comment
    row.would_make_again = payload.would_make_again
    row.feedback_updated_at = _utcnow()
    await db.commit()
    await db.refresh(row)
    return await get_meal_history_detail(db, household_id, meal_id)
