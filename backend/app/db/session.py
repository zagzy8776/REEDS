from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


settings = get_settings()


def normalize_database_url(url: str) -> str:
    """Use psycopg v3 for Neon/PostgreSQL URLs.

    Neon commonly provides URLs beginning with `postgresql://`. SQLAlchemy maps that
    default form to psycopg2, but this project installs `psycopg[binary]` instead.
    Converting the scheme prevents Render startup crashes from missing psycopg2.
    """

    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


database_url = normalize_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


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
    """Patch known additive schema drift at startup.

    Render deployments may point at an existing database that was created before the
    prediction-ledger/community tables were added. ``create_all`` creates missing
    tables but intentionally does not alter existing tables, which can make public
    endpoints crash with 500s when they query newer columns. These additive repairs
    are safe and idempotent for both SQLite and PostgreSQL; full migrations are
    still the preferred long-term workflow.
    """

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
