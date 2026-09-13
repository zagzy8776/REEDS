"""Shared pure helpers for Neon -> Aiven/Cockroach migration (Phase 2).

No DB connections, no network, no secrets. Imported by both migration
scripts and fully unit-testable with scratch SQLite databases.

Ownership: single source of truth is backend/app/db/roles.py TABLE_ROLES.
A test asserts the mapping below stays in sync with it.

Fixture boundary (deterministic):
    cutoff_date = reference_date - 730 days
    match_date >= cutoff -> Aiven (hot, inclusive so no row is lost)
    match_date <  cutoff -> Cockroach (archived fixtures table)
    match_date IS NULL   -> Aiven (safe side; verifier flags null_match_date)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

FIXTURES_HOT_WINDOW_DAYS = 730

AIVEN_TABLES: tuple[str, ...] = (
    "community_comments",
    "community_plays",
    "community_reactions",
    "fixtures",
    "insider_signals",
    "market_evidence",
    "match_events",
    "match_lineups",
    "model_artifacts",
    "model_feedback",
    "model_versions",
    "odds_snapshots",
    "predictions",
    "push_subscriptions",
    "rejected_fixtures",
    "team_aliases",
    "teams",
    "user_follows",
    "user_predictions",
    "user_subscriptions",
    "win_slips",
)

COCKROACH_TABLES: tuple[str, ...] = (
    "backtest_runs",
    "fixtures_archive",
    "historical_evaluation",
)

MIGRATION_ORDER_AIVEN: tuple[str, ...] = (
    "fixtures",
    "teams",
    "team_aliases",
    "model_versions",
    "model_artifacts",
    "predictions",
    "odds_snapshots",
    "market_evidence",
    "model_feedback",
    "match_events",
    "match_lineups",
    "insider_signals",
    "user_predictions",
    "community_comments",
    "community_reactions",
    "community_plays",
    "win_slips",
    "user_follows",
    "user_subscriptions",
    "push_subscriptions",
    "rejected_fixtures",
)

MIGRATION_ORDER_COCKROACH: tuple[str, ...] = (
    "fixtures_archive",
    "historical_evaluation",
    "backtest_runs",
)

NATURAL_KEYS: dict = {
    "fixtures": ("sport", "league", "match_date", "home_team", "away_team"),
    "fixtures_archive": ("sport", "league", "match_date", "home_team", "away_team"),
    "teams": ("sport", "canonical_name"),
    "team_aliases": ("sport", "alias_key"),
    "predictions": None,
    "odds_snapshots": None,
    "market_evidence": ("sport", "market"),
    "model_versions": None,
    "model_artifacts": ("sport", "filename"),  # uq_model_artifact in the ORM
    "model_feedback": ("prediction_id",),
    "user_predictions": None,
    "community_comments": None,
    "community_reactions": None,
    "community_plays": None,
    "win_slips": None,
    "user_subscriptions": ("email",),  # unique=True on email in the ORM
    "push_subscriptions": ("endpoint",),
    "match_events": ("fixture_id", "event_type", "minute", "team", "player"),
    "match_lineups": ("fixture_id", "team", "player"),
    "insider_signals": ("fixture_id", "signal_type", "source"),
    "rejected_fixtures": None,
    "user_follows": ("username", "entity_type", "entity_value"),
    "backtest_runs": None,
    "historical_evaluation": ("fixture_id", "market", "model_version_id", "fold_index"),
}

LOGICAL_REFS = (
    ("predictions", "fixture_id", "fixtures", "id", ("aiven", "cockroach_archive")),
    ("odds_snapshots", "fixture_id", "fixtures", "id", ("aiven", "cockroach_archive")),
    ("odds_snapshots", "prediction_id", "predictions", "id", ("aiven",)),
    ("model_feedback", "prediction_id", "predictions", "id", ("aiven",)),
    ("model_feedback", "fixture_id", "fixtures", "id", ("aiven", "cockroach_archive")),
    ("user_predictions", "fixture_id", "fixtures", "id", ("aiven", "cockroach_archive")),
    ("match_events", "fixture_id", "fixtures", "id", ("aiven", "cockroach_archive")),
    ("match_lineups", "fixture_id", "fixtures", "id", ("aiven", "cockroach_archive")),
    ("insider_signals", "fixture_id", "fixtures", "id", ("aiven", "cockroach_archive")),
    ("team_aliases", "team_id", "teams", "id", ("aiven",)),
    ("historical_evaluation", "fixture_id", "fixtures", "id",
     ("cockroach_archive", "aiven")),
    ("community_comments", "prediction_id", "predictions", "id", ("aiven",)),
    ("community_reactions", "prediction_id", "predictions", "id", ("aiven",)),
    ("community_plays", "prediction_id", "predictions", "id", ("aiven",)),
    ("win_slips", "prediction_id", "predictions", "id", ("aiven",)),
)


def fixture_cutoff(reference=None):
    """Deterministic hot/cold boundary. Defaults to today; pass explicit
    date (--reference-date) for reproducible runs."""
    ref = reference or date.today()
    return ref - timedelta(days=FIXTURES_HOT_WINDOW_DAYS)


def fixture_destination(match_date, cutoff):
    """'aiven' (hot) or 'cockroach' (archived) for one fixture row."""
    if match_date is None:
        return "aiven"
    if isinstance(match_date, str):
        try:
            match_date = date.fromisoformat(match_date[:10])
        except ValueError:
            return "aiven"
    if isinstance(match_date, datetime):
        match_date = match_date.date()
    if not isinstance(match_date, date):
        return "aiven"
    return "aiven" if match_date >= cutoff else "cockroach"


def batched(rows, batch_size=500):
    """Yield successive fixed-size batch lists from an iterable of rows."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    batch = []
    for item in rows:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def sanitize_row(table, row):
    """Destination-safe copy. model_artifacts.data forced to b'' (metadata
    only — the multi-MB pickle must never travel; Render OOM history)."""
    cleaned = dict(row)
    if table == "model_artifacts":
        cleaned["data"] = b""
    return cleaned


