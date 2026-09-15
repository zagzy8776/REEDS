import logging
import threading

import sqlalchemy.exc
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings
from app.db import roles as db_roles


log = logging.getLogger(__name__)

settings = get_settings()


def normalize_database_url(url: str) -> str:
    """Use the psycopg v3 driver for PostgreSQL URLs; the libSQL driver for Turso."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("libsql://") or url.startswith("turso://"):
        return "sqlite+libsql://" + url.split("://", 1)[1]
    return url


def resolve_ipv4_host(url: str) -> str | None:
    """Resolve a PostgreSQL hostname to an IPv4 address when IPv6 is unusable."""
    if not url or url.startswith("sqlite"):
        return None
    try:
        import socket
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        host = parts.hostname
        if not host:
            return None
        rows = socket.getaddrinfo(host, parts.port or 5432, socket.AF_INET, socket.SOCK_STREAM)
        for row in rows:
            sockaddr = row[4] if len(row) > 4 else None
            if sockaddr:
                address = sockaddr[0]
                if address and "." in address:
                    return address
    except Exception:
        return None
    return None


database_url = normalize_database_url(settings.primary_database_url)
if settings.app_env.lower() == "production" and (not database_url or database_url.startswith("sqlite")):
    raise RuntimeError("AIVEN_DATABASE_URL (or DATABASE_URL fallback) must point to PostgreSQL in production")

is_sqlite = database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {
    "connect_timeout": 10,
    "application_name": "loyal-edge-api",
}
if not is_sqlite:
    ipv4 = resolve_ipv4_host(database_url)
    if ipv4:
        connect_args["hostaddr"] = ipv4

engine_kwargs = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "connect_args": connect_args,
}
if not is_sqlite:
    engine_kwargs.update({"pool_size": 5, "max_overflow": 10})

engine = create_engine(database_url, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Role engines (Phase 1).
#
# The module-level ``engine``/``SessionLocal`` above are the PRIMARY engine
# used by the whole application. AIVEN_DATABASE_URL now takes precedence over
# the legacy DATABASE_URL (see config.primary_database_url), so production
# resolves to Aiven when configured while existing deployments that only set
# DATABASE_URL keep working unchanged. The role machinery below adds per-role
# lazy engines resolved from the environment:
#
#   aiven     -> AIVEN_DATABASE_URL, else DATABASE_URL (same as primary)
#   cockroach -> COCKROACH_DATABASE_URL (optional today)
#   turso     -> TURSO_DATABASE_URL (optional today; lease/cache only)
#
# Unconfigured roles resolve to None without raising and without creating
# engines, so deployments that only configure the primary keep working.
# ---------------------------------------------------------------------------

_TURSO_CONNECT_ARGS = {"check_same_thread": False}


def _role_engine_kwargs(role: str, url: str) -> dict:
    """Engine kwargs for one role URL (never logs secret values)."""
    normalized = normalize_database_url(url)
    if normalized.startswith("sqlite"):
        return {
            "pool_pre_ping": True,
            "connect_args": dict(_TURSO_CONNECT_ARGS),
        }
    connect_args = {"connect_timeout": 10, "application_name": f"loyal-edge-{role}"}
    ipv4 = resolve_ipv4_host(normalized)
    if ipv4:
        connect_args["hostaddr"] = ipv4
    kwargs = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "connect_args": connect_args,
    }
    if role == db_roles.COCKROACH:
        # CockroachDB: retry-on-40001 is handled by the driver; keep the pool
        # small — this role serves analytical batch reads, not request paths.
        kwargs.update({"pool_size": 3, "max_overflow": 5})
    else:
        kwargs.update({"pool_size": 5, "max_overflow": 10})
    return kwargs


_role_state: dict[str, object] = {}
_role_state_lock = threading.Lock()


def _role_url(role: str) -> str | None:
    """Env URL for a role, preferring the pydantic setting when it is set."""
    setting = {
        db_roles.AIVEN: settings.aiven_database_url,
        db_roles.COCKROACH: settings.cockroach_database_url,
        db_roles.TURSO: settings.turso_database_url,
    }.get(role, "")
    url = (setting or "").strip()
    if url:
        return url
    return db_roles.resolve_role_url(role)


def get_role_engine(role: str):
    """Lazily create (or return) the engine for one database role.

    Returns None when the role has no URL configured. Thread-safe.
    """
    if role not in db_roles.ROLES:
        raise ValueError(f"Unknown database role: {role!r}")
    with _role_state_lock:
        cached = _role_state.get(f"engine:{role}")
        if cached is not None:
            return cached or None
        url = _role_url(role)
        if not url:
            # Unconfigured: return None WITHOUT negative caching so that a role
            # configured later in the process lifetime (tests, ops tooling) is
            # picked up on the next call.
            return None
        engine_kwargs = _role_engine_kwargs(role, url)
        try:
            role_engine = create_engine(normalize_database_url(url), **engine_kwargs)

            if role == db_roles.COCKROACH:
                role_engine.dialect._get_server_version_info = lambda connection: (15, 0)
        except (ModuleNotFoundError, sqlalchemy.exc.NoSuchModuleError):
            # Missing optional driver/dialect (e.g. libsql for Turso): degrade
            # to None with a clear log instead of breaking the whole process.
            log.warning(
                "Database role %r is configured but its driver is missing; "
                "install it to enable this role",
                role,
            )
            _role_state[f"engine:{role}"] = False
            return None
        _role_state[f"engine:{role}"] = role_engine
        return role_engine


def get_role_sessionmaker(role: str):
    """Sessionmaker bound to a role engine, or None when unconfigured."""
    if role not in db_roles.ROLES:
        raise ValueError(f"Unknown database role: {role!r}")
    key = f"sessionmaker:{role}"
    with _role_state_lock:
        cached = _role_state.get(key)
        if cached is not None:
            return cached or None
    role_engine = get_role_engine(role)
    if role_engine is None:
        return None
    maker = sessionmaker(autocommit=False, autoflush=False, bind=role_engine)
    with _role_state_lock:
        _role_state[key] = maker
    return maker


def get_db_for_role(role: str):
    """FastAPI dependency generator yielding a session for one role (or None)."""
    maker = get_role_sessionmaker(role)
    if maker is None:
        yield None
        return
    db = maker()
    try:
        yield db
    finally:
        db.close()


def session_scope_for_role(role: str):
    """Context manager for a role session (scripts/workers).

    Raises RuntimeError when the role is not configured — batch jobs should
    fail loudly rather than silently run against the wrong database.
    """
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        maker = get_role_sessionmaker(role)
        if maker is None:
            raise RuntimeError(
                f"Database role {role!r} is not configured "
                f"(set {db_roles.env_var_for_role(role)})"
            )
        db = maker()
        try:
            yield db
        finally:
            db.close()

    return _scope()


def reset_role_engines() -> None:
    """Dispose role engines and clear caches (tests / reconfiguration)."""
    with _role_state_lock:
        for key in list(_role_state.keys()):
            value = _role_state.pop(key)
            if isinstance(value, sqlalchemy.Engine):
                try:
                    value.dispose()
                except Exception:
                    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column in existing:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    """Create tables and apply additive schema repairs at startup."""
    # Imported lazily: models.py imports Base from this module, so a module-
    # level import would be circular (registry validation lives in tests).
    from app.db import models  # noqa: F401
    try:
        from app.db.rejected_fixture import RejectedFixture  # noqa: F401
    except Exception:
        RejectedFixture = None  # type: ignore
    Base.metadata.create_all(bind=engine)
    if RejectedFixture is not None:
        try:
            Base.metadata.create_all(bind=engine, tables=[RejectedFixture.__table__])
        except Exception:
            pass
    ensure_schema()


def ensure_schema() -> None:
    inspector = inspect(engine)
    if "predictions" in inspector.get_table_names():
        if engine.dialect.name == "postgresql":
            _add_column_if_missing("predictions", "model_version_id", "INTEGER")
            _add_column_if_missing("predictions", "version", "INTEGER DEFAULT 1 NOT NULL")
            _add_column_if_missing("predictions", "status", "VARCHAR(30) DEFAULT 'active' NOT NULL")
            _add_column_if_missing("predictions", "engine_meta", "JSON")
            _add_column_if_missing("predictions", "published_at", "TIMESTAMP")
            _add_column_if_missing("predictions", "superseded_at", "TIMESTAMP")
        else:
            _add_column_if_missing("predictions", "model_version_id", "INTEGER")
            _add_column_if_missing("predictions", "version", "INTEGER DEFAULT 1")
            _add_column_if_missing("predictions", "status", "VARCHAR(30) DEFAULT 'active'")
            _add_column_if_missing("predictions", "engine_meta", "JSON")
            _add_column_if_missing("predictions", "published_at", "DATETIME")
            _add_column_if_missing("predictions", "superseded_at", "DATETIME")

    if "model_artifacts" in inspector.get_table_names():
        _add_column_if_missing("model_artifacts", "metadata_json", "JSON" if engine.dialect.name == "postgresql" else "JSON")

    from app.db.models import HistoricalEvaluation, MarketEvidence  # noqa: F401
    Base.metadata.create_all(bind=engine, tables=[MarketEvidence.__table__])
    Base.metadata.create_all(bind=engine, tables=[HistoricalEvaluation.__table__])

    for col, ddl in (
        ("historical_settled", "INTEGER DEFAULT 0 NOT NULL"),
        ("historical_wins", "INTEGER DEFAULT 0 NOT NULL"),
        ("historical_losses", "INTEGER DEFAULT 0 NOT NULL"),
        ("historical_accuracy", "DOUBLE PRECISION"),
        ("historical_brier_sum", "DOUBLE PRECISION"),
        ("historical_brier_count", "INTEGER DEFAULT 0 NOT NULL"),
        ("historical_odds_count", "INTEGER DEFAULT 0 NOT NULL"),
        ("historical_roi_units", "DOUBLE PRECISION"),
        ("historical_has_odds", "BOOLEAN DEFAULT FALSE NOT NULL"),
        ("bootstrap_updated_at", "TIMESTAMP"),
    ):
        _add_column_if_missing("market_evidence", col, ddl)


# ── Runtime patches ─────────────────────────────────────────────────────────
# NOTE: apply_runtime_patches() is NOT called at module import time here.
# Doing so created a circular import: session.py -> runtime_patches ->
# predictions -> app.db.models -> app.db.session (Base not yet defined).
# The patches are applied lazily by app.main.startup_runtime_patches() after
# every router and model module has finished importing.

def apply_runtime_patches_late() -> None:
    """Apply production hotfixes (signature + league normalize) after startup.

    Safe to call multiple times; each patch is idempotent.
    """
    try:
        from app.services.runtime_patches import apply_runtime_patches
        apply_runtime_patches()
    except Exception:
        log.exception("failed to apply runtime patches")
