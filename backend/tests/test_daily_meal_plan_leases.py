from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.modules.auth.models import Household, User
from app.modules.discovery.daily_generator import (
    DAILY_CATEGORIES,
    DailyGenerationOutcome,
    GeneratedDailySlot,
)
from app.modules.discovery.daily_models import DailyMealPlan, DailyMealPlanRecipe
from app.modules.discovery.daily_scheduler import (
    claim_daily_plan_slot,
    finalize_generation_attempt,
    renew_generation_lease,
    run_daily_plan_for_household,
    run_scheduler_tick,
)
from app.modules.recipes.models import HouseholdRecipePreference


def _build_dummy_slots(tag: str) -> list[GeneratedDailySlot]:
    slots: list[GeneratedDailySlot] = []
    for cat in DAILY_CATEGORIES:
        for pos in (1, 2):
            slots.append(
                GeneratedDailySlot(
                    meal_category=cat,
                    position=pos,
                    recipe_data={
                        "title": f"{tag}-{cat}-{pos}",
                        "description": f"Recipe {tag} for {cat} {pos}",
                        "servings": 2,
                        "prep_minutes": 15,
                        "structured_steps": [],
                    },
                    availability_data={"availability_bucket": "can_make_now"},
                    quality_status="valid",
                    quality_warnings=[],
                )
            )
    return slots


async def _setup_db(tmp_path: Path):
    db_file = tmp_path / "leases.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        u = User(clerk_user_id="clk_lease_test", email="lease@example.com", display_name="Lease")
        db.add(u)
        await db.flush()
        h = Household(name="Lease Kitchen", currency_code="INR", created_by=u.id)
        db.add(h)
        await db.flush()
        db.add(
            HouseholdRecipePreference(
                household_id=h.id,
                timezone="Asia/Kolkata",
                diet_type="vegetarian",
                preferences_completed_at=datetime.now(UTC),
            )
        )
        await db.commit()
        household_id = h.id

    return engine, session_maker, household_id


