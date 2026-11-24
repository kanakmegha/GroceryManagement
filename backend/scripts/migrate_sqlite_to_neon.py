"""Type-aware, transactional SQLite-to-PostgreSQL (Neon) data migration utility.

Usage:
  Dry-run (read-only, no writes to Neon):
    python scripts/migrate_sqlite_to_neon.py --source dev_local.db --dry-run

  Execute (writes inside a single atomic transaction; requires explicit --execute):
    python scripts/migrate_sqlite_to_neon.py --source dev_local.db --execute
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Table, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

# Ensure backend/ is on sys.path when executed as a standalone script
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.db import Base, build_async_engine, normalize_database_url  # noqa: E402

SEEDED_TABLES_EXPECTED_COUNTS: dict[str, int] = {
    "canonical_ingredients": 50,
    "ingredient_aliases": 59,
}


def _import_all_models() -> None:
    """Register all 30 application models on Base.metadata."""
    import app.modules.auth.models  # noqa: F401
    import app.modules.cooking.models  # noqa: F401
    import app.modules.discovery.daily_models  # noqa: F401
    import app.modules.discovery.models  # noqa: F401
    import app.modules.insights.models  # noqa: F401
    import app.modules.inventory.models  # noqa: F401
    import app.modules.purchases.models  # noqa: F401
    import app.modules.recipes.models  # noqa: F401
    import app.modules.replenishment.models  # noqa: F401


@dataclass
class SeedReconciliationPlan:
    table_name: str
    expected_seed_count: int
    target_existing_count: int
    matched_pk_count: int
    rows_to_update: int
    rows_unchanged: int
    rows_to_insert: int
    unexpected_target_pks: list[tuple[Any, ...]] = field(default_factory=list)
    column_change_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class DryRunReport:
    dependency_order: list[str]
    self_referential_fks: list[tuple[str, str, str]]
    table_counts: dict[str, int] = field(default_factory=dict)
    conversion_counts: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    target_existing_counts: dict[str, int] = field(default_factory=dict)
    reconciliation_plans: dict[str, SeedReconciliationPlan] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return len(self.issues) == 0


def compute_dependency_order(
    tables: dict[str, Table],
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Return deterministic topological order and self-referential FK list."""
    self_refs: list[tuple[str, str, str]] = []
    edges: dict[str, set[str]] = {}
    for tname, tbl in tables.items():
        deps: set[str] = set()
        for fk in tbl.foreign_keys:
            target = fk.column.table.name
            if target == tname:
                self_refs.append((tname, fk.parent.name, fk.column.name))
            elif target in tables:
                deps.add(target)
        edges[tname] = deps

    in_deg = {t: len(deps) for t, deps in edges.items()}
    ready = sorted([t for t, d in in_deg.items() if d == 0])
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for t, deps in edges.items():
            if cur in deps:
                in_deg[t] -= 1
                if in_deg[t] == 0:
                    ready.append(t)
                    ready.sort()

    remaining = [t for t in tables if t not in order]
    if remaining:
        raise ValueError(f"Unresolved multi-table FK cycle involving: {remaining}")
    return order, sorted(self_refs)