def redact_url(url):
    """Redact a DB URL for logs: 'scheme://***@host/db' shape only."""
    if not url:
        return "<missing>"
    try:
        parts = urlsplit(url)
        host = parts.hostname or "?"
        path = parts.path or ""
        scheme = parts.scheme or "?"
        return f"{scheme}://***@{host}{path}"
    except Exception:
        return "<unparseable>"


def redact_mapping(mapping):
    """Copy a mapping, replacing anything that smells like a secret."""
    markers = ("password", "passwd", "pwd", "secret", "token", "api_key",
               "apikey", "database_url", "db_url", "connection")
    out = {}
    for key, value in mapping.items():
        if any(m in str(key).lower() for m in markers):
            out[key] = "***REDACTED***"
        else:
            out[key] = value
    return out


def norm_sqlalchemy_url(url, label="database"):
    """Normalize a plain DB URL to an explicit SQLAlchemy driver dialect.

    postgres:// / postgresql:// -> postgresql+psycopg:// (psycopg 3).
    Cockroach-role URLs -> cockroachdb:// (sqlalchemy-cockroachdb), because
    the stock PG dialect cannot parse `CockroachDB CCL vXX` version strings
    and would raise during engine init. Everything else passes through
    unchanged (sqlite://, etc.).
    """
    low = (label or "").lower()
    if low.startswith("cockroach"):
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "cockroachdb://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "cockroachdb://", 1)
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def _doh_resolve_a(host):
    """Resolve an A record via DNS-over-HTTPS (dns.google).

    Used only when the local resolver fails for a cloud DB host (observed:
    'getaddrinfo failed' while nslookup/DoH resolve the same name). Returns
    the first IPv4 address or ``None``. No secrets are involved — hostnames
    only.
    """
    import json
    from urllib.request import urlopen

    try:
        with urlopen(
            f"https://dns.google/resolve?name={host}&type=A", timeout=10
        ) as resp:
            data = json.loads(resp.read().decode())
        for answer in data.get("Answer", []):
            if answer.get("type") == 1 and answer.get("data"):
                return str(answer["data"])
    except Exception:
        return None
    return None


def _url_with_hostaddr(url, ip):
    """Append ``hostaddr=<ip>`` to a DB URL's query string.

    libpq connects to the IP but keeps using the hostname for TLS/SNI
    verification, so certificate checks are unaffected.
    """
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}hostaddr={ip}"


def connect_with_retry(url, label, attempts=4, backoff_seconds=2.0):
    """Create a SQLAlchemy engine and verify connectivity with retries.

    Local resolvers intermittently fail DNS for cloud DB hosts (observed:
    'getaddrinfo failed' succeeding seconds later). This retries transient
    connection errors with linear backoff, and on DNS-specific failures
    resolves the host via DNS-over-HTTPS and retries with libpq's
    ``hostaddr`` (hostname still used for TLS verification). Fail-closed
    semantics are kept: after ``attempts`` exhausted, the script exits
    non-zero and NOTHING is written. The URL and credentials are never
    printed.
    """
    import time

    from sqlalchemy import create_engine, text

    if not url:
        raise SystemExit(f"FAIL-CLOSED: {label} URL is missing; refusing to guess.")

    def _build(u):
        return create_engine(norm_sqlalchemy_url(u, label), pool_pre_ping=True, future=True)

    eng = _build(url)
    last_exc = None
    hostaddr_tried = False
    for attempt in range(1, attempts + 1):
        try:
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            return eng
        except Exception as exc:  # noqa: BLE001 - re-raised fail-closed below
            last_exc = exc
            msg = str(exc)
            transient = "getaddrinfo" in msg or "Name or service not known" in msg or "Temporary failure in name resolution" in msg
            if transient and not hostaddr_tried:
                hostaddr_tried = True
                from urllib.parse import urlsplit

                host = urlsplit(url).hostname
                ip = _doh_resolve_a(host) if host else None
                if ip:
                    print(
                        f"{label}: local DNS failed for the database host; "
                        "resolved via public DNS, retrying with hostaddr "
                        "(hostname kept for TLS verification) ...",
                        flush=True,
                    )
                    eng = _build(_url_with_hostaddr(url, ip))
                    continue
            if attempt < attempts:
                wait = backoff_seconds * attempt
                print(f"{label}: transient connect failure "
                      f"({type(exc).__name__}), retry {attempt}/{attempts - 1} "
                      f"in {wait:.0f}s ...", flush=True)
                time.sleep(wait)
    raise SystemExit(
        f"FAIL-CLOSED: cannot connect to {label} after {attempts} attempts: "
        f"{type(last_exc).__name__} (URL redacted: {redact_url(url)})"
    )


