from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.auth.models import Household
from app.modules.discovery.daily_context import build_daily_household_context
from app.modules.discovery.daily_generator import DailyMealPlanGenerator
from app.modules.discovery.daily_models import DailyMealPlan, DailyMealPlanRecipe
from app.modules.recipes.models import HouseholdRecipePreference

logger = logging.getLogger(__name__)

DAILY_GENERATION_TARGET_HOUR = 3
MANUAL_REGENERATION_DAILY_LIMIT = 1
TRANSIENT_AUTO_RETRY_ERROR_CODES = frozenset({"CONNECTERROR", "CONNECTTIMEOUT", "READTIMEOUT"})
TRANSIENT_AUTO_RETRY_COOLDOWN_SECONDS = 120
MAX_TRANSIENT_AUTO_RETRIES = 1
STALE_GENERATING_THRESHOLD_SECONDS = 600
MAX_STALE_GENERATING_RECOVERIES = 1


def resolve_timezone(tz_name: str | None) -> tuple[str, ZoneInfo]:
    candidate = (tz_name or "Asia/Kolkata").strip() or "Asia/Kolkata"
    try:
        return candidate, ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return "Asia/Kolkata", ZoneInfo("Asia/Kolkata")


def compute_household_local_now(
    timezone_str: str | None,
    utc_now: datetime | None = None,
) -> tuple[str, datetime]:
    ref_utc = utc_now or datetime.now(UTC)
    if ref_utc.tzinfo is None:
        ref_utc = ref_utc.replace(tzinfo=UTC)
    resolved_tz, tz = resolve_timezone(timezone_str)
    return resolved_tz, ref_utc.astimezone(tz)


def is_household_due_for_daily_plan(
    timezone_str: str | None,
    utc_now: datetime | None = None,
    target_hour: int = DAILY_GENERATION_TARGET_HOUR,
) -> bool:
    _, local_dt = compute_household_local_now(timezone_str, utc_now)
    return local_dt.hour >= target_hour


