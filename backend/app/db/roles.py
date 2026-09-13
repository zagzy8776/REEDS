"""Database role abstraction (Phase 1).

REEDS runs on three purpose-specific databases:

1. Aiven PostgreSQL — hot/transactional production database (primary),
2. CockroachDB — historical/analytical, and
3. Turso/libSQL — lightweight lease/cache state (no ORM tables).

This module is the single source of truth for:

1. which DATABASE ROLE exists (``aiven``, ``cockroach``, ``turso``),
2. which connection URL each role resolves to (with the legacy
   ``DATABASE_URL`` remaining the Aiven fallback during the transition
   from the old single-database deployment), and
3. which TABLE each role owns (the table-role registry, derived from the
   Phase 0 repository audit).

Design rules:

* Pure and env-driven. Resolution reads only ``os.environ`` — never
  ``get_settings()`` — so importing this module does not import pydantic and
  unit tests can monkeypatch the environment freely.
* No engine is created here. ``app.db.session`` consumes ``resolve_role_urls``
  lazily so processes that never touch a role never pay for it.
* AIVEN_DATABASE_URL is the production primary. When it is absent the Aiven
  role falls back to the legacy ``DATABASE_URL`` so existing deployments keep
  working without configuration changes; the fallback is removed in a later
  cutover once the old database is fully retired.
* Turso owns NO application tables. It is lease/cache only (scheduler lease,
  future board projections). Every ORM table is assigned to exactly one of the
  two relational roles and validated against ``Base.metadata`` by tests.
"""

from __future__ import annotations

import os
from typing import Callable, Mapping

# Canonical role names.
AIVEN = "aiven"
COCKROACH = "cockroach"
TURSO = "turso"
ROLES = (AIVEN, COCKROACH, TURSO)

# Env var names per role (names only — values are never logged).
ROLE_ENV_VARS: dict[str, str] = {
    AIVEN: "AIVEN_DATABASE_URL",
    COCKROACH: "COCKROACH_DATABASE_URL",
    TURSO: "TURSO_DATABASE_URL",
}

LEGACY_ENV_VAR = "DATABASE_URL"


def resolve_role_url(role: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Resolve the connection URL for one role from the environment.

    Resolution order:
      1. the role's own variable (AIVEN_DATABASE_URL / COCKROACH_DATABASE_URL /
         TURSO_DATABASE_URL),
      2. for the AIVEN role only: the legacy DATABASE_URL (backward-compatible
         fallback so existing single-database deployments keep working during
         the transition),
      3. otherwise None (the role is not configured).
    """
    if role not in ROLES:
        raise ValueError(f"Unknown database role: {role!r}")
    env = os.environ if environ is None else environ
    url = (env.get(ROLE_ENV_VARS[role]) or "").strip()
    if url:
        return url
    if role == AIVEN:
        url = (env.get(LEGACY_ENV_VAR) or "").strip()
        if url:
            return url
    return None


def resolve_role_urls(environ: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Resolve every role at once: ``{role: url_or_None}``."""
    return {role: resolve_role_url(role, environ) for role in ROLES}


def active_roles(environ: Mapping[str, str] | None = None) -> list[str]:
    """Roles that currently have a URL configured."""
    return [role for role in ROLES if resolve_role_url(role, environ)]


def describe_resolution(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Redacted per-role status for logs/diagnostics. Never includes URL values."""
    env = os.environ if environ is None else environ
    out: dict[str, str] = {}
    for role in ROLES:
        own = bool((env.get(ROLE_ENV_VARS[role]) or "").strip())
        if own:
            out[role] = f"configured via {ROLE_ENV_VARS[role]}"
        elif role == AIVEN and (env.get(LEGACY_ENV_VAR) or "").strip():
            out[role] = f"fallback to {LEGACY_ENV_VAR}"
        else:
            out[role] = "not configured"
    return out


# ---------------------------------------------------------------------------
# Table-role registry (Phase 0 audit).
#
# Every SQLAlchemy-mapped table is owned by exactly one RELATIONAL role.
# Turso deliberately owns no ORM tables: it is lease/cache state only, keyed
# by explicit names in TURSO_KEYS below.
# ---------------------------------------------------------------------------

TABLE_ROLES: dict[str, str] = {
    # --- Aiven: hot transactional / application state (request-time reads) ---
    "fixtures": AIVEN,             # live board + <=730d hot window (training corpus read from here)
    "predictions": AIVEN,
    "odds_snapshots": AIVEN,
    "market_evidence": AIVEN,      # request-time publication gate
    "model_versions": AIVEN,
    "model_artifacts": AIVEN,      # METADATA ONLY — data blob must stay empty (Render OOM history)
    "model_feedback": AIVEN,
    "teams": AIVEN,
    "team_aliases": AIVEN,
    "user_predictions": AIVEN,
    "community_comments": AIVEN,
    "community_reactions": AIVEN,
    "community_plays": AIVEN,
    "win_slips": AIVEN,
    "user_follows": AIVEN,
    "user_subscriptions": AIVEN,
    "push_subscriptions": AIVEN,
    "match_events": AIVEN,
    "match_lineups": AIVEN,
    "insider_signals": AIVEN,
    "rejected_fixtures": AIVEN,
    # --- CockroachDB: historical / analytical ---
    "backtest_runs": COCKROACH,
    "historical_evaluation": COCKROACH,
}

# The 730-day hot window that bounds the Aiven `fixtures` ownership. Older
# completed fixtures belong to CockroachDB once Phase 2 splits the table.
FIXTURES_HOT_WINDOW_DAYS = 730

# Turso holds ONLY lease/cache state — never relational rows. Keys are
# explicit names; nothing from Base.metadata may appear here.
TURSO_KEYS: tuple[str, ...] = (
    "scheduler_lease",
)

RELATIONAL_ROLES = (AIVEN, COCKROACH)


def role_for_table(table: str) -> str | None:
    """Owner role for a table name; None for unknown/Turso-only keys."""
    role = TABLE_ROLES.get(table)
    if role is not None:
        return role
    if table in TURSO_KEYS:
        return TURSO
    return None


def tables_for_role(role: str) -> list[str]:
    """Tables owned by a relational role (sorted, stable for logs/tests)."""
    if role == TURSO:
        return sorted(TURSO_KEYS)
    return sorted(t for t, r in TABLE_ROLES.items() if r == role)


def role_for_tables(tables: list[str]) -> str | None:
    """Single owner role for a set of tables, or None if they span roles."""
    roles = {role_for_table(t) for t in tables}
    roles.discard(None)
    if len(roles) == 1:
        return next(iter(roles))  # type: ignore[return-value]
    return None


# Env var name accessor used by ops tooling/diagnosis (names only).
def env_var_for_role(role: str) -> str:
    if role not in ROLE_ENV_VARS:
        raise ValueError(f"Unknown database role: {role!r}")
    return ROLE_ENV_VARS[role]
