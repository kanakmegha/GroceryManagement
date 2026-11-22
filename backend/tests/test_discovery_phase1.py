from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.core.db import build_async_engine, build_session_maker
from app.modules.auth.models import Household, User
from app.modules.catalog.ingredient_catalogue import get_ingredient_catalogue
from app.modules.discovery.catalogue_data import CURATED_DISH_CATALOGUE
from app.modules.discovery.catalogue_seed import (
    CatalogueValidationError,
    compute_dish_content_hash,
    is_verified_substitution,
    seed_dish_catalogue,
    validate_dish_catalogue_entry,
)
from app.modules.discovery.coordination import (
    VideoRefreshCoordinator,
    YouTubeQuotaGuard,
    derive_quota_project_key,
)
from app.modules.discovery.models import (
    Dish,
    DishFeedback,
    DishIngredient,
    DishVideoCache,
    SavedDish,
    VideoRecord,
)
from app.modules.discovery.schemas import DishCatalogueEntry

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_alembic(db_url: str, action: str, target: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    if action == "upgrade":
        command.upgrade(cfg, target)
    elif action == "downgrade":
        command.downgrade(cfg, target)


@pytest.mark.asyncio
async def test_migration_0019_upgrade_downgrade_and_rls_ddl(tmp_path: Path) -> None:
    db_path = tmp_path / "migration_0019.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")

    engine = build_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            expected = {
                "dishes",
                "dish_ingredients",
                "video_records",
                "dish_video_cache",
                "youtube_quota_usage",
                "saved_dishes",
                "dish_feedback",
            }
            assert expected.issubset(tables)
    finally:
        await engine.dispose()

    await asyncio.to_thread(
        _run_alembic, db_url, "downgrade", "0018_purchase_item_resolution_meta"
    )
    engine_after_down = build_async_engine(db_url)
    try:
        async with engine_after_down.connect() as conn:
            tables_down = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            assert "dishes" not in tables_down
            assert "saved_dishes" not in tables_down
    finally:
        await engine_after_down.dispose()

    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")

    migration_text = (
        BACKEND_DIR / "alembic" / "versions" / "0019_pantry_discovery_tables.py"
    ).read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in migration_text
    assert "FORCE ROW LEVEL SECURITY" in migration_text
    assert "app.current_household_id" in migration_text
    assert "tenant_isolation_saved_dishes" in migration_text
    assert "tenant_isolation_dish_feedback" in migration_text


@pytest.mark.asyncio
async def test_models_constraints_and_foreign_keys(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'constraints.db'}"
    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            dish = Dish(slug="test-dish", canonical_name="Test Dish")
            db.add(dish)
            await db.flush()

            db.add(Dish(slug="test-dish", canonical_name="Duplicate Slug"))
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()

        async with session_maker() as db:
            dish = Dish(slug="fk-dish", canonical_name="FK Dish")
            db.add(dish)
            await db.flush()

            bad_ing = DishIngredient(
                dish_id=dish.id,
                ingredient_name="Invalid Mode",
                requirement_mode="invalid_mode",
                requirement_type="required",
            )
            db.add(bad_ing)
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()

        async with session_maker() as db:
            vid1 = VideoRecord(
                provider="youtube",
                external_video_id="abc123",
                title="Video 1",
                channel_name="Chef A",
                watch_url="https://www.youtube.com/watch?v=abc123",
            )
            db.add(vid1)
            await db.flush()

            vid2 = VideoRecord(
                provider="youtube",
                external_video_id="abc123",
                title="Video 2",
                channel_name="Chef B",
                watch_url="https://www.youtube.com/watch?v=abc123",
            )
            db.add(vid2)
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()
    finally:
        await engine.dispose()


