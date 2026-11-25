"""Repeatable, dry-run-by-default refresh utility for the isolated Public Demo dataset.

Safety guarantees:
- Defaults to ``--dry-run`` (zero writes).
- Strictly separates source household from ``DEMO_HOUSEHOLD_ID`` (aborts if equal).
- Deletes ONLY rows belonging to ``DEMO_HOUSEHOLD_ID`` inside the SAME atomic transaction
  as inserting the newly sanitized demo rows.
- Verifies source household row counts and content checksum BEFORE committing.
- Never modifies global shared tables, never prints PII or credentials, and rejects
  pooled Neon endpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Table, delete, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.db import Base, build_async_engine, normalize_database_url  # noqa: E402
from app.modules.demo.dataset_builder import (  # noqa: E402
    DEMO_HOUSEHOLD_ID,
    DEMO_USER_ID,
    DemoBuildReport,
    build_demo_dataset,
)
from scripts.migrate_sqlite_to_neon import (  # noqa: E402
    _import_all_models,
    _resolve_target_url_from_env,
    compute_dependency_order,
    convert_column_value,
    validate_dataset_integrity,
)

# Tables populated exclusively for the demo household (global tables are read-only)
DEMO_HOUSEHOLD_TABLES: tuple[str, ...] = (
    "canonical_items",
    "canonical_merge_log",
    "cooking_plans",
    "daily_meal_plans",
    "daily_meal_plan_recipes",
    "household_budgets",
    "household_members",
    "household_recipe_preferences",
    "ingredient_aliases",
    "ingredient_mappings",
    "item_aliases",
    "meal_history",
    "purchases",
    "food_consumptions",
    "purchase_items",
    "inventory_lots",
    "recipe_feedback",
    "stock_ledger",
)


def _read_sqlite_source_for_demo(
    source_path: Path,
    tables: dict[str, Table],
    *,
    source_household_id: UUID | None = None,
) -> tuple[UUID, dict[str, list[dict[str, Any]]], set[UUID]]:
    """Read source household rows strictly read-only from SQLite and convert types."""
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite source database not found: {source_path}")

    conn = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite PRAGMA integrity_check failed: {integrity}")

        if source_household_id is None:
            # Select the non-demo household with the most confirmed/non-draft purchases
            rows = conn.execute(
                "SELECT h.id, count(p.id) AS p_cnt "
                "FROM households h LEFT JOIN purchases p ON p.household_id = h.id "
                "GROUP BY h.id ORDER BY p_cnt DESC, h.created_at ASC"
            ).fetchall()
            candidates = [
                UUID(str(r["id"])) for r in rows if UUID(str(r["id"])) != DEMO_HOUSEHOLD_ID
            ]
            if not candidates:
                raise ValueError("No non-demo source household found in SQLite database.")
            resolved_hid = candidates[0]
        else:
            resolved_hid = source_household_id

        if resolved_hid == DEMO_HOUSEHOLD_ID:
            raise ValueError("Source household ID must never equal DEMO_HOUSEHOLD_ID.")

        global_ci_ids = {
            UUID(str(r[0])) for r in conn.execute("SELECT id FROM canonical_ingredients").fetchall()
        }

        source_rows: dict[str, list[dict[str, Any]]] = {}
        existing_tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for tname in DEMO_HOUSEHOLD_TABLES:
            if tname not in existing_tables or tname not in tables:
                source_rows[tname] = []
                continue
            tbl = tables[tname]
            db_cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{tname}")').fetchall()}
            sel_cols = [c for c in tbl.columns if c.name in db_cols]
            quoted = ", ".join(f'"{c.name}"' for c in sel_cols)
            raw_rows = conn.execute(f'SELECT {quoted} FROM "{tname}"').fetchall()
            converted: list[dict[str, Any]] = []
            for r in raw_rows:
                rec = {c.name: convert_column_value(c, r[c.name]) for c in sel_cols}
                converted.append(rec)
            source_rows[tname] = converted

        return resolved_hid, source_rows, global_ci_ids
    finally:
        conn.close()


async def _snapshot_household_checksum(
    conn: AsyncConnection,
    tables: dict[str, Table],
    order: list[str],
    household_id: UUID,
    actual_cols_by_table: dict[str, set[str]] | None = None,
) -> tuple[dict[str, int], str]:
    """Compute exact per-table row counts and deterministic SHA-256 checksum for a household."""
    counts: dict[str, int] = {}
    hasher = hashlib.sha256()

    for tname in order:
        if tname not in DEMO_HOUSEHOLD_TABLES:
            continue
        tbl = tables[tname]
        allowed = (
            actual_cols_by_table.get(tname, {c.name for c in tbl.columns})
            if actual_cols_by_table is not None
            else {c.name for c in tbl.columns}
        )
        sel_cols = [c for c in tbl.columns if c.name in allowed]
        if "household_id" in allowed:
            stmt = select(*sel_cols).where(tbl.c.household_id == household_id)
        elif tname == "daily_meal_plan_recipes":
            dmp = tables["daily_meal_plans"]
            stmt = select(*sel_cols).where(
                tbl.c.daily_meal_plan_id.in_(
                    select(dmp.c.id).where(dmp.c.household_id == household_id)
                )
            )
        else:
            continue

        pk_cols = [c.name for c in tbl.primary_key.columns]
        rows = [dict(r) for r in (await conn.execute(stmt)).mappings().all()]
        rows.sort(key=lambda r: tuple(str(r[c]) for c in pk_cols))
        counts[tname] = len(rows)
        hasher.update(tname.encode("utf-8"))
        hasher.update(json.dumps(rows, sort_keys=True, default=str).encode("utf-8"))

    return counts, hasher.hexdigest()


def _validate_demo_fk_integrity(
    tables: dict[str, Table],
    order: list[str],
    dataset: dict[str, list[dict[str, Any]]],
    global_canonical_ingredient_ids: set[UUID],
) -> list[str]:
    """Validate PK uniqueness, FK integrity, and unique constraints on the demo dataset."""
    augmented: dict[str, list[dict[str, Any]]] = {t: list(dataset.get(t, [])) for t in order}
    augmented["canonical_ingredients"] = [
        {"id": ci_id} for ci_id in global_canonical_ingredient_ids
    ]
    # Validate only tables present in our demo dataset (plus global canonical_ingredients)
    demo_subset = {
        t: tbl for t, tbl in tables.items() if t in dataset or t == "canonical_ingredients"
    }
    sub_order = [t for t in order if t in demo_subset]
    # Skip PK check on synthetic stub canonical_ingredients (only id populated)
    issues = validate_dataset_integrity(
        {t: tables[t] for t in sub_order if t != "canonical_ingredients"},
        [t for t in sub_order if t != "canonical_ingredients"],
        augmented,
    )
    # Also verify canonical_ingredient_id FKs against global_canonical_ingredient_ids
    for tname in ("ingredient_aliases", "ingredient_mappings"):
        for idx, row in enumerate(dataset.get(tname, [])):
            ci_id = row.get("canonical_ingredient_id")
            if ci_id is not None and ci_id not in global_canonical_ingredient_ids:
                issues.append(f"Dangling global FK {tname}[{idx}].canonical_ingredient_id={ci_id}")
    return issues


def run_demo_dry_run(
    source_path: Path,
    *,
    source_household_id: UUID | None = None,
    plan_date: date | None = None,
) -> tuple[DemoBuildReport, list[str]]:
    """Build and fully validate the demo dataset in memory without touching any database."""
    _import_all_models()
    tables = dict(Base.metadata.tables)
    order, _ = compute_dependency_order(tables)

    resolved_hid, source_rows, global_ci_ids = _read_sqlite_source_for_demo(
        source_path, tables, source_household_id=source_household_id
    )
    report = build_demo_dataset(
        source_household_id=resolved_hid,
        source_rows=source_rows,
        global_canonical_ingredient_ids=global_ci_ids,
        plan_date=plan_date,
    )
    fk_issues = _validate_demo_fk_integrity(tables, order, report.dataset, global_ci_ids)
    return report, fk_issues


async def execute_demo_refresh(
    source_path: Path,
    target_url: str,
    *,
    source_household_id: UUID | None = None,
    plan_date: date | None = None,
    allow_pooler: bool = False,
) -> tuple[DemoBuildReport, dict[str, int], bool]:
    """Atomically replace ONLY DEMO_HOUSEHOLD_ID data in target DB and verify immutability."""
    normalized_target = normalize_database_url(target_url)
    host = urlsplit(normalized_target).hostname or ""
    if "-pooler." in host and not allow_pooler:
        raise ValueError(
            "Refusing to refresh demo dataset via pooled Neon endpoint; supply "
            "DATABASE_MIGRATION_URL (direct/unpooled connection)."
        )

    _import_all_models()
    tables = dict(Base.metadata.tables)
    order, self_refs = compute_dependency_order(tables)

    report, fk_issues = run_demo_dry_run(
        source_path, source_household_id=source_household_id, plan_date=plan_date
    )
    if not report.ok:
        raise ValueError(
            f"Demo PII assertion gate failed ({len(report.pii_violations)} violations): "
            f"{report.pii_violations[:5]}"
        )
    if fk_issues:
        raise ValueError(f"Demo FK integrity validation failed ({len(fk_issues)}): {fk_issues[:5]}")

    self_ref_cols_by_table: dict[str, list[str]] = {}
    for tname, col_name, _target_col in self_refs:
        self_ref_cols_by_table.setdefault(tname, []).append(col_name)

    inserted_counts: dict[str, int] = {}
    eng = build_async_engine(normalized_target)
    try:
        async with eng.begin() as conn:
            # Inspect actual target DB columns (handles 0021 vs 0022 compatibility cleanly)
            actual_cols_by_table: dict[str, set[str]] = {}

            def _inspect_cols(sync_conn: sa.Connection) -> None:
                insp = inspect(sync_conn)
                for tname in order:
                    if insp.has_table(tname):
                        actual_cols_by_table[tname] = {c["name"] for c in insp.get_columns(tname)}

            await conn.run_sync(_inspect_cols)

            # 1. Pre-refresh checksum of source household (must remain byte-for-byte unchanged)
            src_counts_before, src_hash_before = await _snapshot_household_checksum(
                conn, tables, order, report.source_household_id, actual_cols_by_table
            )

            # 2. Delete ONLY DEMO_HOUSEHOLD_ID rows in reverse dependency order
            for tname in reversed(order):
                if tname not in DEMO_HOUSEHOLD_TABLES:
                    continue
                tbl = tables[tname]
                if tname == "canonical_items":
                    await conn.execute(
                        tbl.update()
                        .where(tbl.c.household_id == DEMO_HOUSEHOLD_ID)
                        .values(merged_into_id=None)
                    )
                if "household_id" in tbl.c:
                    await conn.execute(delete(tbl).where(tbl.c.household_id == DEMO_HOUSEHOLD_ID))
                elif tname == "daily_meal_plan_recipes":
                    dmp = tables["daily_meal_plans"]
                    await conn.execute(
                        delete(tbl).where(
                            tbl.c.daily_meal_plan_id.in_(
                                select(dmp.c.id).where(dmp.c.household_id == DEMO_HOUSEHOLD_ID)
                            )
                        )
                    )

            # 3. Upsert synthetic Demo User and Demo Household (never deleting/touching others)
            users_tbl = tables["users"]
            demo_user_row = report.dataset["users"][0]
            existing_user = (
                await conn.execute(select(users_tbl.c.id).where(users_tbl.c.id == DEMO_USER_ID))
            ).scalar_one_or_none()
            if existing_user is None:
                await conn.execute(users_tbl.insert().values(**demo_user_row))
            else:
                await conn.execute(
                    users_tbl.update()
                    .where(users_tbl.c.id == DEMO_USER_ID)
                    .values(
                        display_name=demo_user_row["display_name"],
                        email=None,
                        updated_at=demo_user_row["updated_at"],
                    )
                )

            hh_tbl = tables["households"]
            demo_hh_row = report.dataset["households"][0]
            existing_hh = (
                await conn.execute(select(hh_tbl.c.id).where(hh_tbl.c.id == DEMO_HOUSEHOLD_ID))
            ).scalar_one_or_none()
            if existing_hh is None:
                await conn.execute(hh_tbl.insert().values(**demo_hh_row))
            else:
                await conn.execute(
                    hh_tbl.update()
                    .where(hh_tbl.c.id == DEMO_HOUSEHOLD_ID)
                    .values(
                        name=demo_hh_row["name"],
                        currency_code=demo_hh_row["currency_code"],
                        updated_at=demo_hh_row["updated_at"],
                    )
                )

            # 4. Insert all sanitized demo household rows in topological order
            deferred_self_updates: list[tuple[Table, dict[str, Any], dict[str, Any]]] = []
            for tname in order:
                if tname not in DEMO_HOUSEHOLD_TABLES:
                    continue
                rows = report.dataset.get(tname, [])
                if not rows:
                    inserted_counts[tname] = 0
                    continue
                tbl = tables[tname]
                allowed_db_cols = actual_cols_by_table.get(tname, {c.name for c in tbl.columns})
                pk_cols = [c.name for c in tbl.primary_key.columns]
                self_cols = self_ref_cols_by_table.get(tname, [])

                batch: list[dict[str, Any]] = []
                for row in rows:
                    filtered = {k: v for k, v in row.items() if k in allowed_db_cols}
                    if self_cols and any(filtered.get(sc) is not None for sc in self_cols):
                        pass1 = dict(filtered)
                        upd = {sc: filtered[sc] for sc in self_cols if filtered.get(sc) is not None}
                        for sc in self_cols:
                            pass1[sc] = None
                        batch.append(pass1)
                        deferred_self_updates.append((tbl, {c: filtered[c] for c in pk_cols}, upd))
                    else:
                        batch.append(filtered)

                await conn.execute(tbl.insert(), batch)
                inserted_counts[tname] = len(batch)

            for tbl, pk_filter, upd in deferred_self_updates:
                stmt = tbl.update().values(**upd)
                for c_name, pk_val in pk_filter.items():
                    stmt = stmt.where(tbl.c[c_name] == pk_val)
                await conn.execute(stmt)

            # 5. Post-refresh immutability gate for source household BEFORE commit
            src_counts_after, src_hash_after = await _snapshot_household_checksum(
                conn, tables, order, report.source_household_id, actual_cols_by_table
            )
            if src_counts_before != src_counts_after or src_hash_before != src_hash_after:
                raise RuntimeError(
                    "ABORT: Source household checksum changed during demo refresh; rolling back!"
                )

            # Check SQLite FK integrity inside transaction if targeting SQLite
            if normalized_target.startswith("sqlite"):
                fk_violations = (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall()
                if fk_violations:
                    raise RuntimeError(
                        f"ABORT: SQLite PRAGMA foreign_key_check failed: {fk_violations[:5]}"
                    )
    finally:
        await eng.dispose()

    return report, inserted_counts, True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and refresh the isolated public demo dataset (dry-run by default)."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=_BACKEND_DIR / "dev_local.db",
        help="Path to read-only SQLite source database.",
    )
    parser.add_argument(
        "--source-household-id",
        type=UUID,
        default=None,
        help=(
            "Optional explicit source household UUID "
            "(defaults to most populated non-demo household)."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=_BACKEND_DIR.parent / ".env",
        help="Optional .env file containing DATABASE_MIGRATION_URL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate curation, sanitization, PII gate, and FK integrity without writing (default)."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Atomically refresh ONLY DEMO_HOUSEHOLD_ID in target database.",
    )
    args = parser.parse_args()

    if args.execute and args.dry_run:
        print("ERROR: Specify either --dry-run or --execute, not both.", file=sys.stderr)
        return 2
    if not args.execute and not args.dry_run:
        args.dry_run = True

    if args.dry_run:
        report, fk_issues = run_demo_dry_run(
            args.source, source_household_id=args.source_household_id
        )
        print(f"Dry-run OK: {report.ok and len(fk_issues) == 0}")
        print(f"DEMO_USER_ID: {report.demo_user_id}")
        print(f"DEMO_HOUSEHOLD_ID: {report.demo_household_id}")
        print(f"Total demo rows: {sum(report.table_counts.values())}")
        print("Planned row counts per table:")
        for tname, cnt in report.table_counts.items():
            print(f"  {tname}: {cnt}")
        print(f"Sanitization summary: {report.sanitization_summary}")
        print(f"PII violations: {len(report.pii_violations)}")
        for v in report.pii_violations[:10]:
            print(f"  - {v}")
        print(f"FK integrity issues: {len(fk_issues)}")
        for issue in fk_issues[:10]:
            print(f"  - {issue}")
        return 0 if (report.ok and not fk_issues) else 1

    target_url = _resolve_target_url_from_env(args.env_file)
    if not target_url:
        print("ERROR: No DATABASE_MIGRATION_URL or DATABASE_URL_UNPOOLED found.", file=sys.stderr)
        return 2

    report, inserted, src_unchanged = asyncio.run(
        execute_demo_refresh(
            args.source,
            target_url,
            source_household_id=args.source_household_id,
        )
    )
    print(f"Demo dataset refreshed successfully. Source household unchanged: {src_unchanged}")
    for tname, cnt in inserted.items():
        if cnt > 0:
            print(f"  {tname}: {cnt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
