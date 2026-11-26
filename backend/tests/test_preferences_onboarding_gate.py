from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.core.db import Base
from app.modules.auth.models import Household, HouseholdMember, User
from app.modules.discovery.daily_generator import (
    DailyGenerationOutcome,
    DailyMealPlanGenerator,
    GeneratedDailySlot,
)
from app.modules.discovery.daily_models import DailyMealPlan
from app.modules.discovery.daily_scheduler import run_daily_plan_for_household, run_scheduler_tick
from app.modules.recipes.models import HouseholdRecipePreference
from tests.conftest import EphemeralJWKSProvider


class _CountingFakeGenerator(DailyMealPlanGenerator):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, context: dict[str, Any]) -> DailyGenerationOutcome:
        self.calls += 1
        slots = [
            GeneratedDailySlot(
                meal_category=cat,
                position=pos,
                recipe_data={"slug": f"{cat}-{pos}", "title": f"Test {cat} {pos}"},
                availability_data={"availability_bucket": "explore_more"},
                quality_status="valid",
                quality_warnings=[],
            )
            for cat in ("breakfast", "lunch", "snacks", "dinner")
            for pos in (1, 2)
        ]
        return DailyGenerationOutcome(
            success=True,
            slots=slots,
            provider_metadata={"provider": "fake"},
        )