def convert_column_value(
    col: sa.Column[Any],
    raw_value: Any,
    stats: Counter[str] | None = None,
) -> Any:
    """Convert a SQLite raw value to the Python type expected by PostgreSQL/asyncpg."""
    if raw_value is None:
        if stats is not None:
            stats["null"] += 1
        return None

    col_type = col.type
    if isinstance(col_type, sa.Uuid):
        if stats is not None:
            stats["uuid"] += 1
        return raw_value if isinstance(raw_value, UUID) else UUID(str(raw_value).strip())

    if isinstance(col_type, sa.Boolean):
        if stats is not None:
            stats["boolean"] += 1
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, (int, float)):
            return bool(raw_value)
        low = str(raw_value).strip().lower()
        if low in {"1", "true", "t", "yes"}:
            return True
        if low in {"0", "false", "f", "no"}:
            return False
        raise ValueError(f"Invalid boolean value {raw_value!r} for {col.table.name}.{col.name}")

    if isinstance(col_type, sa.JSON):
        if stats is not None:
            stats["json"] += 1
        if isinstance(raw_value, (dict, list)):
            return raw_value
        if isinstance(raw_value, str):
            return json.loads(raw_value)
        return raw_value

    if isinstance(col_type, sa.DateTime):
        if stats is not None:
            stats["datetime"] += 1
        if isinstance(raw_value, datetime):
            dt = raw_value
        else:
            s = str(raw_value).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
        if getattr(col_type, "timezone", False) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt

    if isinstance(col_type, sa.Date):
        if stats is not None:
            stats["date"] += 1
        if isinstance(raw_value, date) and not isinstance(raw_value, datetime):
            return raw_value
        return date.fromisoformat(str(raw_value).strip()[:10])

    if isinstance(col_type, sa.Numeric):
        if stats is not None:
            stats["numeric"] += 1
        return raw_value if isinstance(raw_value, Decimal) else Decimal(str(raw_value))

    if isinstance(col_type, sa.Integer):
        if stats is not None:
            stats["integer"] += 1
        return int(raw_value)

    if isinstance(col_type, sa.Float):
        if stats is not None:
            stats["float"] += 1
        return float(raw_value)

    if stats is not None:
        stats["string"] += 1
    return str(raw_value)


def read_sqlite_dataset(
    source_path: Path,
    tables: dict[str, Table],
    order: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], Counter[str], list[str]]:
    """Read all rows from SQLite strictly in read-only mode and convert values in memory."""
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite source database not found: {source_path}")

    uri = f"file:{source_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    stats: Counter[str] = Counter()
    issues: list[str] = []
    converted_rows: dict[str, list[dict[str, Any]]] = {}

    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            issues.append(f"SQLite PRAGMA integrity_check failed: {integrity}")

        existing_tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        for tname in order:
            tbl = tables[tname]
            if tname not in existing_tables:
                issues.append(f"Missing table in SQLite source: {tname}")
                converted_rows[tname] = []
                continue

            col_names = [c.name for c in tbl.columns]
            quoted_cols = ", ".join(f'"{c}"' for c in col_names)
            raw_rows = conn.execute(f'SELECT {quoted_cols} FROM "{tname}"').fetchall()
            out_rows: list[dict[str, Any]] = []
            for row_idx, r in enumerate(raw_rows):
                rec: dict[str, Any] = {}
                for col in tbl.columns:
                    raw_val = r[col.name]
                    try:
                        val = convert_column_value(col, raw_val, stats)
                    except Exception as exc:
                        issues.append(
                            f"Type conversion failed at {tname}[{row_idx}].{col.name}: {exc}"
                        )
                        val = None
                    if val is None and not col.nullable:
                        issues.append(f"NOT NULL violation at {tname}[{row_idx}].{col.name}")
                    rec[col.name] = val
                out_rows.append(rec)
            converted_rows[tname] = out_rows
    finally:
        conn.close()

    return converted_rows, stats, issues


