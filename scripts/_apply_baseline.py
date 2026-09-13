"""ONE-OFF: apply a Phase 2 baseline schema to a destination database.

Schema-only DDL (op.create_table). No data is read, written, or moved.
Usage: python _apply_baseline.py aiven|cockroach
Reads the target URL from the environment (AIVEN_DATABASE_URL /
COCKROACH_DATABASE_URL). Fails closed if missing. Records the applied
revision in alembic_version so re-runs are idempotent.

Requires the migration scripts dir on sys.path to import migration_common:
    cd scripts && python _apply_baseline.py aiven
or:
    PYTHONPATH=$PWD/scripts python _apply_baseline.py aiven
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load E:\REEDS-main\REEDS-main\.env so bare `python _apply_baseline.py aiven`
# works from any cwd. Real env vars still win (override=False).
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass

BASELINE_DIR = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "baselines"

NORM = {
    "postgresql://": "postgresql+psycopg://",
    "postgres://": "postgresql+psycopg://",
}


def norm(url: str) -> str:
    for old, new in NORM.items():
        if url.startswith(old):
            return url.replace(old, new, 1)
    return url


def load_baseline(target: str):
    path = BASELINE_DIR / f"{target}_baseline_0001.py"
    if not path.exists():
        raise SystemExit(f"FAIL-CLOSED: baseline file not found: {path}")
    spec = importlib.util.spec_from_file_location(f"_baseline_{target}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, path


def main() -> int:
    import sqlalchemy as sa
    from sqlalchemy import create_engine, inspect, text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    if len(sys.argv) != 2 or sys.argv[1] not in ("aiven", "cockroach"):
        print("usage: python _apply_baseline.py aiven|cockroach")
        return 2
    target = sys.argv[1]
    env_key = f"{target.upper()}_DATABASE_URL"
    url = os.environ.get(env_key, "").strip()
    if not url:
        print(f"FAIL-CLOSED: {env_key} is not set in the environment.")
        return 1

    mod, path = load_baseline(target)
    revision = mod.revision

    from migration_common import connect_with_retry

    eng = connect_with_retry(url, env_key)

    insp = inspect(eng)
    tables_before = set(insp.get_table_names())

    if "alembic_version" in tables_before:
        with eng.connect() as conn:
            stamped = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if revision in stamped:
            print(f"{target}: baseline {revision} already applied - nothing to do.")
            return 0
        print(f"FAIL-CLOSED: alembic_version exists with {stamped}, expected {revision}.")
        return 1

    pre_existing = tables_before - {"alembic_version"}
    if pre_existing:
        print(f"FAIL-CLOSED: {target} already has tables {sorted(pre_existing)}; "
              "refusing to layer a baseline on a non-empty database.")
        return 1

    with eng.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            conn.execute(sa.text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            ))
            try:
                mod.upgrade()
                conn.execute(sa.text(
                    "INSERT INTO alembic_version (version_num) VALUES (:rev)"
                ), {"rev": revision})
            except Exception:
                # transaction rolls back both DDL and the version stamp
                raise

    after = set(inspect(eng).get_table_names())
    print(f"{target}: baseline {revision} applied. Tables now ({len(after)}): "
          f"{', '.join(sorted(after))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
