import sqlite3
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config


async def test_health_endpoints(client: httpx.AsyncClient) -> None:
    resp_root = await client.get("/health")
    assert resp_root.status_code == 200
    assert resp_root.json() == {"status": "ok", "db": "connected"}
    assert resp_root.headers.get("X-Request-Id", "").startswith("req_")

    resp_v1 = await client.get("/api/v1/health")
    assert resp_v1.status_code == 200
    assert resp_v1.json() == {"status": "ok", "db": "connected"}


def test_alembic_upgrade_head_on_temp_sqlite(tmp_path: Path) -> None:
    db_file = tmp_path / "migration_check.db"
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_file}")

    command.upgrade(alembic_cfg, "head")

    with sqlite3.connect(db_file) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert {"users", "households", "household_members", "alembic_version"} <= tables