def validate_dataset_integrity(
    tables: dict[str, Table],
    order: list[str],
    dataset: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Validate PK uniqueness, FK references, and unique constraints in memory."""
    issues: list[str] = []
    pk_sets: dict[str, set[tuple[Any, ...]]] = {}

    # 1. Primary key uniqueness
    for tname in order:
        tbl = tables[tname]
        pk_cols = [c.name for c in tbl.primary_key.columns]
        seen_pks: set[tuple[Any, ...]] = set()
        for idx, row in enumerate(dataset.get(tname, [])):
            pk_val = tuple(row[c] for c in pk_cols)
            if pk_val in seen_pks:
                issues.append(f"Duplicate primary key in {tname}[{idx}]: {pk_val}")
            seen_pks.add(pk_val)
        pk_sets[tname] = seen_pks

    # 2. Foreign key referential integrity
    for tname in order:
        tbl = tables[tname]
        for fk in tbl.foreign_keys:
            parent_col = fk.parent.name
            target_table = fk.column.table.name
            target_col = fk.column.name
            target_vals = {row[target_col] for row in dataset.get(target_table, [])}
            for idx, row in enumerate(dataset.get(tname, [])):
                val = row[parent_col]
                if val is not None and val not in target_vals:
                    issues.append(
                        f"Dangling FK {tname}[{idx}].{parent_col}={val!r} "
                        f"not found in {target_table}.{target_col}"
                    )

    # 3. Unique constraints (full table-level UniqueConstraint and unique columns)
    for tname in order:
        tbl = tables[tname]
        unique_col_sets: list[tuple[str, list[str]]] = []
        for cons in tbl.constraints:
            if isinstance(cons, sa.UniqueConstraint):
                col_names = [c.name for c in cons.columns]
                unique_col_sets.append((cons.name or ",".join(col_names), col_names))
        for col in tbl.columns:
            if col.unique and not col.primary_key:
                unique_col_sets.append((f"{tname}.{col.name}", [col.name]))

        for cname, cols in unique_col_sets:
            seen_uqs: set[tuple[Any, ...]] = set()
            for idx, row in enumerate(dataset.get(tname, [])):
                vals = tuple(row[c] for c in cols)
                # In PostgreSQL, NULLs are distinct in standard UNIQUE constraints
                if any(v is None for v in vals):
                    continue
                if vals in seen_uqs:
                    issues.append(f"Unique constraint conflict {cname} in {tname}[{idx}]: {vals}")
                seen_uqs.add(vals)

    return issues


async def inspect_target_counts(
    engine: AsyncEngine,
    order: list[str],
) -> dict[str, int]:
    """Read-only row counts for target PostgreSQL tables."""
    counts: dict[str, int] = {}
    async with engine.connect() as conn:
        for tname in order:
            cnt = (await conn.execute(text(f'SELECT count(*) FROM "{tname}"'))).scalar_one()
            counts[tname] = int(cnt)
    return counts


async def inspect_seed_reconciliation(
    conn: AsyncConnection,
    tables: dict[str, Table],
    order: list[str],
    dataset: dict[str, list[dict[str, Any]]],
    expected_seed_counts: dict[str, int] = SEEDED_TABLES_EXPECTED_COUNTS,
) -> tuple[dict[str, SeedReconciliationPlan], dict[str, int], list[str]]:
    """Verify pre-write safety checks and build exact reconciliation plan for seeded tables.

    Enforces:
    - All non-seeded tables (the other 28 tables) are completely empty in target.
    - Each seeded table has the exact expected existing seed count (50 / 59).
    - Every existing target seed PK exists in the SQLite source (zero unexpected IDs).
    - Records every column that would change for matched PKs.
    """
    plans: dict[str, SeedReconciliationPlan] = {}
    target_counts: dict[str, int] = {}
    issues: list[str] = []

    for tname in order:
        if tname not in expected_seed_counts:
            cnt = int((await conn.execute(text(f'SELECT count(*) FROM "{tname}"'))).scalar_one())
            target_counts[tname] = cnt
            if cnt > 0:
                issues.append(
                    f"Non-seeded table {tname} is not empty in target (found {cnt} rows); "
                    "refusing --sync-seeded-tables migration."
                )
            continue

        tbl = tables[tname]
        pk_cols = [c.name for c in tbl.primary_key.columns]
        expected_cnt = expected_seed_counts[tname]
        existing_rows = (await conn.execute(select(tbl))).mappings().all()
        target_counts[tname] = len(existing_rows)

        if len(existing_rows) != expected_cnt:
            issues.append(
                f"Unexpected existing seed count in {tname}: "
                f"expected {expected_cnt}, found {len(existing_rows)}"
            )

        source_rows = dataset.get(tname, [])
        source_by_pk = {tuple(r[c] for c in pk_cols): r for r in source_rows}

        matched_pk_count = 0
        rows_to_update = 0
        rows_unchanged = 0
        unexpected_pks: list[tuple[Any, ...]] = []
        col_changes: Counter[str] = Counter()

        for existing in existing_rows:
            pk_key = tuple(existing[c] for c in pk_cols)
            if pk_key not in source_by_pk:
                unexpected_pks.append(pk_key)
                issues.append(
                    f"Unexpected target-only primary key in {tname}: {pk_key} "
                    "(not present in SQLite source)"
                )
                continue

            matched_pk_count += 1
            incoming = source_by_pk[pk_key]
            row_changed = False
            for col_name, val in incoming.items():
                if col_name in pk_cols:
                    continue
                if not _values_equal(existing.get(col_name), val):
                    col_changes[col_name] += 1
                    row_changed = True
            if row_changed:
                rows_to_update += 1
            else:
                rows_unchanged += 1

        if matched_pk_count != expected_cnt and len(existing_rows) == expected_cnt:
            issues.append(
                f"Matched seed PK count mismatch in {tname}: "
                f"expected {expected_cnt}, matched {matched_pk_count}"
            )

        plans[tname] = SeedReconciliationPlan(
            table_name=tname,
            expected_seed_count=expected_cnt,
            target_existing_count=len(existing_rows),
            matched_pk_count=matched_pk_count,
            rows_to_update=rows_to_update,
            rows_unchanged=rows_unchanged,
            rows_to_insert=len(source_rows) - matched_pk_count,
            unexpected_target_pks=unexpected_pks,
            column_change_counts=dict(sorted(col_changes.items())),
        )

    return plans, target_counts, issues


async def run_dry_run(
    source_path: Path,
    *,
    target_url: str | None = None,
    sync_seeded_tables: bool = False,
) -> DryRunReport:
    """Validate source data, conversions, and integrity without modifying target."""
    _import_all_models()
    tables = dict(Base.metadata.tables)
    order, self_refs = compute_dependency_order(tables)
    dataset, stats, read_issues = read_sqlite_dataset(source_path, tables, order)
    val_issues = validate_dataset_integrity(tables, order, dataset)

    report = DryRunReport(
        dependency_order=order,
        self_referential_fks=self_refs,
        table_counts={t: len(dataset.get(t, [])) for t in order},
        conversion_counts=dict(stats),
        issues=read_issues + val_issues,
    )

    if target_url:
        normalized_target = normalize_database_url(target_url)
        eng = build_async_engine(normalized_target)
        try:
            if sync_seeded_tables:
                async with eng.connect() as conn:
                    plans, target_counts, seed_issues = await inspect_seed_reconciliation(
                        conn, tables, order, dataset
                    )
                report.reconciliation_plans = plans
                report.target_existing_counts = target_counts
                report.issues.extend(seed_issues)
            else:
                report.target_existing_counts = await inspect_target_counts(eng, order)
        finally:
            await eng.dispose()
    elif sync_seeded_tables:
        report.issues.append(
            "Target database URL is required when --sync-seeded-tables is specified."
        )

    return report


def _values_equal(existing: Any, incoming: Any) -> bool:
    if isinstance(existing, datetime) and isinstance(incoming, datetime):
        ex_utc = (
            existing.replace(tzinfo=UTC) if existing.tzinfo is None else existing.astimezone(UTC)
        )
        in_utc = (
            incoming.replace(tzinfo=UTC) if incoming.tzinfo is None else incoming.astimezone(UTC)
        )
        return ex_utc == in_utc
    return existing == incoming


async def execute_migration(
    source_path: Path,
    target_url: str,
    *,
    allow_pooler: bool = False,
    sync_seeded_tables: bool = False,
    out_updated_counts: dict[str, int] | None = None,
) -> dict[str, int]:
    """Copy validated records from SQLite (read-only) to target DB in one atomic transaction.

    Without sync_seeded_tables: skips identical existing PKs and aborts/rolls back on any
    conflicting existing PK.

    With sync_seeded_tables=True: verifies exact Alembic seed counts (50 canonical_ingredients,
    59 ingredient_aliases), zero unexpected seed IDs, and emptiness of all other 28 tables,
    then updates matching PKs in ONLY those two seeded tables from SQLite before inserting
    all remaining records in dependency order inside the same atomic transaction.
    """
    normalized_target = normalize_database_url(target_url)
    host = urlsplit(normalized_target).hostname or ""
    if "-pooler." in host and not allow_pooler:
        raise ValueError(
            "Refusing to migrate via pooled Neon endpoint; supply DATABASE_MIGRATION_URL "
            "(direct/unpooled connection)."
        )

    _import_all_models()
    tables = dict(Base.metadata.tables)
    order, self_refs = compute_dependency_order(tables)
    dataset, _stats, read_issues = read_sqlite_dataset(source_path, tables, order)
    val_issues = validate_dataset_integrity(tables, order, dataset)
    all_issues = read_issues + val_issues
    if all_issues:
        raise ValueError(f"Source validation failed ({len(all_issues)} issues): {all_issues[:5]}")

    self_ref_cols_by_table: dict[str, list[str]] = {}
    for tname, col_name, _target_col in self_refs:
        self_ref_cols_by_table.setdefault(tname, []).append(col_name)

    inserted_counts: dict[str, int] = {t: 0 for t in order}
    updated_counts: dict[str, int] = {t: 0 for t in order}
    eng = build_async_engine(normalized_target)
    try:
        async with eng.begin() as conn:
            if sync_seeded_tables:
                _plans, _target_counts, seed_issues = await inspect_seed_reconciliation(
                    conn, tables, order, dataset
                )
                if seed_issues:
                    raise ValueError(
                        f"Seed reconciliation pre-check failed ({len(seed_issues)} issues): "
                        f"{seed_issues[:5]}"
                    )

            deferred_self_updates: list[tuple[Table, dict[str, Any], dict[str, Any]]] = []

            for tname in order:
                tbl = tables[tname]
                rows = dataset.get(tname, [])
                if not rows:
                    continue

                pk_cols = [c.name for c in tbl.primary_key.columns]
                existing_rows = (await conn.execute(select(tbl))).mappings().all()
                existing_by_pk = {tuple(r[c] for c in pk_cols): dict(r) for r in existing_rows}

                self_cols = self_ref_cols_by_table.get(tname, [])
                rows_to_insert: list[dict[str, Any]] = []

                for row in rows:
                    pk_key = tuple(row[c] for c in pk_cols)
                    if pk_key in existing_by_pk:
                        existing = existing_by_pk[pk_key]
                        changed_vals = {
                            col_name: val
                            for col_name, val in row.items()
                            if col_name not in pk_cols
                            and not _values_equal(existing.get(col_name), val)
                        }
                        if changed_vals:
                            if sync_seeded_tables and tname in SEEDED_TABLES_EXPECTED_COUNTS:
                                stmt = tbl.update().values(**changed_vals)
                                for col_name in pk_cols:
                                    stmt = stmt.where(tbl.c[col_name] == row[col_name])
                                await conn.execute(stmt)
                                updated_counts[tname] += 1
                            else:
                                first_col = next(iter(changed_vals))
                                raise ValueError(
                                    f"Refusing to overwrite conflicting record {tname} "
                                    f"pk={pk_key} column={first_col}"
                                )
                        continue

                    if self_cols and any(row[sc] is not None for sc in self_cols):
                        pass1_row = dict(row)
                        update_vals = {sc: row[sc] for sc in self_cols if row[sc] is not None}
                        for sc in self_cols:
                            pass1_row[sc] = None
                        rows_to_insert.append(pass1_row)
                        deferred_self_updates.append(
                            (tbl, {c: row[c] for c in pk_cols}, update_vals)
                        )
                    else:
                        rows_to_insert.append(row)

                if rows_to_insert:
                    await conn.execute(tbl.insert(), rows_to_insert)
                    inserted_counts[tname] = len(rows_to_insert)

            # Pass 2: restore self-referential FKs within the same atomic transaction
            for tbl, pk_filter, update_vals in deferred_self_updates:
                stmt = tbl.update().values(**update_vals)
                for col_name, pk_val in pk_filter.items():
                    stmt = stmt.where(tbl.c[col_name] == pk_val)
                await conn.execute(stmt)
    finally:
        await eng.dispose()

    if out_updated_counts is not None:
        out_updated_counts.update(updated_counts)

    return inserted_counts


def _resolve_target_url_from_env(env_file: Path | None) -> str | None:
    for key in ("DATABASE_MIGRATION_URL", "DATABASE_URL_UNPOOLED"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    if env_file and env_file.exists():
        env_map: dict[str, str] = {}
        for line in env_file.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            env_map[k.strip()] = v.strip().strip('"').strip("'")
        return env_map.get("DATABASE_MIGRATION_URL") or env_map.get("DATABASE_URL_UNPOOLED")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Type-aware SQLite-to-Neon PostgreSQL data migration utility."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=_BACKEND_DIR / "dev_local.db",
        help="Path to read-only SQLite source database.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=_BACKEND_DIR.parent / ".env",
        help="Optional .env file containing DATABASE_MIGRATION_URL or DATABASE_URL_UNPOOLED.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate source data and conversions without writing to target.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute atomic data copy to target PostgreSQL database.",
    )
    parser.add_argument(
        "--check-target",
        action="store_true",
        help="In dry-run mode, also inspect target table counts read-only.",
    )
    parser.add_argument(
        "--sync-seeded-tables",
        action="store_true",
        help=(
            "Explicit opt-in to reconcile matching Alembic-seeded PKs in ONLY "
            "canonical_ingredients (50) and ingredient_aliases (59) from SQLite."
        ),
    )
    args = parser.parse_args()

    if args.execute and args.dry_run:
        print("ERROR: Specify either --dry-run or --execute, not both.", file=sys.stderr)
        return 2
    if not args.execute and not args.dry_run:
        args.dry_run = True

    target_url = _resolve_target_url_from_env(args.env_file)

    if args.dry_run:
        inspect_target = args.check_target or args.sync_seeded_tables
        report = asyncio.run(
            run_dry_run(
                args.source,
                target_url=target_url if inspect_target else None,
                sync_seeded_tables=args.sync_seeded_tables,
            )
        )
        print(f"Dry-run OK: {report.ok}")
        print(f"Tables ({len(report.dependency_order)}): {report.dependency_order}")
        print(f"Self-referential FKs: {report.self_referential_fks}")
        print(f"Total rows: {sum(report.table_counts.values())}")
        print("Row counts per table:")
        for tname in report.dependency_order:
            extra = (
                f" (target_existing={report.target_existing_counts.get(tname, 0)})"
                if report.target_existing_counts
                else ""
            )
            print(f"  {tname}: {report.table_counts[tname]}{extra}")
        print(f"Type conversions: {report.conversion_counts}")
        if report.reconciliation_plans:
            print("Seed reconciliation plan:")
            non_seeded_non_empty = [
                t
                for t, c in report.target_existing_counts.items()
                if t not in SEEDED_TABLES_EXPECTED_COUNTS and c > 0
            ]
            print(f"  Other 28 tables empty: {len(non_seeded_non_empty) == 0}")
            for tname, plan in report.reconciliation_plans.items():
                print(
                    f"  {tname}: expected={plan.expected_seed_count}, "
                    f"existing={plan.target_existing_count}, "
                    f"matched_pks={plan.matched_pk_count}, "
                    f"unexpected_pks={len(plan.unexpected_target_pks)}, "
                    f"to_update={plan.rows_to_update}, "
                    f"unchanged={plan.rows_unchanged}, "
                    f"to_insert={plan.rows_to_insert}"
                )
                print(f"    column_changes: {plan.column_change_counts}")
        if report.issues:
            print(f"Issues ({len(report.issues)}):")
            for issue in report.issues:
                print(f"  - {issue}")
            return 1
        return 0

    if not target_url:
        print("ERROR: No DATABASE_MIGRATION_URL or DATABASE_URL_UNPOOLED found.", file=sys.stderr)
        return 2

    updated: dict[str, int] = {}
    inserted = asyncio.run(
        execute_migration(
            args.source,
            target_url,
            sync_seeded_tables=args.sync_seeded_tables,
            out_updated_counts=updated,
        )
    )
    print("Migration committed successfully.")
    for tname in inserted:
        upd = updated.get(tname, 0)
        ins = inserted[tname]
        if upd > 0 or ins > 0:
            print(f"  {tname}: updated {upd}, inserted {ins}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
