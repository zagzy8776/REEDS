"""Idempotency test for standings re-ingestion on the ``standings`` table.

Asserts that re-ingesting the same league/season/date does not duplicate rows:
``uq_standing`` has no ``provider`` column (last writer wins), so the second
ingest upserts rather than inserts. Self-contained fixture (mirrors
``tests/test_standings_pipeline.py``).

Run:  python -m pytest tests/test_standings_idempotency_extras.py -q
"""
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Standing
from tests.providers.mock_provider import MockStandingsProvider
from app.services.standings_service import StandingsIngestor


SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture
def db_session():
    engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture
def ingestor(db_session):
    return StandingsIngestor(db_session, providers=[MockStandingsProvider()])


EPL_ROUND_2 = [
    {"team": "Arsenal", "position": 1, "points": 6, "games_played": 2,
     "wins": 2, "draws": 0, "losses": 0, "goals_for": 5, "goals_against": 1,
     "goal_difference": 4, "recent_form": "WW"},
    {"team": "Chelsea", "position": 2, "points": 4, "games_played": 2,
     "wins": 1, "draws": 1, "losses": 0, "goals_for": 3, "goals_against": 2,
     "goal_difference": 1, "recent_form": "DW"},
]


def test_standings_table_idempotent_reingest_no_duplicates(ingestor, db_session):
    """Re-ingest same league/season/date -> no duplicate rows on ``standings``."""
    provider = ingestor.providers[0]
    provider.set_standings("2024-08-19", EPL_ROUND_2)

    ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")
    ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")

    rows = db_session.execute(
        select(Standing).where(
            Standing.league == "EPL",
            Standing.sport == "soccer",
            Standing.effective_date == date(2024, 8, 19),
            Standing.standing_type == "total",
        )
    ).scalars().all()
    assert len(rows) == 2
    assert {r.team for r in rows} == {"Arsenal", "Chelsea"}
    assert all(r.standing_type == "total" for r in rows)


def test_standings_table_reingest_same_row_count_preserved(ingestor, db_session):
    """A third ingest still leaves exactly one row per team (no UniqueViolation)."""
    provider = ingestor.providers[0]
    provider.set_standings("2024-08-19", EPL_ROUND_2)

    ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")
    ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")
    ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")

    count = db_session.execute(
        select(Standing).where(
            Standing.league == "EPL",
            Standing.sport == "soccer",
            Standing.effective_date == date(2024, 8, 19),
        )
    ).scalars().all()
    assert len(count) == 2