def test_catalogue_validation_rules_and_substitutions() -> None:
    catalogue = get_ingredient_catalogue()
    for entry in CURATED_DISH_CATALOGUE:
        validate_dish_catalogue_entry(entry, catalogue)

    h1 = compute_dish_content_hash(CURATED_DISH_CATALOGUE[0])
    h2 = compute_dish_content_hash(CURATED_DISH_CATALOGUE[0])
    assert h1 == h2
    assert len(h1) == 64

    # exact_variant cannot use __unspecified
    bad_exact = DishCatalogueEntry(
        slug="bad-exact",
        canonical_name="Bad Exact",
        ingredients=[
            {
                "ingredient_name": "Onion",
                "requirement_mode": "exact_variant",
                "requirement_type": "required",
                "variant_id": "onion__unspecified",
                "family_id": "onion",
            }
        ],
    )
    with pytest.raises(CatalogueValidationError, match="unspecified"):
        validate_dish_catalogue_entry(bad_exact, catalogue)

    # generic_ingredient cannot require a specific variant
    bad_generic = DishCatalogueEntry(
        slug="bad-generic",
        canonical_name="Bad Generic",
        ingredients=[
            {
                "ingredient_name": "Basmati",
                "requirement_mode": "generic_ingredient",
                "requirement_type": "required",
                "variant_id": "rice__basmati_rice",
                "family_id": "rice",
            }
        ],
    )
    with pytest.raises(CatalogueValidationError, match="specific variant"):
        validate_dish_catalogue_entry(bad_generic, catalogue)

    # compatible_variant_ids must belong to family_id
    bad_compat = DishCatalogueEntry(
        slug="bad-compat",
        canonical_name="Bad Compat",
        ingredients=[
            {
                "ingredient_name": "Onion",
                "requirement_mode": "compatible_family",
                "requirement_type": "required",
                "family_id": "onion",
                "compatible_variant_ids": ["tomato__desi_tomato"],
            }
        ],
    )
    with pytest.raises(CatalogueValidationError, match="compatible_variant_id"):
        validate_dish_catalogue_entry(bad_compat, catalogue)

    assert is_verified_substitution({"verified": True, "substitute_name": "Tofu"}) is True
    assert is_verified_substitution({"verified": False, "substitute_name": "Guess"}) is False

    # Valid original_variant_id passes
    valid_orig_sub = DishCatalogueEntry(
        slug="valid-orig-sub",
        canonical_name="Valid Orig Sub",
        ingredients=[
            {
                "ingredient_name": "Basmati Rice",
                "requirement_mode": "exact_variant",
                "requirement_type": "required",
                "variant_id": "rice__basmati_rice",
                "family_id": "rice",
                "substitutions": [
                    {
                        "original_variant_id": "rice__basmati_rice",
                        "substitute_variant_id": "rice__sona_masoori_rice",
                        "substitute_name": "Sona Masoori Rice",
                        "verified": True,
                    }
                ],
            }
        ],
    )
    validate_dish_catalogue_entry(valid_orig_sub, catalogue)

    # Invalid original_variant_id is rejected
    bad_orig_sub = DishCatalogueEntry(
        slug="bad-orig-sub",
        canonical_name="Bad Orig Sub",
        ingredients=[
            {
                "ingredient_name": "Basmati Rice",
                "requirement_mode": "exact_variant",
                "requirement_type": "required",
                "variant_id": "rice__basmati_rice",
                "family_id": "rice",
                "substitutions": [
                    {
                        "original_variant_id": "rice__nonexistent_grain",
                        "substitute_variant_id": "rice__sona_masoori_rice",
                        "substitute_name": "Sona Masoori Rice",
                        "verified": True,
                    }
                ],
            }
        ],
    )
    with pytest.raises(CatalogueValidationError, match="original_variant_id"):
        validate_dish_catalogue_entry(bad_orig_sub, catalogue)


