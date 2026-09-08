"""Safe PostgreSQL-to-PostgreSQL migration for REEDS.

Copies relational data between two standard PostgreSQL databases using
native binary COPY (bulk, low memory) and verifies row counts per table.

Guarantees:
  * streaming bulk copy — never materializes tables in Python memory
  * preserves IDs, timestamps, JSON, LargeBinary, predictions, snapshots,
    learning context and the model registry
  * never prints, commits, or mutates the source database
  * the target schema must exist first (run the app's alembic migrations on
    the target before importing)
  * prints only masked URLs (passwords always redacted)

Usage:
  all-in-1: python migrate_postgres.py migrate --source "$SRC" --target "$DST"
  export:   python migrate_postgres.py export --source "$SRC" --out ./backup
  import:   python migrate_postgres.py import-data --target "$DST" --into ./backup --verify
"""

from __future__ import annotations

import argparse
from pathlib import Path

EXCLUDED_TABLES = {"alembic_version"}


def masked_url(url: str) -> str:
    """Redact password in a connection URL for console output."""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        netloc = parts.netloc
        if "@" in netloc:
            userinfo, host = netloc.rsplit("@", 1)
            user = userinfo.split(":", 1)[0]
            netloc = f"{user}:***@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return "<url>"


def connect(url: str):
    import psycopg

    return psycopg.connect(url, connect_timeout=30)


def tables_list(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        return [row[0] for row in cur.fetchall() if row[0] not in EXCLUDED_TABLES]


def table_count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Table ordering: copy parents before children so FKs are never violated.
# ---------------------------------------------------------------------------


def fk_dependencies(conn) -> dict[str, set[str]]:
    """Map each public table to the set of public tables its FKs reference."""
    deps: dict[str, set[str]] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT conrelid::regclass::text, confrelid::regclass::text
            FROM pg_constraint
            WHERE contype = 'f'
              AND connamespace = 'public'::regnamespace
            """
        )
        for child, parent in cur.fetchall():
            child = child.split(".")[-1].strip('"')
            parent = parent.split(".")[-1].strip('"')
            if child in EXCLUDED_TABLES or parent in EXCLUDED_TABLES:
                continue
            deps.setdefault(child, set()).add(parent)
    return deps


def topo_order(conn) -> list[str]:
    """Dependency-first order (Kahn). Cycles (if any) fall back to name order."""
    tables = tables_list(conn)
    table_set = set(tables)
    fk_deps = fk_dependencies(conn)
    deps = {
        t: {d for d in fk_deps.get(t, set()) if d in table_set and d != t}
        for t in tables
    }
    ordered: list[str] = []
    remaining = sorted(tables)
    while remaining:
        ready = [t for t in remaining if not (deps[t] - set(ordered))]
        if not ready:
            print(f"WARN: FK cycle among tables; copying rest in name order: {remaining}")
            ordered.extend(remaining)
            break
        for t in sorted(ready):
            ordered.append(t)
        remaining = [t for t in remaining if t not in set(ordered)]
    return ordered


def copy_table(src_conn, dst_conn, table: str) -> int:
    """Stream one table source->target via binary COPY. Returns row count."""
    rows = 0
    with src_conn.cursor() as src_cur, dst_conn.cursor() as dst_cur:
        with src_cur.copy(f'COPY "{table}" TO STDOUT (FORMAT BINARY)') as src_copy:
            with dst_cur.copy(f'COPY "{table}" FROM STDIN (FORMAT BINARY)') as dst_copy:
                for row in src_copy:
                    dst_copy.write_row(row)
                    rows += 1
    return rows


def reset_sequences(conn) -> list[str]:
    """Advance identity sequences past imported IDs so future inserts work."""
    fixed: list[str] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name,
                   pg_get_serial_sequence(quote_ident(table_name), column_name)
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND column_default LIKE 'nextval(%'
            """
        )
        for table, column, seq in cur.fetchall():
            if not seq:
                continue
            cur.execute(f'SELECT COALESCE(MAX("{column}"), 0) FROM "{table}"')
            max_id = int(cur.fetchone()[0])
            if max_id > 0:
                cur.execute("SELECT setval(%s, %s)", (seq, max_id))
            else:
                cur.execute("SELECT setval(%s, 1, false)", (seq,))
            fixed.append(table)
    return fixed


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_counts(src_conn, dst_conn, tables: list[str] | None = None) -> bool:
    """Compare row counts per table; print a report; return overall pass/fail."""
    tables = tables or tables_list(src_conn)
    ok = True
    print("\n=== ROW COUNT VERIFICATION ===")
    for table in tables:
        try:
            s = table_count(src_conn, table)
        except Exception as exc:
            print(f"  {table:32s} source read FAILED: {exc}")
            ok = False
            continue
        try:
            d = table_count(dst_conn, table)
        except Exception as exc:
            print(f"  {table:32s} target read FAILED: {exc}")
            ok = False
            continue
        status = "OK" if s == d else "MISMATCH"
        if s != d:
            ok = False
        print(f"  {table:32s} source={s:>8d}  target={d:>8d}  {status}")
    print(f"=== RESULT: {'PASS' if ok else 'FAIL'} ===")
    return ok