async def claim_daily_plan_slot(
    db: AsyncSession,
    *,
    household_id: UUID,
    local_date: date,
    timezone_str: str,
    is_manual_regeneration: bool = False,
    utc_now: datetime | None = None,
    stale_threshold_seconds: int = STALE_GENERATING_THRESHOLD_SECONDS,
) -> DailyMealPlan | None:
    """Atomically claim a household's daily plan slot for generation.

    Guarantees at most ONE automatic LLM generation attempt per (household_id, local_date)
    even across concurrent workers or repeated scheduler ticks, with bounded recovery for
    transient transport failures and stale interrupted generations.
    """
    now_utc = utc_now or datetime.now(UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    if not is_manual_regeneration:
        plan = DailyMealPlan(
            household_id=household_id,
            local_date=local_date,
            timezone=timezone_str,
            status="generating",
            auto_attempted=True,
            manual_regeneration_count=0,
            context_snapshot={},
            provider_metadata={},
            generation_started_at=now_utc,
        )
        try:
            async with db.begin_nested():
                db.add(plan)
                await db.flush()
            return plan
        except IntegrityError:
            # Row already exists for (household_id, local_date). Only claim if pending
            # and never automatically attempted before.
            res = await db.execute(
                update(DailyMealPlan)
                .where(
                    DailyMealPlan.household_id == household_id,
                    DailyMealPlan.local_date == local_date,
                    DailyMealPlan.status == "pending",
                    DailyMealPlan.auto_attempted.is_(False),
                )
                .values(
                    status="generating",
                    auto_attempted=True,
                    timezone=timezone_str,
                    generation_started_at=now_utc,
                    updated_at=now_utc,
                )
            )
            if (res.rowcount or 0) == 1:
                return (
                    await db.execute(
                        select(DailyMealPlan).where(
                            DailyMealPlan.household_id == household_id,
                            DailyMealPlan.local_date == local_date,
                        )
                    )
                ).scalar_one_or_none()

            # Allow at most MAX_TRANSIENT_AUTO_RETRIES automatic retry after cooldown
            # when a transient transport error (e.g. ConnectError on laptop wake) failed.
            existing = (
                await db.execute(
                    select(DailyMealPlan).where(
                        DailyMealPlan.household_id == household_id,
                        DailyMealPlan.local_date == local_date,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                return None

            if existing.status == "generating":
                started_at = existing.generation_started_at or existing.updated_at
                if started_at is not None and started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                if (
                    started_at is not None
                    and (now_utc - started_at).total_seconds() < max(1, stale_threshold_seconds)
                ):
                    return None

                prev_meta = dict(existing.provider_metadata or {})
                stale_recovery_count = int(prev_meta.get("stale_recovery_count") or 0)
                prev_meta["interrupted_generation_count"] = (
                    int(prev_meta.get("interrupted_generation_count") or 0) + 1
                )
                prev_meta["last_interrupted_at"] = now_utc.isoformat()
                if started_at is not None:
                    prev_meta["previous_generation_started_at"] = started_at.isoformat()
                if existing.error_code:
                    prev_meta["previous_error_code"] = existing.error_code

                started_cond = (
                    DailyMealPlan.generation_started_at == existing.generation_started_at
                    if existing.generation_started_at is not None
                    else DailyMealPlan.generation_started_at.is_(None)
                )

                if stale_recovery_count >= MAX_STALE_GENERATING_RECOVERIES:
                    await db.execute(
                        update(DailyMealPlan)
                        .where(
                            DailyMealPlan.id == existing.id,
                            DailyMealPlan.status == "generating",
                            started_cond,
                        )
                        .values(
                            status="failed",
                            error_code="STALE_GENERATION_INTERRUPTED",
                            error_message=(
                                "Generation was interrupted and exceeded the automatic "
                                "recovery limit."
                            ),
                            provider_metadata=prev_meta,
                            generation_completed_at=now_utc,
                            updated_at=now_utc,
                        )
                    )
                    await db.commit()
                    return None

                prev_meta["stale_recovery_count"] = stale_recovery_count + 1
                stale_res = await db.execute(
                    update(DailyMealPlan)
                    .where(
                        DailyMealPlan.id == existing.id,
                        DailyMealPlan.status == "generating",
                        started_cond,
                    )
                    .values(
                        status="generating",
                        timezone=timezone_str,
                        error_code=None,
                        error_message=None,
                        provider_metadata=prev_meta,
                        generation_started_at=now_utc,
                        updated_at=now_utc,
                    )
                )
                if (stale_res.rowcount or 0) != 1:
                    return None
                return (
                    await db.execute(
                        select(DailyMealPlan).where(DailyMealPlan.id == existing.id)
                    )
                ).scalar_one_or_none()

            if existing.status != "failed":
                return None
            if (existing.error_code or "").upper() not in TRANSIENT_AUTO_RETRY_ERROR_CODES:
                return None
            prev_meta = dict(existing.provider_metadata or {})
            retry_count = int(prev_meta.get("transient_retry_count") or 0)
            if retry_count >= MAX_TRANSIENT_AUTO_RETRIES:
                return None
            completed_at = existing.generation_completed_at
            if completed_at is not None:
                if completed_at.tzinfo is None:
                    completed_at = completed_at.replace(tzinfo=UTC)
                if (now_utc - completed_at).total_seconds() < TRANSIENT_AUTO_RETRY_COOLDOWN_SECONDS:
                    return None
            prev_meta["transient_retry_count"] = retry_count + 1
            if existing.error_code:
                prev_meta["previous_error_code"] = existing.error_code
            retry_res = await db.execute(
                update(DailyMealPlan)
                .where(
                    DailyMealPlan.id == existing.id,
                    DailyMealPlan.status == "failed",
                )
                .values(
                    status="generating",
                    timezone=timezone_str,
                    error_code=None,
                    error_message=None,
                    provider_metadata=prev_meta,
                    generation_started_at=now_utc,
                    updated_at=now_utc,
                )
            )
            if (retry_res.rowcount or 0) != 1:
                return None
            return (
                await db.execute(
                    select(DailyMealPlan).where(DailyMealPlan.id == existing.id)
                )
            ).scalar_one_or_none()

    # Manual regeneration path: bounded to MANUAL_REGENERATION_DAILY_LIMIT per day
    manual_plan = DailyMealPlan(
        household_id=household_id,
        local_date=local_date,
        timezone=timezone_str,
        status="generating",
        auto_attempted=False,
        manual_regeneration_count=1,
        context_snapshot={},
        provider_metadata={},
        generation_started_at=now_utc,
    )
    try:
        async with db.begin_nested():
            db.add(manual_plan)
            await db.flush()
        return manual_plan
    except IntegrityError:
        res = await db.execute(
            update(DailyMealPlan)
            .where(
                DailyMealPlan.household_id == household_id,
                DailyMealPlan.local_date == local_date,
                DailyMealPlan.status != "generating",
                DailyMealPlan.manual_regeneration_count < MANUAL_REGENERATION_DAILY_LIMIT,
            )
            .values(
                status="generating",
                manual_regeneration_count=DailyMealPlan.manual_regeneration_count + 1,
                timezone=timezone_str,
                generation_started_at=now_utc,
                updated_at=now_utc,
            )
        )
        if (res.rowcount or 0) != 1:
            return None
        return (
            await db.execute(
                select(DailyMealPlan).where(
                    DailyMealPlan.household_id == household_id,
                    DailyMealPlan.local_date == local_date,
                )
            )
        ).scalar_one_or_none()


async def run_daily_plan_for_household(
    db: AsyncSession,
    household_id: UUID,
    *,
    utc_now: datetime | None = None,
    generator: DailyMealPlanGenerator | None = None,
    is_manual_regeneration: bool = False,
    weather_snapshot: dict[str, Any] | None = None,
    stale_threshold_seconds: int = STALE_GENERATING_THRESHOLD_SECONDS,
) -> DailyMealPlan | None:
    """Claim and generate the daily 8-recipe plan for a household if due."""
    pref_tz = (
        await db.execute(
            select(HouseholdRecipePreference.timezone).where(
                HouseholdRecipePreference.household_id == household_id
            )
        )
    ).scalar_one_or_none()

    resolved_tz, local_dt = compute_household_local_now(pref_tz, utc_now)
    if not is_manual_regeneration and local_dt.hour < DAILY_GENERATION_TARGET_HOUR:
        return None

    claimed = await claim_daily_plan_slot(
        db,
        household_id=household_id,
        local_date=local_dt.date(),
        timezone_str=resolved_tz,
        is_manual_regeneration=is_manual_regeneration,
        stale_threshold_seconds=stale_threshold_seconds,
    )
    if claimed is None:
        return None

    await db.commit()

    context = await build_daily_household_context(
        db,
        household_id,
        local_date=local_dt.date(),
        timezone_str=resolved_tz,
        weather_snapshot=weather_snapshot,
    )
    gen = generator or DailyMealPlanGenerator()
    outcome = await gen.generate(context)

    completed_at = datetime.now(UTC)
    sanitized_context = {
        "context_version": context.get("context_version"),
        "local_date": context.get("local_date"),
        "timezone": context.get("timezone"),
        "inventory_count": len(context.get("inventory_items") or []),
        "explicit_preferences": context.get("explicit_preferences"),
    }

    claimed_row = (
        await db.execute(select(DailyMealPlan).where(DailyMealPlan.id == claimed.id))
    ).scalar_one()
    claimed_row.context_snapshot = sanitized_context
    claimed_row.weather_snapshot = context.get("weather")
    merged_provider_meta = dict(outcome.provider_metadata or {})
    if isinstance(claimed.provider_metadata, dict):
        for diag_key in (
            "transient_retry_count",
            "stale_recovery_count",
            "interrupted_generation_count",
            "last_interrupted_at",
            "previous_generation_started_at",
            "previous_error_code",
        ):
            if claimed.provider_metadata.get(diag_key) is not None:
                merged_provider_meta[diag_key] = claimed.provider_metadata[diag_key]
    claimed_row.provider_metadata = merged_provider_meta
    claimed_row.generation_completed_at = completed_at

    if outcome.success:
        await db.execute(
            delete(DailyMealPlanRecipe).where(
                DailyMealPlanRecipe.daily_meal_plan_id == claimed_row.id
            )
        )
        for slot in outcome.slots:
            db.add(
                DailyMealPlanRecipe(
                    daily_meal_plan_id=claimed_row.id,
                    meal_category=slot.meal_category,
                    position=slot.position,
                    recipe_data=slot.recipe_data,
                    availability_data=slot.availability_data,
                    quality_status=slot.quality_status,
                    quality_warnings=slot.quality_warnings,
                )
            )
        claimed_row.status = "ready"
        claimed_row.error_code = None
        claimed_row.error_message = None
    else:
        claimed_row.status = "failed"
        claimed_row.error_code = outcome.error_code
        claimed_row.error_message = outcome.error_message

    await db.commit()
    await db.refresh(claimed_row)
    return claimed_row


async def run_scheduler_tick(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    utc_now: datetime | None = None,
    generator: DailyMealPlanGenerator | None = None,
    stale_threshold_seconds: int = STALE_GENERATING_THRESHOLD_SECONDS,
) -> int:
    """Poll households and generate due daily meal plans once per household/local_date."""
    async with session_maker() as db:
        household_ids = list((await db.execute(select(Household.id))).scalars().all())

    attempted = 0
    for hid in household_ids:
        try:
            async with session_maker() as db:
                res = await run_daily_plan_for_household(
                    db,
                    hid,
                    utc_now=utc_now,
                    generator=generator,
                    stale_threshold_seconds=stale_threshold_seconds,
                )
                if res is not None:
                    attempted += 1
        except Exception:
            logger.exception("Daily meal scheduler failed for household %s", hid)
    return attempted


class DailyMealPlanScheduler:
    """Persistent DB-backed asyncio scheduler for 03:00 household-local daily plans."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: int = 60,
        stale_threshold_seconds: int = STALE_GENERATING_THRESHOLD_SECONDS,
    ) -> None:
        self._session_maker = session_maker
        self._poll_interval_seconds = max(5, poll_interval_seconds)
        self._stale_threshold_seconds = max(60, stale_threshold_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._loop(), name="daily-meal-plan-scheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await run_scheduler_tick(
                    self._session_maker,
                    stale_threshold_seconds=self._stale_threshold_seconds,
                )
            except Exception:
                logger.exception("Unhandled error in DailyMealPlanScheduler tick")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval_seconds
                )
            except TimeoutError:
                continue
