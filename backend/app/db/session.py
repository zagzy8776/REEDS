from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


settings = get_settings()


def normalize_database_url(url: str) -> str:
    """Use psycopg v3 for Neon/PostgreSQL URLs."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
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


database_url = normalize_database_url(settings.database_url)
if settings.app_env.lower() == "production" and (not database_url or database_url.startswith("sqlite")):
    raise RuntimeError("DATABASE_URL must point to PostgreSQL in production")

is_sqlite = database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {
    "connect_timeout": 10,
    "application_name": "loyal-edge-api",
}
if not is_sqlite:
    # Render's network currently cannot route to the IPv6 address returned by
    # some Neon endpoints. libpq/psycopg supports hostaddr separately from host:
    # keep the hostname for TLS identity while forcing the TCP socket to IPv4.
    ipv4 = resolve_ipv4_host(database_url)
    if ipv4:
        connect_args["hostaddr"] = ipv4

engine_kwargs = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "connect_args": connect_args,
}
if not is_sqlite:
    # Keep the free Render instance conservative: fewer idle DB connections
    # means less memory and less chance of exhausting the external DB pool.
    engine_kwargs.update({"pool_size": 2, "max_overflow": 1, "pool_timeout": 10})

engine = create_engine(database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.db import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    repair_runtime_schema()


def _column_exists(table: str, column: str) -> bool:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    if _column_exists(table, column):
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def repair_runtime_schema() -> None:
    """Patch known additive schema drift at startup."""
    inspector = inspect(engine)
    if "predictions" not in inspector.get_table_names():
        return

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

    # MarketEvidence is created by Base.metadata.create_all; this is a safety
    # net for deployments that skip the alembic migration.
    from app.db.models import MarketEvidence  # noqa: F401
    Base.metadata.create_all(bind=engine, tables=[MarketEvidence.__table__])
