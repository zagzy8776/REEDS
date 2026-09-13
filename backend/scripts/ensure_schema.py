import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from app.db.session import engine, init_db, repair_runtime_schema


def add_column_if_missing(table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    existing = {col["name"] for col in inspector.get_columns(table)} if table in inspector.get_table_names() else set()
    if column not in existing:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        print({"added_column": f"{table}.{column}"})


def repair_existing_sqlite_schema() -> None:
    """Local safety repair for MVP SQLite databases created before migrations.

    SQLAlchemy create_all creates missing tables but does not alter existing ones.
    This keeps local/dev databases usable after adding ledger columns. Production
    should still use Alembic migrations.
    """

    if engine.dialect.name != "sqlite":
        return
    add_column_if_missing("predictions", "model_version_id", "INTEGER")
    add_column_if_missing("predictions", "version", "INTEGER DEFAULT 1")
    add_column_if_missing("predictions", "status", "VARCHAR(30) DEFAULT 'active'")
    add_column_if_missing("predictions", "published_at", "DATETIME")
    add_column_if_missing("predictions", "superseded_at", "DATETIME")


if __name__ == "__main__":
    init_db()
    repair_runtime_schema()
    repair_existing_sqlite_schema()
    print({"schema": "ensured", "note": "SQLAlchemy create_all completed for configured DATABASE_URL"})