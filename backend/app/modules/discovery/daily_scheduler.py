"""Persistent DB-backed daily meal plan scheduler with ownership-fenced leases.

Durability note:
    ``DailyMealPlanScheduler`` and manual regeneration runs execute as in-process
    asyncio tasks inside the FastAPI worker process. They are **not** durable across
    process crashes, SIGKILL, host sleep/suspension, or Render/container restarts.
    Instead, every generation attempt claims a unique ``active_generation_id`` UUID
    and a bounded ``lease_expires_at`` window renewed by periodic heartbeats. If the
    process crashes or restarts mid-generation, heartbeats cease, the lease expires
    automatically, and the next scheduler tick safely reclaims the abandoned row
    under a fresh ``active_generation_id`` while ownership-fenced writes guarantee
    that any lingering old worker can never overwrite newer state or recipes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.auth.models import Household
from app.modules.discovery.daily_context import build_daily_household_context
from app.modules.discovery.daily_generator import DailyGenerationOutcome, DailyMealPlanGenerator
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
DEFAULT_LEASE_SECONDS = 120
DEFAULT_HEARTBEAT_SECONDS = 30


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _is_lease_expired(
    existing: DailyMealPlan,
    now_utc: datetime,
    stale_threshold_seconds: int,
) -> bool:
    """Return True if a 'generating' row's lease has expired (or legacy timestamp is stale)."""
    lease_exp = _as_utc(existing.lease_expires_at)
    if lease_exp is not None:
        return now_utc >= lease_exp

    # Fallback for legacy pre-0022 rows (lease_expires_at IS NULL)
    ref_ts = _as_utc(
        existing.last_heartbeat_at or existing.generation_started_at or existing.updated_at
    )
    if ref_ts is None:
        return True
    return (now_utc - ref_ts).total_seconds() >= max(1, stale_threshold_seconds)


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
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    generation_id: UUID | None = None,
) -> DailyMealPlan | None:
    """Atomically claim a household's daily plan slot with a unique generation ownership ID.

    Guarantees at most ONE active owner per ``(household_id, local_date)`` across concurrent
    workers or repeated scheduler ticks, with lease-based recovery for crashed/abandoned
    generations and bounded recovery for transient transport failures.
    """
    now_utc = _as_utc(utc_now or datetime.now(UTC))
    assert now_utc is not None
    gen_id = generation_id or uuid4()
    lease_exp = now_utc + timedelta(seconds=max(1, lease_seconds))

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
            active_generation_id=gen_id,
            last_heartbeat_at=now_utc,
            lease_expires_at=lease_exp,
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
                    error_code=None,
                    error_message=None,
                    generation_started_at=now_utc,
                    active_generation_id=gen_id,
                    last_heartbeat_at=now_utc,
                    lease_expires_at=lease_exp,
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
                if not _is_lease_expired(existing, now_utc, stale_threshold_seconds):
                    return None

                started_at = _as_utc(existing.generation_started_at or existing.updated_at)
                prev_meta = dict(existing.provider_metadata or {})
                stale_recovery_count = int(prev_meta.get("stale_recovery_count") or 0)
                prev_meta["interrupted_generation_count"] = (
                    int(prev_meta.get("interrupted_generation_count") or 0) + 1
                )
                prev_meta["last_interrupted_at"] = now_utc.isoformat()
                if started_at is not None:
                    prev_meta["previous_generation_started_at"] = started_at.isoformat()
                if existing.active_generation_id is not None:
                    prev_meta["previous_generation_id"] = str(existing.active_generation_id)
                if existing.error_code:
                    prev_meta["previous_error_code"] = existing.error_code

                started_cond = (
                    DailyMealPlan.generation_started_at == existing.generation_started_at
                    if existing.generation_started_at is not None
                    else DailyMealPlan.generation_started_at.is_(None)
                )
                gen_cond = (
                    DailyMealPlan.active_generation_id == existing.active_generation_id
                    if existing.active_generation_id is not None
                    else DailyMealPlan.active_generation_id.is_(None)
                )

                if stale_recovery_count >= MAX_STALE_GENERATING_RECOVERIES:
                    await db.execute(
                        update(DailyMealPlan)
                        .where(
                            DailyMealPlan.id == existing.id,
                            DailyMealPlan.status == "generating",
                            started_cond,
                            gen_cond,
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
                            active_generation_id=None,
                            lease_expires_at=None,
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
                        gen_cond,
                    )
                    .values(
                        status="generating",
                        timezone=timezone_str,
                        error_code=None,
                        error_message=None,
                        provider_metadata=prev_meta,
                        generation_started_at=now_utc,
                        active_generation_id=gen_id,
                        last_heartbeat_at=now_utc,
                        lease_expires_at=lease_exp,
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

            # Allow at most MAX_TRANSIENT_AUTO_RETRIES automatic retry after cooldown
            # when a transient transport error (e.g. ConnectError on laptop wake) failed.
            if existing.status != "failed":
                return None
            if (existing.error_code or "").upper() not in TRANSIENT_AUTO_RETRY_ERROR_CODES:
                return None
            prev_meta = dict(existing.provider_metadata or {})
            retry_count = int(prev_meta.get("transient_retry_count") or 0)
            if retry_count >= MAX_TRANSIENT_AUTO_RETRIES:
                return None
            completed_at = _as_utc(existing.generation_completed_at)
            if (
                completed_at is not None
                and (now_utc - completed_at).total_seconds()
                < TRANSIENT_AUTO_RETRY_COOLDOWN_SECONDS
            ):
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
                    active_generation_id=gen_id,
                    last_heartbeat_at=now_utc,
                    lease_expires_at=lease_exp,
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
        active_generation_id=gen_id,
        last_heartbeat_at=now_utc,
        lease_expires_at=lease_exp,
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
                error_code=None,
                error_message=None,
                generation_started_at=now_utc,
                active_generation_id=gen_id,
                last_heartbeat_at=now_utc,
                lease_expires_at=lease_exp,
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

        # Also permit manual reclamation if an abandoned 'generating' row's lease expired
        existing = (
            await db.execute(
                select(DailyMealPlan).where(
                    DailyMealPlan.household_id == household_id,
                    DailyMealPlan.local_date == local_date,
                )
            )
        ).scalar_one_or_none()
        if (
            existing is None
            or existing.status != "generating"
            or existing.manual_regeneration_count >= MANUAL_REGENERATION_DAILY_LIMIT
            or not _is_lease_expired(existing, now_utc, stale_threshold_seconds)
        ):
            return None

        gen_cond = (
            DailyMealPlan.active_generation_id == existing.active_generation_id
            if existing.active_generation_id is not None
            else DailyMealPlan.active_generation_id.is_(None)
        )
        reclaim_res = await db.execute(
            update(DailyMealPlan)
            .where(
                DailyMealPlan.id == existing.id,
                DailyMealPlan.status == "generating",
                gen_cond,
                DailyMealPlan.manual_regeneration_count < MANUAL_REGENERATION_DAILY_LIMIT,
            )
            .values(
                status="generating",
                manual_regeneration_count=DailyMealPlan.manual_regeneration_count + 1,
                timezone=timezone_str,
                error_code=None,
                error_message=None,
                generation_started_at=now_utc,
                active_generation_id=gen_id,
                last_heartbeat_at=now_utc,
                lease_expires_at=lease_exp,
                updated_at=now_utc,
            )
        )
        if (reclaim_res.rowcount or 0) != 1:
            return None
        return (
            await db.execute(select(DailyMealPlan).where(DailyMealPlan.id == existing.id))
        ).scalar_one_or_none()


async def renew_generation_lease(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    plan_id: UUID,
    generation_id: UUID,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    utc_now: datetime | None = None,
) -> bool:
    """Renew heartbeat and lease expiry iff this generation_id still owns the plan.

    Returns ``True`` when the fenced update matched the active owner row, or ``False``
    if ownership has been lost (e.g., reclaimed by another worker or finalized).
    """
    now_utc = _as_utc(utc_now or datetime.now(UTC))
    assert now_utc is not None
    next_lease = now_utc + timedelta(seconds=max(1, lease_seconds))

    async with session_maker() as hb_db:
        res = await hb_db.execute(
            update(DailyMealPlan)
            .where(
                DailyMealPlan.id == plan_id,
                DailyMealPlan.status == "generating",
                DailyMealPlan.active_generation_id == generation_id,
            )
            .values(
                last_heartbeat_at=now_utc,
                lease_expires_at=next_lease,
                updated_at=now_utc,
            )
        )
        await hb_db.commit()
        return (res.rowcount or 0) == 1


async def finalize_generation_attempt(
    db: AsyncSession,
    *,
    plan_id: UUID,
    generation_id: UUID,
    outcome: DailyGenerationOutcome,
    sanitized_context: dict[str, Any],
    weather_snapshot: dict[str, Any] | None,
    merged_provider_meta: dict[str, Any],
    completed_at: datetime | None = None,
) -> DailyMealPlan | None:
    """Atomically publish or fail a generation attempt ONLY if generation_id still owns the row.

    If ownership was lost (``rowcount != 1``), rolls back without touching recipes or status
    and returns ``None``.
    """
    now_utc = _as_utc(completed_at or datetime.now(UTC))
    assert now_utc is not None

    update_values: dict[str, Any] = {
        "context_snapshot": sanitized_context,
        "weather_snapshot": weather_snapshot,
        "provider_metadata": merged_provider_meta,
        "generation_completed_at": now_utc,
        "active_generation_id": None,
        "lease_expires_at": None,
        "updated_at": now_utc,
    }
    if outcome.success:
        update_values["status"] = "ready"
        update_values["error_code"] = None
        update_values["error_message"] = None
    else:
        update_values["status"] = "failed"
        update_values["error_code"] = outcome.error_code
        update_values["error_message"] = outcome.error_message

    res = await db.execute(
        update(DailyMealPlan)
        .where(
            DailyMealPlan.id == plan_id,
            DailyMealPlan.status == "generating",
            DailyMealPlan.active_generation_id == generation_id,
        )
        .values(**update_values)
    )
    if (res.rowcount or 0) != 1:
        await db.rollback()
        logger.warning(
            "Ownership lost for daily_meal_plans id=%s generation_id=%s; aborting write-back.",
            plan_id,
            generation_id,
        )
        return None

    if outcome.success:
        await db.execute(
            delete(DailyMealPlanRecipe).where(
                DailyMealPlanRecipe.daily_meal_plan_id == plan_id
            )
        )
        for slot in outcome.slots:
            db.add(
                DailyMealPlanRecipe(
                    daily_meal_plan_id=plan_id,
                    meal_category=slot.meal_category,
                    position=slot.position,
                    recipe_data=slot.recipe_data,
                    availability_data=slot.availability_data,
                    quality_status=slot.quality_status,
                    quality_warnings=slot.quality_warnings,
                )
            )

    await db.commit()
    return (
        await db.execute(select(DailyMealPlan).where(DailyMealPlan.id == plan_id))
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
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
) -> DailyMealPlan | None:
    """Claim and generate the daily 8-recipe plan with heartbeat renewal and fenced writes."""
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
        utc_now=utc_now,
        stale_threshold_seconds=stale_threshold_seconds,
        lease_seconds=lease_seconds,
    )
    if claimed is None or claimed.active_generation_id is None:
        return None

    plan_id = claimed.id
    generation_id = claimed.active_generation_id
    claimed_meta = dict(claimed.provider_metadata or {})
    await db.commit()

    context = await build_daily_household_context(
        db,
        household_id,
        local_date=local_dt.date(),
        timezone_str=resolved_tz,
        weather_snapshot=weather_snapshot,
    )
    gen = generator or DailyMealPlanGenerator()

    ownership_lost = False
    stop_hb = asyncio.Event()
    gen_task = asyncio.create_task(gen.generate(context))

    async def _heartbeat_loop() -> None:
        nonlocal ownership_lost
        assert session_maker is not None
        interval = max(0.05, float(heartbeat_seconds))
        while not stop_hb.is_set():
            try:
                await asyncio.wait_for(stop_hb.wait(), timeout=interval)
                break
            except TimeoutError:
                pass
            if stop_hb.is_set():
                break
            try:
                still_owner = await renew_generation_lease(
                    session_maker,
                    plan_id=plan_id,
                    generation_id=generation_id,
                    lease_seconds=lease_seconds,
                )
            except Exception:
                # Transient DB error during heartbeat: log and retry on next interval;
                # final write-back remains strictly fenced by active_generation_id.
                logger.warning(
                    "Heartbeat DB error for daily_meal_plans id=%s generation_id=%s",
                    plan_id,
                    generation_id,
                    exc_info=True,
                )
                continue
            if not still_owner:
                ownership_lost = True
                logger.warning(
                    "Lease lost during heartbeat for daily_meal_plans id=%s generation_id=%s; "
                    "cancelling in-flight generation.",
                    plan_id,
                    generation_id,
                )
                gen_task.cancel()
                break

    hb_task: asyncio.Task[None] | None = None
    if session_maker is not None and heartbeat_seconds > 0:
        hb_task = asyncio.create_task(_heartbeat_loop())

    try:
        outcome = await gen_task
    except asyncio.CancelledError:
        if ownership_lost:
            return None
        raise
    finally:
        stop_hb.set()
        if hb_task is not None:
            hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb_task

    if ownership_lost:
        return None

    completed_at = datetime.now(UTC)
    sanitized_context = {
        "context_version": context.get("context_version"),
        "local_date": context.get("local_date"),
        "timezone": context.get("timezone"),
        "inventory_count": len(context.get("inventory_items") or []),
        "explicit_preferences": context.get("explicit_preferences"),
    }

    merged_provider_meta = dict(outcome.provider_metadata or {})
    merged_provider_meta["generation_id"] = str(generation_id)
    for diag_key in (
        "transient_retry_count",
        "stale_recovery_count",
        "interrupted_generation_count",
        "last_interrupted_at",
        "previous_generation_started_at",
        "previous_generation_id",
        "previous_error_code",
    ):
        if claimed_meta.get(diag_key) is not None:
            merged_provider_meta[diag_key] = claimed_meta[diag_key]

    return await finalize_generation_attempt(
        db,
        plan_id=plan_id,
        generation_id=generation_id,
        outcome=outcome,
        sanitized_context=sanitized_context,
        weather_snapshot=context.get("weather"),
        merged_provider_meta=merged_provider_meta,
        completed_at=completed_at,
    )


async def run_scheduler_tick(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    utc_now: datetime | None = None,
    generator: DailyMealPlanGenerator | None = None,
    stale_threshold_seconds: int = STALE_GENERATING_THRESHOLD_SECONDS,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
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
                    session_maker=session_maker,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                )
                if res is not None:
                    attempted += 1
        except Exception:
            logger.exception("Daily meal scheduler failed for household %s", hid)
    return attempted


class DailyMealPlanScheduler:
    """Persistent DB-backed asyncio scheduler for 03:00 household-local daily plans.

    Note: Runs in-process inside the web worker; not durable across worker crashes or
    restarts. Lease expiry (``lease_seconds``) ensures abandoned runs are safely
    recovered on a subsequent tick after restart.
    """

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: int = 60,
        stale_threshold_seconds: int = STALE_GENERATING_THRESHOLD_SECONDS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        self._session_maker = session_maker
        self._poll_interval_seconds = max(5, poll_interval_seconds)
        self._stale_threshold_seconds = max(60, stale_threshold_seconds)
        self._lease_seconds = max(10, lease_seconds)
        self._heartbeat_seconds = max(5, min(heartbeat_seconds, self._lease_seconds // 2))
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
                    lease_seconds=self._lease_seconds,
                    heartbeat_seconds=self._heartbeat_seconds,
                )
            except Exception:
                logger.exception("Unhandled error in DailyMealPlanScheduler tick")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval_seconds
                )
            except TimeoutError:
                continue