def _check_target_schema(dst_conn) -> list[str]:
    tables = tables_list(dst_conn)
    if not tables:
        raise SystemExit(
            "Target database has no public tables. Run 'alembic upgrade head' "
            "with DATABASE_URL pointed at the target first, then re-run."
        )
    return tables


def cmd_export(args) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_conn = connect(args.source)
    try:
        order = topo_order(src_conn)
        import json

        manifest: dict[str, int] = {}
        for table in order:
            path = out_dir / f"{table}.copybin"
            rows = 0
            with path.open("wb") as fh:
                with src_conn.cursor() as cur:
                    with cur.copy(f'COPY "{table}" TO STDOUT (FORMAT BINARY)') as cp:
                        for chunk in cp:
                            fh.write(bytes(chunk))
                            rows += 1
            manifest[table] = rows
            print(f"  exported {table:32s} {rows:>8d} rows")
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"Export complete -> {out_dir} (manifest.json written)")
        return 0
    finally:
        src_conn.close()


def cmd_import(args) -> int:
    in_dir = Path(args.into)
    manifest_path = in_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"missing manifest: {manifest_path}")
    import json

    manifest: dict[str, int] = json.loads(manifest_path.read_text())
    dst_conn = connect(args.target)
    try:
        _check_target_schema(dst_conn)
        existing = set(tables_list(dst_conn))
        for table, expected in manifest.items():
            if table not in existing:
                print(f"  SKIP {table}: table missing on target")
                continue
            path = in_dir / f"{table}.copybin"
            if not path.is_file():
                print(f"  SKIP {table}: missing data file")
                continue
            with path.open("rb") as fh:
                with dst_conn.cursor() as cur:
                    with cur.copy(f'COPY "{table}" FROM STDIN (FORMAT BINARY)') as cp:
                        while True:
                            chunk = fh.read(1 << 20)
                            if not chunk:
                                break
                            cp.write(chunk)
            print(f"  imported {table:32s} {expected:>8d} expected rows")
        dst_conn.commit()
        fixed = reset_sequences(dst_conn)
        dst_conn.commit()
        print(f"Sequences advanced for {len(fixed)} tables")
        if args.verify:
            ok = True
            print("\n=== ROW COUNT VERIFICATION (vs manifest) ===")
            for table, expected in manifest.items():
                try:
                    actual = table_count(dst_conn, table)
                except Exception as exc:
                    print(f"  {table:32s} read FAILED: {exc}")
                    ok = False
                    continue
                status = "OK" if actual == expected else "MISMATCH"
                if actual != expected:
                    ok = False
                print(f"  {table:32s} expected={expected:>8d}  actual={actual:>8d}  {status}")
            print(f"=== RESULT: {'PASS' if ok else 'FAIL'} ===")
            if not ok:
                return 1
        return 0
    finally:
        dst_conn.close()


def cmd_migrate(args) -> int:
    src_conn = connect(args.source)
    dst_conn = connect(args.target)
    try:
        _check_target_schema(dst_conn)
        order = topo_order(src_conn)
        print(f"Migrating {len(order)} tables (dependency order):")
        for table in order:
            rows = copy_table(src_conn, dst_conn, table)
            print(f"  {table:32s} {rows:>8d} rows")
        dst_conn.commit()
        fixed = reset_sequences(dst_conn)
        dst_conn.commit()
        print(f"Sequences advanced for {len(fixed)} tables")
        if not verify_counts(src_conn, dst_conn):
            print("ERROR: row-count verification failed; investigate before cutover.")
            return 1
        print("Migration verified OK. Source database was NOT modified.")
        return 0
    finally:
        src_conn.close()
        dst_conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="REEDS PostgreSQL-to-PostgreSQL migration")
    sub = parser.add_subparsers(dest="command", required=True)

    p_migrate = sub.add_parser("migrate", help="stream source -> target directly and verify")
    p_migrate.add_argument("--source", required=True)
    p_migrate.add_argument("--target", required=True)
    p_migrate.set_defaults(func=cmd_migrate)

    p_export = sub.add_parser("export", help="dump all tables to a local directory")
    p_export.add_argument("--source", required=True)
    p_export.add_argument("--out", default="./reeds_backup")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import-data", help="load an export directory into the target")
    p_import.add_argument("--target", required=True)
    p_import.add_argument("--into", default="./reeds_backup")
    p_import.add_argument("--verify", action="store_true")
    p_import.set_defaults(func=cmd_import)

    args = parser.parse_args()
    if hasattr(args, "source"):
        print(f"source: {masked_url(args.source)}")
    if hasattr(args, "target"):
        print(f"target: {masked_url(args.target)}")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

