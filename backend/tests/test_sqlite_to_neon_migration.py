from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db import Base
from scripts.migrate_sqlite_to_neon import (
    compute_dependency_order,
    convert_column_value,
    execute_migration,
    run_dry_run,
    validate_dataset_integrity,
)


def _init_sqlite_schema_via_alembic(db_path: Path) -> None:
    import os
    import subprocess

    backend_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    url = f"sqlite+aiosqlite:///{db_path}"
    env["DATABASE_URL"] = url
    env["DATABASE_MIGRATION_URL"] = url
    res = subprocess.run(
        [str(backend_dir / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr


def test_column_type_conversions() -> None:
    meta = sa.MetaData()
    tbl = sa.Table(
        "sample",
        meta,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("flag", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
    )

    uid = uuid4()
    assert convert_column_value(tbl.c.id, uid.hex) == uid
    assert convert_column_value(tbl.c.flag, 1) is True
    assert convert_column_value(tbl.c.flag, 0) is False
    assert convert_column_value(tbl.c.payload, '{"a": 1}') == {"a": 1}
    assert convert_column_value(tbl.c.payload, None) is None

    dt = convert_column_value(tbl.c.ts, "2026-11-24 12:00:00")
    assert isinstance(dt, datetime) and dt.tzinfo == UTC
    assert convert_column_value(tbl.c.day, "2026-11-24") == date(2026, 11, 24)
    assert convert_column_value(tbl.c.qty, "1.250") == Decimal("1.250")


def test_dependency_order_and_integrity_conflict_detection() -> None:
    import app.modules.auth.models  # noqa: F401
    import app.modules.cooking.models  # noqa: F401
    import app.modules.discovery.daily_models  # noqa: F401
    import app.modules.discovery.models  # noqa: F401
    import app.modules.insights.models  # noqa: F401
    import app.modules.inventory.models  # noqa: F401
    import app.modules.purchases.models  # noqa: F401
    import app.modules.recipes.models  # noqa: F401
    import app.modules.replenishment.models  # noqa: F401

    tables = dict(Base.metadata.tables)
    order, self_refs = compute_dependency_order(tables)
    assert len(order) == 30
    assert order.index("users") < order.index("households")
    assert order.index("households") < order.index("inventory_lots")
    assert ("canonical_items", "merged_into_id", "id") in self_refs

    # Detect duplicate PK and unique constraint conflict (users.clerk_user_id)
    u1, u2 = uuid4(), uuid4()
    now = datetime.now(UTC)
    dataset = {t: [] for t in order}
    dataset["users"] = [
        {
            "id": u1,
            "clerk_user_id": "clk_dup",
            "email": "a@example.com",
            "display_name": "A",
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": u1,  # duplicate PK
            "clerk_user_id": "clk_dup",  # duplicate unique column
            "email": "b@example.com",
            "display_name": "B",
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": u2,
            "clerk_user_id": "clk_dup",  # duplicate unique column
            "email": "c@example.com",
            "display_name": "C",
            "created_at": now,
            "updated_at": now,
        },
    ]
    issues = validate_dataset_integrity(tables, order, dataset)
    assert any("Duplicate primary key in users" in i for i in issues)
    assert any("Unique constraint conflict" in i for i in issues)


@pytest.mark.asyncio
async def test_execute_migration_self_ref_repeat_safety_and_rollback(tmp_path: Path) -> None:
    src_db = tmp_path / "src.db"
    dst_db = tmp_path / "dst.db"
    _init_sqlite_schema_via_alembic(src_db)
    _init_sqlite_schema_via_alembic(dst_db)

    # Clear migration-seeded rows in dst_db so this test exercises clean target insertion
    conn_clean = sqlite3.connect(dst_db)
    conn_clean.execute("DELETE FROM ingredient_aliases")
    conn_clean.execute("DELETE FROM canonical_ingredients")
    conn_clean.commit()
    conn_clean.close()

    u_id = uuid4()
    h_id = uuid4()
    ci_parent = uuid4()
    ci_merged = uuid4()
    now_str = "2026-11-24 10:00:00"

    conn = sqlite3.connect(src_db)
    conn.execute(
        "INSERT INTO users (id, clerk_user_id, email, display_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (u_id.hex, "clk_1", "chef@example.com", "Chef", now_str, now_str),
    )
    conn.execute(
        "INSERT INTO households (id, name, currency_code, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (h_id.hex, "Home", "INR", u_id.hex, now_str, now_str),
    )
    # Self-referential canonical_items: ci_merged points to ci_parent
    for cid, slug, name, merged_to in [
        (ci_merged, "old-milk", "Old Milk", ci_parent.hex),
        (ci_parent, "milk", "Milk", None),
    ]:
        conn.execute(
            "INSERT INTO canonical_items (id, household_id, slug, name, category, food_state, "
            "is_prepared_food, pantry_eligible, budget_treatment, language, verified, "
            "default_base_unit, low_stock_threshold_qty, merged_into_id, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid.hex,
                h_id.hex,
                slug,
                name,
                "dairy",
                "raw",
                0,
                1,
                "pantry_inventory",
                "en",
                1,
                "ml",
                "0.000",
                merged_to,
                1,
                now_str,
            ),
        )
    conn.commit()
    conn.close()

    # 1. Dry-run succeeds with 0 issues
    report = await run_dry_run(src_db, target_url=f"sqlite+aiosqlite:///{dst_db}")
    assert report.ok
    assert report.table_counts["users"] == 1
    assert report.table_counts["canonical_items"] == 2

    # 2. Execute migration copies all rows including self-referential FK
    dst_url = f"sqlite+aiosqlite:///{dst_db}"
    inserted = await execute_migration(src_db, dst_url)
    assert inserted["users"] == 1
    assert inserted["households"] == 1
    assert inserted["canonical_items"] == 2

    # Verify self-referential FK preserved in target
    eng = create_async_engine(dst_url)
    async with eng.connect() as c:
        merged_val = (
            await c.execute(
                text("SELECT merged_into_id FROM canonical_items WHERE slug = 'old-milk'")
            )
        ).scalar_one()
        assert UUID(str(merged_val)) == ci_parent
    await eng.dispose()

    # 3. Repeat-run is idempotent (0 new inserts, no duplicates)
    inserted_again = await execute_migration(src_db, dst_url)
    assert sum(inserted_again.values()) == 0

    # 4. Conflicting existing record aborts & rolls back without overwriting
    conn_dst = sqlite3.connect(dst_db)
    conn_dst.execute("UPDATE users SET display_name = 'Modified Target'")
    conn_dst.commit()
    conn_dst.close()

    with pytest.raises(ValueError, match="Refusing to overwrite conflicting record"):
        await execute_migration(src_db, dst_url)

    conn_dst = sqlite3.connect(dst_db)
    assert conn_dst.execute("SELECT display_name FROM users").fetchone()[0] == "Modified Target"
    conn_dst.close()


@pytest.mark.asyncio
async def test_seed_reconciliation_updates_fields_preserves_ids_and_opt_in_behavior(
    tmp_path: Path,
) -> None:
    src_db = tmp_path / "src.db"
    dst_db = tmp_path / "dst.db"
    _init_sqlite_schema_via_alembic(src_db)
    _init_sqlite_schema_via_alembic(dst_db)

    # Enrich 1 seeded canonical_ingredient in src_db (taxonomy, variant, JSON, updated_at)
    # and add 1 extra canonical_ingredient + 1 user
    u_id = uuid4()
    extra_ing_id = uuid4()
    now_str = "2026-11-24 10:00:00"

    conn_src = sqlite3.connect(src_db)
    seed_id, seed_norm = conn_src.execute(
        "SELECT id, normalized_name FROM canonical_ingredients ORDER BY normalized_name LIMIT 1"
    ).fetchone()
    conn_src.execute(
        "UPDATE canonical_ingredients "
        "SET family_id = ?, family_name = ?, variant_id = ?, variant_status = ?, "
        "is_unspecified_variant = ?, attributes_json = ?, updated_at = ? "
        "WHERE id = ?",
        (
            "fam_dairy",
            "Dairy",
            "var_whole",
            "resolved",
            0,
            '{"fat": "full"}',
            now_str,
            seed_id,
        ),
    )
    conn_src.execute(
        "INSERT INTO canonical_ingredients "
        "(id, name, normalized_name, category, standard_unit, cooking_variants, supported_units, "
        "is_active, is_unspecified_variant, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            extra_ing_id.hex,
            "Extra Saffron",
            "extra saffron",
            "spices",
            "g",
            "[]",
            '["g"]',
            1,
            0,
            now_str,
            now_str,
        ),
    )
    conn_src.execute(
        "INSERT INTO users (id, clerk_user_id, email, display_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (u_id.hex, "clk_seed_test", "seed@example.com", "Seed Tester", now_str, now_str),
    )
    conn_src.commit()
    conn_src.close()

    dst_url = f"sqlite+aiosqlite:///{dst_db}"

    # 1. Dry-run with sync_seeded_tables=True passes and reports exact planned changes
    report = await run_dry_run(src_db, target_url=dst_url, sync_seeded_tables=True)
    assert report.ok, report.issues
    ci_plan = report.reconciliation_plans["canonical_ingredients"]
    ia_plan = report.reconciliation_plans["ingredient_aliases"]
    assert ci_plan.expected_seed_count == 50
    assert ci_plan.matched_pk_count == 50
    assert ci_plan.rows_to_insert == 1
    assert ci_plan.column_change_counts["family_id"] == 1
    assert ci_plan.column_change_counts["attributes_json"] == 1
    assert ia_plan.expected_seed_count == 59
    assert ia_plan.matched_pk_count == 59
    assert ia_plan.rows_to_insert == 0

    # 2. Without opt-in flag, existing strict conflict-abort behavior is unchanged
    with pytest.raises(ValueError, match="Refusing to overwrite conflicting record"):
        await execute_migration(src_db, dst_url, sync_seeded_tables=False)

    # 3. With opt-in flag, reconciliation updates fields, preserves IDs, and inserts new rows
    updated: dict[str, int] = {}
    inserted = await execute_migration(
        src_db, dst_url, sync_seeded_tables=True, out_updated_counts=updated
    )
    assert updated["canonical_ingredients"] >= 1
    assert inserted["canonical_ingredients"] == 1
    assert inserted["users"] == 1

    conn_dst = sqlite3.connect(dst_db)
    row = conn_dst.execute(
        "SELECT id, normalized_name, family_id, family_name, variant_id, variant_status, "
        "attributes_json FROM canonical_ingredients WHERE id = ?",
        (seed_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == seed_id
    assert row[1] == seed_norm
    assert row[2] == "fam_dairy"
    assert row[3] == "Dairy"
    assert row[4] == "var_whole"
    assert row[5] == "resolved"
    assert '"fat"' in row[6]
    assert conn_dst.execute("SELECT count(*) FROM canonical_ingredients").fetchone()[0] == 51
    assert conn_dst.execute("SELECT count(*) FROM ingredient_aliases").fetchone()[0] == 59
    conn_dst.close()


@pytest.mark.asyncio
async def test_seed_reconciliation_aborts_on_unexpected_seed_id(tmp_path: Path) -> None:
    src_db = tmp_path / "src.db"
    dst_db = tmp_path / "dst.db"
    _init_sqlite_schema_via_alembic(src_db)
    _init_sqlite_schema_via_alembic(dst_db)

    # Replace one seeded canonical_ingredient ID in dst_db with an unexpected Neon-only UUID
    conn_dst = sqlite3.connect(dst_db)
    victim_id = conn_dst.execute(
        "SELECT id FROM canonical_ingredients "
        "WHERE id NOT IN (SELECT canonical_ingredient_id FROM ingredient_aliases) LIMIT 1"
    ).fetchone()[0]
    unexpected_id = uuid4().hex
    conn_dst.execute(
        "UPDATE canonical_ingredients SET id = ? WHERE id = ?",
        (unexpected_id, victim_id),
    )
    conn_dst.commit()
    conn_dst.close()

    dst_url = f"sqlite+aiosqlite:///{dst_db}"
    report = await run_dry_run(src_db, target_url=dst_url, sync_seeded_tables=True)
    assert not report.ok
    assert any(
        "Unexpected target-only primary key in canonical_ingredients" in i for i in report.issues
    )

    with pytest.raises(ValueError, match="Seed reconciliation pre-check failed"):
        await execute_migration(src_db, dst_url, sync_seeded_tables=True)


@pytest.mark.asyncio
async def test_seed_reconciliation_protects_other_28_tables(tmp_path: Path) -> None:
    src_db = tmp_path / "src.db"
    dst_db = tmp_path / "dst.db"
    _init_sqlite_schema_via_alembic(src_db)
    _init_sqlite_schema_via_alembic(dst_db)

    now_str = "2026-11-24 10:00:00"
    u_id = uuid4().hex
    conn_dst = sqlite3.connect(dst_db)
    conn_dst.execute(
        "INSERT INTO users (id, clerk_user_id, email, display_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (u_id, "clk_existing", "keep@example.com", "Keep Me", now_str, now_str),
    )
    conn_dst.commit()
    conn_dst.close()

    dst_url = f"sqlite+aiosqlite:///{dst_db}"
    report = await run_dry_run(src_db, target_url=dst_url, sync_seeded_tables=True)
    assert not report.ok
    assert any("Non-seeded table users is not empty in target" in i for i in report.issues)

    with pytest.raises(ValueError, match="Seed reconciliation pre-check failed"):
        await execute_migration(src_db, dst_url, sync_seeded_tables=True)

    conn_dst = sqlite3.connect(dst_db)
    assert conn_dst.execute("SELECT display_name FROM users").fetchone()[0] == "Keep Me"
    conn_dst.close()


@pytest.mark.asyncio
async def test_seed_reconciliation_rolls_back_all_changes_on_mid_tx_failure(
    tmp_path: Path,
) -> None:
    src_db = tmp_path / "src.db"
    dst_db = tmp_path / "dst.db"
    _init_sqlite_schema_via_alembic(src_db)
    _init_sqlite_schema_via_alembic(dst_db)

    now_str = "2026-11-24 10:00:00"
    conn_src = sqlite3.connect(src_db)
    seed_id = conn_src.execute(
        "SELECT id FROM canonical_ingredients ORDER BY normalized_name LIMIT 1"
    ).fetchone()[0]
    conn_src.execute(
        "UPDATE canonical_ingredients SET family_name = 'RolledBackFamily' WHERE id = ?",
        (seed_id,),
    )
    conn_src.execute(
        "INSERT INTO users (id, clerk_user_id, email, display_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uuid4().hex, "clk_boom", "boom@example.com", "Boom", now_str, now_str),
    )
    conn_src.commit()
    conn_src.close()

    # Inject a trigger in dst_db that aborts when inserting into users (after canonical_ingredients)
    conn_dst = sqlite3.connect(dst_db)
    orig_family = conn_dst.execute(
        "SELECT family_name FROM canonical_ingredients WHERE id = ?", (seed_id,)
    ).fetchone()[0]
    conn_dst.execute(
        "CREATE TRIGGER fail_user_insert BEFORE INSERT ON users "
        "BEGIN SELECT RAISE(ABORT, 'simulated downstream failure'); END;"
    )
    conn_dst.commit()
    conn_dst.close()

    dst_url = f"sqlite+aiosqlite:///{dst_db}"
    with pytest.raises(Exception, match="simulated downstream failure"):
        await execute_migration(src_db, dst_url, sync_seeded_tables=True)

    # Confirm canonical_ingredients update was rolled back atomically
    conn_dst = sqlite3.connect(dst_db)
    after_family = conn_dst.execute(
        "SELECT family_name FROM canonical_ingredients WHERE id = ?", (seed_id,)
    ).fetchone()[0]
    assert after_family == orig_family
    assert conn_dst.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    conn_dst.close()