@pytest.mark.asyncio
async def test_new_household_preferences_gate_put_patch_and_manual_regen_409(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token_a = jwks_provider.make_token(sub="user_onboard_a", email="a@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 1. First-time sign-in auto-provisions household
    me = await client.get("/api/v1/auth/me", headers=headers_a)
    assert me.status_code == 200
    hh_id = me.json()["active_household"]["id"]
    hh_headers_a = {**headers_a, "X-Household-Id": hh_id}

    # 2. Default preferences without explicit save -> onboarding_completed=False
    prefs = await client.get("/api/v1/recipes/preferences", headers=hh_headers_a)
    assert prefs.status_code == 200
    assert prefs.json()["onboarding_completed"] is False
    assert prefs.json()["preferences_completed_at"] is None

    # 3. Daily plan returns pending + onboarding_required=True
    dp = await client.get("/api/v1/discover/daily-plan", headers=hh_headers_a)
    assert dp.status_code == 200
    assert dp.json()["status"] == "pending"
    assert dp.json()["onboarding_required"] is True

    # 4. PATCH recommendation-mode must NOT mark onboarding complete
    patch_res = await client.patch(
        "/api/v1/recipes/preferences/recommendation-mode",
        headers=hh_headers_a,
        json={"recommendation_mode": "new", "dismiss_personalisation_prompt": True},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["onboarding_completed"] is False
    assert patch_res.json()["preferences_completed_at"] is None

    # 5. Manual regeneration returns 409 PREFERENCES_REQUIRED
    regen = await client.post("/api/v1/discover/daily-plan/regenerate", headers=hh_headers_a)
    assert regen.status_code == 409
    assert regen.json()["error"]["code"] == "PREFERENCES_REQUIRED"

    # 6. PUT /api/v1/recipes/preferences atomically completes onboarding
    put_res = await client.put(
        "/api/v1/recipes/preferences",
        headers=hh_headers_a,
        json={
            "timezone": "Asia/Kolkata",
            "diet_type": "vegetarian",
            "dietary_restrictions": [],
            "preferred_cuisines": ["Indian"],
            "preferred_meal_types": ["breakfast", "lunch", "dinner"],
        },
    )
    assert put_res.status_code == 200
    assert put_res.json()["onboarding_completed"] is True
    assert put_res.json()["preferences_completed_at"] is not None

    # 7. Daily plan now shows onboarding_required=False
    dp_after = await client.get("/api/v1/discover/daily-plan", headers=hh_headers_a)
    assert dp_after.status_code == 200
    assert dp_after.json()["onboarding_required"] is False


@pytest.mark.asyncio
async def test_scheduler_skips_incomplete_and_generates_with_empty_inventory_after_save(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sched_gate.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)
    gen = _CountingFakeGenerator()

    async with sm() as db:
        u1 = User(
            id=uuid4(), clerk_user_id="u_sched_1", email="u1@example.com", display_name="User 1"
        )
        u2 = User(
            id=uuid4(), clerk_user_id="u_sched_2", email="u2@example.com", display_name="User 2"
        )
        db.add_all([u1, u2])
        await db.flush()

        # Household 1: brand new, no preferences, 0 inventory
        hh_new = Household(id=uuid4(), name="New Home", currency_code="INR", created_by=u1.id)
        # Household 2: existing household with an already-ready plan (no pref row)
        hh_existing = Household(
            id=uuid4(), name="Existing Home", currency_code="INR", created_by=u2.id
        )
        db.add_all([hh_new, hh_existing])
        await db.flush()
        db.add_all(
            [
                HouseholdMember(household_id=hh_new.id, user_id=u1.id, role="owner"),
                HouseholdMember(household_id=hh_existing.id, user_id=u2.id, role="owner"),
            ]
        )
        db.add(
            DailyMealPlan(
                id=uuid4(),
                household_id=hh_existing.id,
                local_date=date(2026, 11, 24),
                timezone="Asia/Kolkata",
                status="ready",
                auto_attempted=True,
            )
        )
        await db.commit()
        hh_new_id: UUID = hh_new.id
        hh_existing_id: UUID = hh_existing.id

    # Tick 1: hh_new skipped (0 LLM calls for it), hh_existing generates today's plan
    attempted = await run_scheduler_tick(sm, utc_now=now, generator=gen)
    assert attempted == 1
    assert gen.calls == 1

    async with sm() as db:
        new_plans = (
            await db.execute(select(DailyMealPlan).where(DailyMealPlan.household_id == hh_new_id))
        ).scalars().all()
        assert new_plans == []

        existing_today = (
            await db.execute(
                select(DailyMealPlan).where(
                    DailyMealPlan.household_id == hh_existing_id,
                    DailyMealPlan.local_date == date(2026, 11, 25),
                )
            )
        ).scalar_one_or_none()
        assert existing_today is not None
        assert existing_today.status == "ready"

        # Now explicitly save preferences for hh_new (still 0 inventory!)
        db.add(
            HouseholdRecipePreference(
                household_id=hh_new_id,
                timezone="Asia/Kolkata",
                diet_type="vegetarian",
                preferences_completed_at=now,
            )
        )
        await db.commit()

    # Tick 2: hh_new now generates cleanly with empty inventory
    async with sm() as db:
        res = await run_daily_plan_for_household(db, hh_new_id, utc_now=now, generator=gen)
        assert res is not None
        assert res.status == "ready"
    assert gen.calls == 2

    await engine.dispose()


def test_alembic_0023_backfills_existing_preferences_without_overwriting_and_downgrades(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "mig_0023.db"
    url = f"sqlite+aiosqlite:///{db_file}"
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", url)

    # Upgrade to 0022 first and insert an existing household + preference row
    command.upgrade(alembic_cfg, "0022_daily_plan_generation_leases")
    conn = sqlite3.connect(str(db_file))
    uid = uuid4().hex
    hh_with_pref = uuid4().hex
    hh_without_pref = uuid4().hex
    ts = "2026-11-20 10:00:00"
    conn.execute(
        "INSERT INTO users (id, clerk_user_id, display_name, created_at, updated_at) "
        "VALUES (?, ?, 'Mig User', ?, ?)",
        (uid, "clerk_mig_test", ts, ts),
    )
    conn.execute(
        "INSERT INTO households (id, name, currency_code, created_by, created_at, updated_at) "
        "VALUES (?, 'Home A', 'INR', ?, ?, ?)",
        (hh_with_pref, uid, ts, ts),
    )
    conn.execute(
        "INSERT INTO households (id, name, currency_code, created_by, created_at, updated_at) "
        "VALUES (?, 'Home B', 'INR', ?, ?, ?)",
        (hh_without_pref, uid, ts, ts),
    )
    conn.execute(
        "INSERT INTO household_recipe_preferences "
        "(household_id, timezone, diet_type, dietary_restrictions, preferred_cuisines, "
        "preferred_meal_types, recommendation_mode, updated_at) "
        "VALUES (?, 'Asia/Kolkata', 'vegan', '[]', '[\"South Indian\"]', '[]', 'balanced', ?)",
        (hh_with_pref, ts),
    )
    conn.commit()
    conn.close()

    # Upgrade to 0023
    command.upgrade(alembic_cfg, "0023_preferences_onboarding_completion")
    conn = sqlite3.connect(str(db_file))
    rows = conn.execute(
        "SELECT household_id, diet_type, preferred_cuisines, preferences_completed_at "
        "FROM household_recipe_preferences"
    ).fetchall()
    conn.close()
    assert len(rows) == 1  # Did NOT blindly create a synthetic row for hh_without_pref
    assert rows[0][0] == hh_with_pref
    assert rows[0][1] == "vegan"
    assert rows[0][2] == '["South Indian"]'
    assert rows[0][3] is not None

    # Downgrade back to 0022 cleanly
    command.downgrade(alembic_cfg, "0022_daily_plan_generation_leases")
    conn = sqlite3.connect(str(db_file))
    cols = [
        r[1] for r in conn.execute("PRAGMA table_info(household_recipe_preferences)").fetchall()
    ]
    conn.close()
    assert "preferences_completed_at" not in cols
