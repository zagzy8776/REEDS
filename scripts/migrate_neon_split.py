"""Neon -> Aiven/Cockroach split migration (Phase 2).

READS from Neon (legacy DATABASE_URL / SOURCE_DATABASE_URL) and WRITES to
Aiven (hot/transactional) + Cockroach (cold/analytical). NEVER writes to,
deletes from, or modifies Neon.

Fail-closed rules (any violation -> exit non-zero, no partial writes beyond
the current batch transaction which is rolled back):
  * selected destination URL missing -> stop (no DATABASE_URL fallback for
    Cockroach; no Turso fallback ever).
  * source/destination connection failure -> stop.
  * destination table missing or destination missing a source column -> stop
    (run the Alembic baselines first; the script never auto-creates schema).
  * --dry-run performs zero destination writes.

Idempotency: every batch upserts on the primary key (explicit ``id``
preserved), so re-running after a stop/resume converges without duplicates.
Safe resume: keyset pagination (WHERE id > last ORDER BY id) plus
--start-after TABLE:ID and --tables filters, plus --limit-rows smoke cap.

Fixtures: deterministic 730-day boundary (see migration_common).
  match_date >= cutoff -> Aiven.fixtures ; older -> Cockroach.fixtures_archive
  NULL match_date      -> Aiven (live board is the safe side; flagged later).

model_artifacts.data is forced to b"" (metadata-only; the pickled bundle
stays in object storage / the training workers, never in the row stream).

Usage (DO NOT RUN until the real URLs are provisioned and baselines applied):
  python scripts/migrate_neon_split.py --dry-run
  python scripts/migrate_neon_split.py --reference-date 2026-09-12 --batch-size 500

Real migration command (Phase 3 day, after dry-run + verify are green):
  SOURCE_DATABASE_URL='<neon-readonly-url>' AIVEN_DATABASE_URL='<aiven-url>' \\
    COCKROACH_DATABASE_URL='<cockroach-url>' \\
    python scripts/migrate_neon_split.py --reference-date <YYYY-MM-DD>

No secret is ever printed: URLs are redacted, env values are never logged.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from migration_common import (  # noqa: E402
    AIVEN_TABLES,
    COCKROACH_TABLES,
    MIGRATION_ORDER_AIVEN,
    MIGRATION_ORDER_COCKROACH,
    batched,
    fixture_cutoff,
    fixture_destination,
    redact_url,
    sanitize_row,
)

log = logging.getLogger("migrate_neon_split")

DEST_FOR_TABLE: dict[str, str] = {t: "aiven" for t in AIVEN_TABLES}
for _t in COCKROACH_TABLES:
    if _t != "fixtures_archive":
        DEST_FOR_TABLE[_t] = "cockroach"

TABLE_CHUNK_DEFAULT = 500


def _get_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def resolve_urls(args) -> tuple[str, str, str]:
    """Fail-closed URL resolution. No cross-role fallback for Cockroach."""
    source = _get_env("SOURCE_DATABASE_URL") or _get_env("DATABASE_URL")
    aiven = _get_env("AIVEN_DATABASE_URL")
    cockroach = _get_env("COCKROACH_DATABASE_URL")
    missing = []
    if args.dest in ("aiven", "both") and not aiven:
        missing.append("AIVEN_DATABASE_URL")
    if args.dest in ("cockroach", "both") and not cockroach:
        missing.append("COCKROACH_DATABASE_URL")
    if not source:
        missing.append("SOURCE_DATABASE_URL (or DATABASE_URL as Neon source)")
    if missing:
        raise SystemExit(
            "FAIL-CLOSED: missing required connection strings: "
            + ", ".join(missing)
            + ". Refusing to guess or fall back to another database."
        )
    return source, aiven, cockroach


def _norm_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def make_engine(url: str, read_only: bool = False, label: str = "database"):
    from sqlalchemy import text

    from migration_common import connect_with_retry

    if not url:
        raise SystemExit("FAIL-CLOSED: empty URL passed to make_engine.")
    eng = connect_with_retry(url, label)
    try:
        with eng.connect() as conn:
            # PostgreSQL-only harderning of the source: Neon must stay untouched.
            # SQLite scratch sources (unit tests) skip this since the setting is
            # a Postgres session feature, not a standard SQL statement.
            if read_only and eng.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
    except Exception as exc:
        raise SystemExit(
            f"FAIL-CLOSED: cannot connect to {redact_url(url)}: "
            f"{type(exc).__name__}"
        )
    return eng


def table_columns(eng, table: str) -> list[str]:
    from sqlalchemy import inspect as sa_inspect

    cols = sa_inspect(eng).get_columns(table)
    if not cols:
        raise SystemExit(
            f"FAIL-CLOSED: destination table '{table}' does not exist. "
            "Apply the Alembic baselines first; this script never creates schema."
        )
    return [c["name"] for c in cols]


def check_schema_compatible(src_cols, dest_cols, table) -> None:
    missing = [c for c in src_cols if c not in dest_cols]
    # model_artifacts.data may legitimately differ (bytea vs blob); it is
    # forced to b"" anyway, so only fail when a real column is absent.
    real_missing = [c for c in missing if not (table == "model_artifacts" and c == "data")]
    if real_missing:
        raise SystemExit(
            f"FAIL-CLOSED: schema mismatch for '{table}': destination is "
            f"missing columns {real_missing}. Migrate schema first."
        )


def fetch_batches(src_eng, table, cols, batch_size, start_after=0, limit_rows=0):
    """Keyset-paginated read: never loads the whole table into RAM."""
    from sqlalchemy import text

    collist = ", ".join(f'"{c}"' for c in cols)
    last = start_after
    yielded = 0
    while True:
        remaining = None
        if limit_rows:
            remaining = limit_rows - yielded
            if remaining <= 0:
                return
        chunk = min(batch_size, remaining) if remaining else batch_size
        sql = text(
            f'SELECT {collist} FROM "{table}" WHERE id > :last '
            f"ORDER BY id ASC LIMIT :lim"
        )
        with src_eng.connect() as conn:
            rows = conn.execute(sql, {"last": last, "lim": chunk}).mappings().all()
        if not rows:
            return
        yield [dict(r) for r in rows]
        yielded += len(rows)
        last = max(int(r["id"]) for r in rows)
        if len(rows) < chunk:
            return


def upsert_batch(dest_eng, table, dest_cols, rows):
    """Idempotent per-batch upsert on PK inside one transaction."""
    import json

    from sqlalchemy import text

    if not rows:
        return 0

    def _coerce(value):
        # JSON columns on PostgreSQL arrive as dict/list from the source
        # driver; psycopg cannot adapt those into a text() INSERT, so serialize
        # them explicitly (destination jsonb accepts the JSON text). SQLite
        # accepts the JSON string equally well.
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str, separators=(",", ":"))
        return value

    prepared = [{key: _coerce(value) for key, value in row.items()} for row in rows]
    cols = [c for c in dest_cols if c in prepared[0]]
    collist = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    non_pk = [c for c in cols if c != "id"]
    if non_pk:
        updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in non_pk)
        sql = text(
            f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders}) '
            f'ON CONFLICT (id) DO UPDATE SET {updates}'
        )
    else:  # pragma: no cover - PK-only tables do not exist today
        sql = text(
            f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders}) '
            "ON CONFLICT (id) DO NOTHING"
        )
    # SQLite fallback for unit tests (no ON CONFLICT ... DO UPDATE path
    # differences matter here only for scratch DBs).
    if dest_eng.dialect.name == "sqlite":
        cols_q = ", ".join(f'"{c}"' for c in cols)
        ph = ", ".join(f":{c}" for c in cols)
        sql = text(f'INSERT OR REPLACE INTO "{table}" ({cols_q}) VALUES ({ph})')
    with dest_eng.begin() as conn:
        conn.execute(sql, prepared)
    return len(rows)


def migrate_table(src_eng, dest_eng, table, dest_table, args, cutoff, stats):
    from sqlalchemy import inspect as sa_inspect

    src_cols = [c["name"] for c in sa_inspect(src_eng).get_columns(table)]
    try:
        dest_cols = table_columns(dest_eng, dest_table)
    except Exception:
        raise SystemExit(
            f"FAIL-CLOSED: destination table '{dest_table}' does not exist. "
            "Apply the Alembic baselines first."
        )
    check_schema_compatible(src_cols, dest_cols, table)
    start_after = 0
    if args.start_after and args.start_after.startswith(table + ":"):
        try:
            start_after = int(args.start_after.split(":", 1)[1])
        except ValueError:
            start_after = 0
    total = 0
    for chunk in fetch_batches(src_eng, table, src_cols, args.batch_size,
                               start_after=start_after,
                               limit_rows=args.limit_rows or 0):
        prepared = []
        for row in chunk:
            cleaned = sanitize_row(table, row)
            if table == "fixtures":
                dest = fixture_destination(cleaned.get("match_date"), cutoff)
                if (dest_table == "fixtures" and dest != "aiven") or (
                        dest_table == "fixtures_archive" and dest != "cockroach"):
                    continue  # belongs to the other fixture partition
            keep = {c: cleaned.get(c) for c in dest_cols if c in cleaned}
            prepared.append(keep)
        if args.dry_run:
            total += len(prepared)
            log.info("[dry-run] %s -> %s: would upsert %d rows (batch)",
                     table, dest_table, len(prepared))
            continue
        n = upsert_batch(dest_eng, dest_table, dest_cols, prepared)
        total += n
        log.info("%s -> %s: upserted batch of %d (total %d)", table, dest_table, n, total)
        stats[dest_table] = stats.get(dest_table, 0) + n
    if args.dry_run:
        log.info("[dry-run] %s -> %s: total %d rows (no writes)", table, dest_table, total)
    else:
        log.info("%s -> %s: DONE total %d rows", table, dest_table, total)
    return total


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Neon -> Aiven/Cockroach split migration")
    p.add_argument("--dry-run", action="store_true",
                   help="read Neon and log what would move; zero destination writes")
    p.add_argument("--dest", choices=["aiven", "cockroach", "both"], default="both")
    p.add_argument("--tables", default="",
                   help="comma-separated subset of source tables (default: all)")
    p.add_argument("--batch-size", type=int, default=TABLE_CHUNK_DEFAULT)
    p.add_argument("--limit-rows", type=int, default=0,
                   help="cap rows per table (smoke tests only)")
    p.add_argument("--start-after", default="",
                   help="resume cursor TABLE:ID, e.g. predictions:9000")
    p.add_argument("--reference-date", default="",
                   help="YYYY-MM-DD pinning the fixture cutoff (default: today)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    if args.batch_size <= 0:
        raise SystemExit("FAIL-CLOSED: --batch-size must be positive.")
    if args.reference_date:
        try:
            ref = date.fromisoformat(args.reference_date)
        except ValueError:
            raise SystemExit("FAIL-CLOSED: --reference-date must be YYYY-MM-DD.")
    else:
        ref = date.today()
    cutoff = fixture_cutoff(ref)
    log.info("fixture cutoff (730d, ref=%s): match_date >= %s -> Aiven, older -> Cockroach",
             ref.isoformat(), cutoff.isoformat())

    source_url, aiven_url, cockroach_url = resolve_urls(args)
    log.info("source: %s", redact_url(source_url))
    if args.dest in ("aiven", "both"):
        log.info("aiven destination: %s", redact_url(aiven_url))
    if args.dest in ("cockroach", "both"):
        log.info("cockroach destination: %s", redact_url(cockroach_url))
    if args.dry_run:
        log.info("DRY-RUN mode: no destination writes will occur.")

    subset = {t.strip() for t in (args.tables or "").split(",") if t.strip()}
    if subset:
        unknown = subset - set(DEST_FOR_TABLE) - {"fixtures"}
        if unknown:
            raise SystemExit(f"FAIL-CLOSED: unknown --tables entries: {sorted(unknown)}")

    # Connect lazily, destination-by-destination, so a bad Cockroach URL can
    # never route historical rows into Aiven (and vice versa).
    stats: dict[str, int] = {}
    if args.dest in ("aiven", "both"):
        src_eng = make_engine(source_url, read_only=True, label="source (Neon)")
        aiven_eng = make_engine(aiven_url, label="aiven")
        for table in MIGRATION_ORDER_AIVEN:
            if subset and table not in subset:
                continue
            migrate_table(src_eng, aiven_eng, table, table, args, cutoff, stats)
        # Archived fixtures live in Cockroach under fixtures_archive.
        if (not subset or "fixtures" in subset) and args.dest == "both":
            cock_eng = make_engine(cockroach_url, label="cockroach")
            migrate_table(src_eng, cock_eng, "fixtures", "fixtures_archive",
                          args, cutoff, stats)
            cock_eng.dispose()
        src_eng.dispose()
        aiven_eng.dispose()
    if args.dest == "cockroach":
        src_eng = make_engine(source_url, read_only=True, label="source (Neon)")
        cock_eng = make_engine(cockroach_url, label="cockroach")
        migrate_table(src_eng, cock_eng, "fixtures", "fixtures_archive",
                      args, cutoff, stats)
        for table in ("historical_evaluation", "backtest_runs"):
            if subset and table not in subset:
                continue
            migrate_table(src_eng, cock_eng, table, table, args, cutoff, stats)
        src_eng.dispose()
        cock_eng.dispose()
    if args.dest == "both":
        # Cockroach-only tables (fixtures_archive already done above).
        src_eng = make_engine(source_url, read_only=True, label="source (Neon)")
        cock_eng = make_engine(cockroach_url, label="cockroach")
        for table in ("historical_evaluation", "backtest_runs"):
            if subset and table not in subset:
                continue
            migrate_table(src_eng, cock_eng, table, table, args, cutoff, stats)
        src_eng.dispose()
        cock_eng.dispose()

    log.info("summary: %s", stats if stats else "no tables migrated")
    log.info("Neon source was never written to (read-only access only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