@pytest.mark.asyncio
async def test_idempotent_seeding_rollback_soft_deactivation_and_user_preservation(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}"
    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            res1 = await seed_dish_catalogue(db)
            await db.commit()
            assert res1.inserted == len(CURATED_DISH_CATALOGUE)
            assert res1.updated == 0
            assert res1.deactivated == 0

            res2 = await seed_dish_catalogue(db)
            await db.commit()
            assert res2.inserted == 0
            assert res2.updated == 0
            assert res2.unchanged == len(CURATED_DISH_CATALOGUE)

        # Save a dish & record feedback for a household
        async with session_maker() as db:
            owner = User(clerk_user_id="clerk_seed_owner", display_name="Seed Owner")
            db.add(owner)
            await db.flush()
            hh = Household(name="Test Kitchen", created_by=owner.id)
            db.add(hh)
            await db.flush()
            palak = (
                await db.execute(select(Dish).where(Dish.slug == "palak-paneer"))
            ).scalar_one()
            db.add(SavedDish(household_id=hh.id, dish_id=palak.id))
            db.add(DishFeedback(household_id=hh.id, dish_id=palak.id, action="like", rating=5))
            await db.commit()

        # Rollback on invalid catalogue entry leaves DB intact
        bad_entry = DishCatalogueEntry(
            slug="bad-entry",
            canonical_name="Bad Entry",
            ingredients=[
                {
                    "ingredient_name": "Unknown",
                    "requirement_mode": "compatible_family",
                    "requirement_type": "required",
                    "family_id": "nonexistent_family_xyz",
                }
            ],
        )
        async with session_maker() as db:
            with pytest.raises(CatalogueValidationError):
                await seed_dish_catalogue(db, entries=[CURATED_DISH_CATALOGUE[0], bad_entry])

        # Soft deactivation when a dish is removed from active catalogue preserves user data
        subset = [d for d in CURATED_DISH_CATALOGUE if d.slug != "palak-paneer"]
        async with session_maker() as db:
            res_sub = await seed_dish_catalogue(db, entries=subset)
            await db.commit()
            assert res_sub.deactivated == 1

            palak_after = (
                await db.execute(select(Dish).where(Dish.slug == "palak-paneer"))
            ).scalar_one()
            assert palak_after.is_active is False

            saved_count = len((await db.execute(select(SavedDish))).scalars().all())
            feedback_count = len((await db.execute(select(DishFeedback))).scalars().all())
            assert saved_count == 1
            assert feedback_count == 1

            # RESTRICT prevents hard-deleting a dish referenced by saved_dishes/dish_feedback
            await db.delete(palak_after)
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_distributed_lease_claim_expiry_and_failure_release(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}"
    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            dish = Dish(slug="lease-dish", canonical_name="Lease Dish")
            db.add(dish)
            await db.commit()
            dish_id = dish.id

        coord_worker_a = VideoRefreshCoordinator(
            session_maker, lease_ttl_seconds=30, cooldown_seconds=900
        )
        coord_worker_b = VideoRefreshCoordinator(
            session_maker, lease_ttl_seconds=30, cooldown_seconds=900
        )
        now = datetime.now(UTC)

        claim_a = await coord_worker_a.try_claim_refresh_lease(
            dish_id=dish_id,
            query_hash="q1",
            query_text="lease dish recipe",
            now=now,
        )
        assert claim_a.acquired is True
        assert claim_a.lock_token is not None

        # Competing worker B cannot claim while lease is active
        claim_b = await coord_worker_b.try_claim_refresh_lease(
            dish_id=dish_id,
            query_hash="q1",
            query_text="lease dish recipe",
            now=now + timedelta(seconds=5),
        )
        assert claim_b.acquired is False

        # After lease TTL expires (crash recovery), worker B can reclaim
        claim_b_after_ttl = await coord_worker_b.try_claim_refresh_lease(
            dish_id=dish_id,
            query_hash="q1",
            query_text="lease dish recipe",
            now=now + timedelta(seconds=35),
        )
        assert claim_b_after_ttl.acquired is True
        assert claim_b_after_ttl.lock_token != claim_a.lock_token

        # Failure release clears lock and sets cooldown
        released = await coord_worker_b.release_refresh_lease_on_failure(
            dish_id=dish_id,
            query_hash="q1",
            lock_token=claim_b_after_ttl.lock_token,  # type: ignore[arg-type]
            apply_cooldown=True,
            now=now + timedelta(seconds=36),
        )
        assert released is True

        # During cooldown, new claim is blocked
        claim_during_cooldown = await coord_worker_a.try_claim_refresh_lease(
            dish_id=dish_id,
            query_hash="q1",
            query_text="lease dish recipe",
            now=now + timedelta(seconds=60),
        )
        assert claim_during_cooldown.acquired is False
        assert claim_during_cooldown.in_cooldown is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_atomic_quota_reservation_concurrency_and_non_secret_key(tmp_path: Path) -> None:
    raw_secret = "AIzaSySuperSecretKeyDoNotPersist123"
    key = derive_quota_project_key(api_key=raw_secret)
    assert raw_secret not in key
    assert key.startswith("key_sha256_")

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'quota.db'}"
    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        guard = YouTubeQuotaGuard(session_maker, daily_budget=300)
        fixed_now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)

        async def _attempt() -> bool:
            res = await guard.reserve_quota(
                quota_project_key=key,
                cost=100,
                now=fixed_now,
            )
            return res.reserved

        # Run 6 concurrent attempts of 100 units against a 300-unit budget
        results = await asyncio.gather(*[_attempt() for _ in range(6)])
        assert sum(1 for r in results if r) == 3

        async with session_maker() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT units_used, search_calls FROM youtube_quota_usage "
                        "WHERE quota_project_key = :k"
                    ),
                    {"k": key},
                )
            ).one()
            assert row[0] == 300
            assert row[1] == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_household_isolation_saved_dishes_and_feedback(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tenancy.db'}"
    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            owner = User(clerk_user_id="clerk_tenancy_owner", display_name="Tenancy Owner")
            db.add(owner)
            await db.flush()
            hh_a = Household(name="Household A", created_by=owner.id)
            hh_b = Household(name="Household B", created_by=owner.id)
            dish = Dish(slug="tenancy-dish", canonical_name="Tenancy Dish")
            db.add_all([hh_a, hh_b, dish])
            await db.flush()

            db.add(SavedDish(household_id=hh_a.id, dish_id=dish.id))
            db.add(DishFeedback(household_id=hh_a.id, dish_id=dish.id, action="cooked", rating=5))
            await db.commit()

            a_saved = (
                await db.execute(select(SavedDish).where(SavedDish.household_id == hh_a.id))
            ).scalars().all()
            b_saved = (
                await db.execute(select(SavedDish).where(SavedDish.household_id == hh_b.id))
            ).scalars().all()
            b_feedback = (
                await db.execute(select(DishFeedback).where(DishFeedback.household_id == hh_b.id))
            ).scalars().all()

            assert len(a_saved) == 1
            assert len(b_saved) == 0
            assert len(b_feedback) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_coordination_session_ownership_preserves_request_transaction(
    tmp_path: Path,
) -> None:
    """
    Coordination and quota operations use a dedicated short-lived session and never
    commit, close, or clear an unrelated request-scoped session's transaction.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'session_ownership.db'}"
    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as setup_db:
            owner = User(clerk_user_id="clerk_rls_owner", display_name="RLS Owner")
            setup_db.add(owner)
            await setup_db.flush()
            hh = Household(name="RLS Kitchen", created_by=owner.id)
            dish = Dish(slug="rls-dish", canonical_name="RLS Dish")
            setup_db.add_all([hh, dish])
            await setup_db.commit()
            hh_id = hh.id
            dish_id = dish.id

        coord = VideoRefreshCoordinator(session_maker)
        guard = YouTubeQuotaGuard(session_maker, daily_budget=500)

        async with session_maker() as request_db:
            # Start an active request transaction (as get_household_context does on read)
            # and stage an uncommitted household-scoped object in the request session.
            await request_db.execute(select(SavedDish).where(SavedDish.household_id == hh_id))
            pending_saved = SavedDish(household_id=hh_id, dish_id=dish_id)
            request_db.add(pending_saved)
            assert request_db.in_transaction() is True
            assert pending_saved in request_db.new

            # Execute coordination + quota reservation (each commits its own dedicated session)
            claim = await coord.try_claim_refresh_lease(
                dish_id=dish_id,
                query_hash="q_rls",
                query_text="rls dish recipe",
            )
            assert claim.acquired is True
            quota_res = await guard.reserve_quota(quota_project_key="proj_rls", cost=100)
            assert quota_res.reserved is True
            completed = await coord.complete_refresh_lease(
                dish_id=dish_id,
                query_hash="q_rls",
                lock_token=claim.lock_token,  # type: ignore[arg-type]
                video_record_ids=["vid_1"],
            )
            assert completed is True

            # Request session transaction is still open and pending_saved was NOT committed
            assert request_db.in_transaction() is True
            assert pending_saved in request_db.new
            async with session_maker() as observer_db:
                external_saved = (await observer_db.execute(select(SavedDish))).scalars().all()
                assert len(external_saved) == 0

            # Request session itself can still commit its own work cleanly afterward
            await request_db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingredient_reconciliation_after_catalogue_update(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'reconcile.db'}"
    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    initial_entry = DishCatalogueEntry(
        slug="reconcile-dal",
        canonical_name="Reconcile Dal",
        ingredients=[
            {
                "ingredient_name": "Split Toor Dal",
                "requirement_mode": "exact_variant",
                "requirement_type": "required",
                "variant_id": "toor_dal__split_toor_dal",
                "family_id": "toor_dal",
                "sort_order": 1,
            },
            {
                "ingredient_name": "Turmeric",
                "requirement_mode": "compatible_family",
                "requirement_type": "required",
                "family_id": "turmeric",
                "sort_order": 2,
            },
        ],
    )
    updated_entry = DishCatalogueEntry(
        slug="reconcile-dal",
        canonical_name="Reconcile Dal",
        ingredients=[
            {
                "ingredient_name": "Split Yellow Moong Dal",
                "requirement_mode": "exact_variant",
                "requirement_type": "required",
                "variant_id": "moong__split_yellow_moong_dal",
                "family_id": "moong",
                "sort_order": 1,
            },
            {
                "ingredient_name": "Cumin",
                "requirement_mode": "compatible_family",
                "requirement_type": "optional",
                "family_id": "cumin",
                "sort_order": 2,
            },
        ],
    )
    assert compute_dish_content_hash(initial_entry) != compute_dish_content_hash(updated_entry)

    try:
        async with session_maker() as db:
            res1 = await seed_dish_catalogue(db, entries=[initial_entry])
            await db.commit()
            assert res1.inserted == 1

            owner = User(clerk_user_id="clerk_reconcile", display_name="Reconcile User")
            db.add(owner)
            await db.flush()
            hh = Household(name="Reconcile Kitchen", created_by=owner.id)
            db.add(hh)
            await db.flush()
            dish = (
                await db.execute(select(Dish).where(Dish.slug == "reconcile-dal"))
            ).scalar_one()
            dish_id = dish.id
            db.add(SavedDish(household_id=hh.id, dish_id=dish_id))
            db.add(DishFeedback(household_id=hh.id, dish_id=dish_id, action="like", rating=4))
            await db.commit()

        async with session_maker() as db:
            res2 = await seed_dish_catalogue(db, entries=[updated_entry])
            await db.commit()
            assert res2.updated == 1
            assert res2.inserted == 0

            dish_after = (
                await db.execute(select(Dish).where(Dish.slug == "reconcile-dal"))
            ).scalar_one()
            assert dish_after.id == dish_id

            ings = (
                await db.execute(
                    select(DishIngredient)
                    .where(DishIngredient.dish_id == dish_id)
                    .order_by(DishIngredient.sort_order)
                )
            ).scalars().all()
            assert [i.ingredient_name for i in ings] == ["Split Yellow Moong Dal", "Cumin"]
            assert [i.variant_id for i in ings] == ["moong__split_yellow_moong_dal", None]
            assert [i.requirement_type for i in ings] == ["required", "optional"]

            assert len((await db.execute(select(SavedDish))).scalars().all()) == 1
            assert len((await db.execute(select(DishFeedback))).scalars().all()) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_worker_cannot_overwrite_newer_worker(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'stale_worker.db'}"
    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            dish = Dish(slug="stale-worker-dish", canonical_name="Stale Worker Dish")
            db.add(dish)
            await db.commit()
            dish_id = dish.id

        worker_a = VideoRefreshCoordinator(session_maker, lease_ttl_seconds=30)
        worker_b = VideoRefreshCoordinator(session_maker, lease_ttl_seconds=30)
        now = datetime.now(UTC)

        claim_a = await worker_a.try_claim_refresh_lease(
            dish_id=dish_id,
            query_hash="q_stale",
            query_text="stale worker dish",
            now=now,
        )
        assert claim_a.acquired is True
        assert claim_a.lock_token is not None

        # Lease expires; Worker B acquires a fresh lease
        claim_b = await worker_b.try_claim_refresh_lease(
            dish_id=dish_id,
            query_hash="q_stale",
            query_text="stale worker dish",
            now=now + timedelta(seconds=35),
        )
        assert claim_b.acquired is True
        assert claim_b.lock_token is not None
        assert claim_b.lock_token != claim_a.lock_token

        # Stale Worker A finishes late and attempts completion with old token -> rejected
        completed_a = await worker_a.complete_refresh_lease(
            dish_id=dish_id,
            query_hash="q_stale",
            lock_token=claim_a.lock_token,
            video_record_ids=["stale_vid_1"],
            now=now + timedelta(seconds=36),
        )
        assert completed_a is False

        async with session_maker() as db:
            cache_mid = (
                await db.execute(
                    select(DishVideoCache).where(
                        DishVideoCache.dish_id == dish_id,
                        DishVideoCache.query_hash == "q_stale",
                    )
                )
            ).scalar_one()
            assert cache_mid.refresh_lock_token == claim_b.lock_token
            assert cache_mid.video_record_ids == []

        # Worker B completes with its valid token -> succeeds
        completed_b = await worker_b.complete_refresh_lease(
            dish_id=dish_id,
            query_hash="q_stale",
            lock_token=claim_b.lock_token,
            video_record_ids=["fresh_vid_1", "fresh_vid_2"],
            now=now + timedelta(seconds=37),
        )
        assert completed_b is True

        async with session_maker() as db:
            cache_end = (
                await db.execute(
                    select(DishVideoCache).where(
                        DishVideoCache.dish_id == dish_id,
                        DishVideoCache.query_hash == "q_stale",
                    )
                )
            ).scalar_one()
            assert cache_end.refresh_lock_token is None
            assert cache_end.video_record_ids == ["fresh_vid_1", "fresh_vid_2"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_genuine_mid_transaction_seed_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'mid_tx_rollback.db'}"
    await asyncio.to_thread(_run_alembic, db_url, "upgrade", "head")
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    entry_a = CURATED_DISH_CATALOGUE[0]
    entry_b = DishCatalogueEntry(
        slug="new-dish-before-failure",
        canonical_name="New Dish Before Failure",
        ingredients=[
            {
                "ingredient_name": "Onion",
                "requirement_mode": "compatible_family",
                "requirement_type": "required",
                "family_id": "onion",
                "sort_order": 1,
            }
        ],
    )

    try:
        async with session_maker() as db:
            await seed_dish_catalogue(db, entries=[entry_a])
            await db.commit()

            owner = User(clerk_user_id="clerk_rb_owner", display_name="RB Owner")
            db.add(owner)
            await db.flush()
            hh = Household(name="RB Kitchen", created_by=owner.id)
            db.add(hh)
            await db.flush()
            dish_a = (
                await db.execute(select(Dish).where(Dish.slug == entry_a.slug))
            ).scalar_one()
            db.add(SavedDish(household_id=hh.id, dish_id=dish_a.id))
            db.add(DishFeedback(household_id=hh.id, dish_id=dish_a.id, action="cooked", rating=5))
            await db.commit()

        # Pass entries=[entry_b] (without entry_a, so entry_a would be soft-deactivated and
        # entry_b inserted), then fail mid-transaction on final flush after writes occurred.
        async with session_maker() as db:
            orig_flush = db.flush
            flush_calls = 0

            async def _failing_final_flush(*args: object, **kwargs: object) -> None:
                nonlocal flush_calls
                flush_calls += 1
                await orig_flush(*args, **kwargs)
                if flush_calls >= 2:
                    raise RuntimeError("Simulated DB failure mid-transaction")

            monkeypatch.setattr(db, "flush", _failing_final_flush)
            with pytest.raises(RuntimeError, match="Simulated DB failure"):
                await seed_dish_catalogue(db, entries=[entry_b])

        # Verify complete rollback: entry_b was NOT persisted, entry_a remains active,
        # and saved_dishes / dish_feedback remain intact.
        async with session_maker() as db:
            all_dishes = (await db.execute(select(Dish))).scalars().all()
            assert [d.slug for d in all_dishes] == [entry_a.slug]
            assert all_dishes[0].is_active is True
            assert len((await db.execute(select(SavedDish))).scalars().all()) == 1
            assert len((await db.execute(select(DishFeedback))).scalars().all()) == 1
    finally:
        await engine.dispose()
