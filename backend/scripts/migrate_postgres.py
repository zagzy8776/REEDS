"""Safe PostgreSQL-to-PostgreSQL migration for REEDS.

Copies relational data between two standard PostgreSQL databases using
native binary COPY (bulk, low memory) and verifies row counts per table.

Guarantees:
  * streaming bulk copy — never materializes tables in Python memory
  * preserves IDs, timestamps, JSON, LargeBinary, predictions, snapshots,
    learning context and the model registry
  * never prints, commits, or mutates the source database
  * the target schema must exist first (run the app's alembic migrations /
    init_db on the target before importing)
  * prints only masked URLs (passwords always redacted)

Usage:
  export : python migrate_postgres.py export --source "$SRC_URL" --out ./backup
  import : python migrate_postgres.py import-data --target "$DST_URL" --in ./backup
  verify : python migrate_postgres.py import-data --target "$DST_URL" --in ./backup --verify
  all-in-1: python migrate_postgres.py migrate --source "$SRC_URL" --target "$DST_URL"
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