@pytest.mark.asyncio
async def test_two_concurrent_workers_claim_same_household_date_only_one_wins(
    tmp_path: Path,
) -> None:
    engine, session_maker, household_id = await _setup_db(tmp_path)
    try:
        today = date(2026, 11, 25)
        now = datetime(2026, 11, 25, 4, 0, tzinfo=UTC)

        async def worker_claim() -> tuple[UUID | None, UUID | None]:
            async with session_maker() as db:
                claimed = await claim_daily_plan_slot(
                    db,
                    household_id=household_id,
                    local_date=today,
                    timezone_str="Asia/Kolkata",
                    utc_now=now,
                    lease_seconds=120,
                )
                if claimed is None:
                    return None, None
                await db.commit()
                return claimed.id, claimed.active_generation_id

        res_a, res_b = await asyncio.gather(worker_claim(), worker_claim())
        winners = [r for r in (res_a, res_b) if r[0] is not None]
        losers = [r for r in (res_a, res_b) if r[0] is None]
        assert len(winners) == 1
        assert len(losers) == 1
        assert winners[0][1] is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_a_loses_ownership_to_worker_b_and_cannot_overwrite_ready_or_failed(
    tmp_path: Path,
) -> None:
    engine, session_maker, household_id = await _setup_db(tmp_path)
    try:
        today = date(2026, 11, 25)
        t0 = datetime(2026, 11, 25, 4, 0, tzinfo=UTC)
        gen_a = uuid4()
        gen_b = uuid4()

        # Worker A claims slot with 120s lease
        async with session_maker() as db:
            claimed_a = await claim_daily_plan_slot(
                db,
                household_id=household_id,
                local_date=today,
                timezone_str="Asia/Kolkata",
                utc_now=t0,
                lease_seconds=120,
                generation_id=gen_a,
            )
            assert claimed_a is not None
            plan_id = claimed_a.id
            await db.commit()

        # Before lease expiry, Worker B cannot reclaim
        async with session_maker() as db:
            denied = await claim_daily_plan_slot(
                db,
                household_id=household_id,
                local_date=today,
                timezone_str="Asia/Kolkata",
                utc_now=t0 + timedelta(seconds=60),
                lease_seconds=120,
                generation_id=gen_b,
            )
            assert denied is None

        # After lease expiry (t0 + 125s), Worker B reclaims ownership
        t_reclaim = t0 + timedelta(seconds=125)
        async with session_maker() as db:
            claimed_b = await claim_daily_plan_slot(
                db,
                household_id=household_id,
                local_date=today,
                timezone_str="Asia/Kolkata",
                utc_now=t_reclaim,
                lease_seconds=120,
                generation_id=gen_b,
            )
            assert claimed_b is not None
            assert claimed_b.active_generation_id == gen_b
            await db.commit()

        # Worker A's heartbeat now fails (ownership lost)
        assert (
            await renew_generation_lease(
                session_maker,
                plan_id=plan_id,
                generation_id=gen_a,
                lease_seconds=120,
                utc_now=t_reclaim + timedelta(seconds=5),
            )
            is False
        )

        # Worker A tries to mark the reclaimed row FAILED -> rejected by ownership fence
        async with session_maker() as db:
            stale_fail = await finalize_generation_attempt(
                db,
                plan_id=plan_id,
                generation_id=gen_a,
                outcome=DailyGenerationOutcome(
                    success=False,
                    error_code="VALIDATION_FAILED",
                    error_message="Worker A stale failure",
                ),
                sanitized_context={},
                weather_snapshot=None,
                merged_provider_meta={"worker": "A"},
                completed_at=t_reclaim + timedelta(seconds=10),
            )
            assert stale_fail is None

        # Worker A tries to mark the reclaimed row READY -> rejected by ownership fence
        async with session_maker() as db:
            stale_ready = await finalize_generation_attempt(
                db,
                plan_id=plan_id,
                generation_id=gen_a,
                outcome=DailyGenerationOutcome(success=True, slots=_build_dummy_slots("workerA")),
                sanitized_context={},
                weather_snapshot=None,
                merged_provider_meta={"worker": "A"},
                completed_at=t_reclaim + timedelta(seconds=12),
            )
            assert stale_ready is None

        # Worker B publishes its 8 recipes cleanly
        async with session_maker() as db:
            pub_b = await finalize_generation_attempt(
                db,
                plan_id=plan_id,
                generation_id=gen_b,
                outcome=DailyGenerationOutcome(success=True, slots=_build_dummy_slots("workerB")),
                sanitized_context={"owner": "B"},
                weather_snapshot=None,
                merged_provider_meta={"worker": "B"},
                completed_at=t_reclaim + timedelta(seconds=20),
            )
            assert pub_b is not None
            assert pub_b.status == "ready"
            assert pub_b.active_generation_id is None
            assert pub_b.lease_expires_at is None

        # Worker A again cannot overwrite Worker B's published READY plan
        async with session_maker() as db:
            late_a = await finalize_generation_attempt(
                db,
                plan_id=plan_id,
                generation_id=gen_a,
                outcome=DailyGenerationOutcome(
                    success=False,
                    error_code="LATE_ERROR",
                    error_message="Should never overwrite ready",
                ),
                sanitized_context={},
                weather_snapshot=None,
                merged_provider_meta={"worker": "A"},
            )
            assert late_a is None

            row = (
                await db.execute(select(DailyMealPlan).where(DailyMealPlan.id == plan_id))
            ).scalar_one()
            recipes = (
                await db.execute(
                    select(DailyMealPlanRecipe).where(
                        DailyMealPlanRecipe.daily_meal_plan_id == plan_id
                    )
                )
            ).scalars().all()
            assert row.status == "ready"
            assert row.error_code is None
            assert len(recipes) == 8
            assert all(r.recipe_data["title"].startswith("workerB-") for r in recipes)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeats_keep_long_running_generation_alive_and_prevent_reclaim(
    tmp_path: Path,
) -> None:
    engine, session_maker, household_id = await _setup_db(tmp_path)
    try:
        reclaim_attempted = False
        reclaim_result: DailyMealPlan | None = None

        class _SlowGenerator:
            async def generate(self, context: dict[str, Any]) -> DailyGenerationOutcome:
                nonlocal reclaim_attempted, reclaim_result
                # Wait long enough for multiple heartbeats (lease=1s, heartbeat=0.15s)
                await asyncio.sleep(0.55)
                async with session_maker() as check_db:
                    reclaim_attempted = True
                    reclaim_result = await claim_daily_plan_slot(
                        check_db,
                        household_id=household_id,
                        local_date=date(2026, 11, 25),
                        timezone_str="Asia/Kolkata",
                        lease_seconds=1,
                    )
                await asyncio.sleep(0.2)
                return DailyGenerationOutcome(
                    success=True,
                    slots=_build_dummy_slots("healthy"),
                    provider_metadata={"provider": "test"},
                )

        # Use real wall-clock UTC so heartbeat renewal advances lease_expires_at beyond now
        now_utc = datetime.now(UTC)
        async with session_maker() as db:
            result = await run_daily_plan_for_household(
                db,
                household_id,
                utc_now=now_utc,
                generator=_SlowGenerator(),  # type: ignore[arg-type]
                is_manual_regeneration=True,
                session_maker=session_maker,
                lease_seconds=1,
                heartbeat_seconds=0.15,
            )

        assert reclaim_attempted is True
        assert reclaim_result is None
        assert result is not None
        assert result.status == "ready"
        assert result.active_generation_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_transient_db_error_does_not_abort_and_loss_cancels_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_maker, household_id = await _setup_db(tmp_path)
    try:
        # Part 1: Transient DB error on first heartbeat does not crash healthy worker
        hb_calls = 0
        orig_renew = renew_generation_lease

        async def flaky_renew(*args: Any, **kwargs: Any) -> bool:
            nonlocal hb_calls
            hb_calls += 1
            if hb_calls == 1:
                raise RuntimeError("simulated transient DB error")
            return await orig_renew(*args, **kwargs)

        monkeypatch.setattr(
            "app.modules.discovery.daily_scheduler.renew_generation_lease", flaky_renew
        )

        class _MediumGenerator:
            async def generate(self, context: dict[str, Any]) -> DailyGenerationOutcome:
                await asyncio.sleep(0.35)
                return DailyGenerationOutcome(
                    success=True,
                    slots=_build_dummy_slots("flaky-hb"),
                    provider_metadata={"provider": "test"},
                )

        async with session_maker() as db:
            res = await run_daily_plan_for_household(
                db,
                household_id,
                utc_now=datetime.now(UTC),
                generator=_MediumGenerator(),  # type: ignore[arg-type]
                is_manual_regeneration=True,
                session_maker=session_maker,
                lease_seconds=5,
                heartbeat_seconds=0.1,
            )
        assert hb_calls >= 2
        assert res is not None
        assert res.status == "ready"

        # Part 2: Confirmed ownership loss on heartbeat cancels in-flight generator
        cancelled_flag = False

        async def lost_renew(*args: Any, **kwargs: Any) -> bool:
            return False

        monkeypatch.setattr(
            "app.modules.discovery.daily_scheduler.renew_generation_lease", lost_renew
        )

        class _CancellableGenerator:
            async def generate(self, context: dict[str, Any]) -> DailyGenerationOutcome:
                nonlocal cancelled_flag
                try:
                    await asyncio.sleep(5.0)
                except asyncio.CancelledError:
                    cancelled_flag = True
                    raise
                return DailyGenerationOutcome(success=True, slots=_build_dummy_slots("never"))

        tomorrow = datetime.now(UTC) + timedelta(days=1)
        async with session_maker() as db:
            res2 = await run_daily_plan_for_household(
                db,
                household_id,
                utc_now=tomorrow,
                generator=_CancellableGenerator(),  # type: ignore[arg-type]
                is_manual_regeneration=True,
                session_maker=session_maker,
                lease_seconds=5,
                heartbeat_seconds=0.1,
            )
        assert cancelled_flag is True
        assert res2 is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_after_simulated_worker_interruption_and_restart(tmp_path: Path) -> None:
    engine, session_maker, household_id = await _setup_db(tmp_path)
    try:
        due_utc = datetime(2026, 11, 25, 4, 0, tzinfo=UTC)
        crashed_gen_id = uuid4()

        # Simulate a crashed/restarted worker that left a generating row whose lease expired
        async with session_maker() as db:
            db.add(
                DailyMealPlan(
                    household_id=household_id,
                    local_date=date(2026, 11, 25),
                    timezone="Asia/Kolkata",
                    status="generating",
                    auto_attempted=True,
                    manual_regeneration_count=0,
                    context_snapshot={},
                    provider_metadata={"provider": "huggingface"},
                    generation_started_at=due_utc - timedelta(minutes=5),
                    active_generation_id=crashed_gen_id,
                    last_heartbeat_at=due_utc - timedelta(minutes=4),
                    lease_expires_at=due_utc - timedelta(minutes=2),
                )
            )
            await db.commit()

        class _RecoveryGenerator:
            async def generate(self, context: dict[str, Any]) -> DailyGenerationOutcome:
                return DailyGenerationOutcome(
                    success=True,
                    slots=_build_dummy_slots("recovered"),
                    provider_metadata={"provider": "test"},
                )

        attempted = await run_scheduler_tick(
            session_maker,
            utc_now=due_utc,
            generator=_RecoveryGenerator(),  # type: ignore[arg-type]
            lease_seconds=120,
            heartbeat_seconds=30,
        )
        assert attempted == 1

        async with session_maker() as db:
            plan = (
                await db.execute(
                    select(DailyMealPlan).where(
                        DailyMealPlan.household_id == household_id,
                        DailyMealPlan.local_date == date(2026, 11, 25),
                    )
                )
            ).scalar_one()
            recipes = (
                await db.execute(
                    select(DailyMealPlanRecipe).where(
                        DailyMealPlanRecipe.daily_meal_plan_id == plan.id
                    )
                )
            ).scalars().all()
            assert plan.status == "ready"
            assert plan.active_generation_id is None
            assert plan.lease_expires_at is None
            assert plan.provider_metadata.get("stale_recovery_count") == 1
            assert plan.provider_metadata.get("previous_generation_id") == str(crashed_gen_id)
            assert plan.provider_metadata.get("generation_id") != str(crashed_gen_id)
            assert len(recipes) == 8
    finally:
        await engine.dispose()


def test_alembic_0022_upgrade_and_downgrade_on_fresh_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "alembic_0022.db"
    backend_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    url = f"sqlite+aiosqlite:///{db_path}"
    env["DATABASE_URL"] = url
    env["DATABASE_MIGRATION_URL"] = url

    up = subprocess.run(
        [str(backend_dir / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert up.returncode == 0, up.stderr

    down = subprocess.run(
        [str(backend_dir / ".venv/bin/alembic"), "downgrade", "0021_recommendation_mode"],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert down.returncode == 0, down.stderr

    reup = subprocess.run(
        [str(backend_dir / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert reup.returncode == 0, reup.stderr
