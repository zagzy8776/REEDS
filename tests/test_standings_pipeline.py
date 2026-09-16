from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Standing, TeamPerformance, DataSourceProvenance, Base
from app.scraper.providers.base import StandingsRow
from app.services.standings_service import StandingsIngestor
from tests.providers.mock_provider import MockStandingsProvider


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

EPL_ROUND_3 = [
    {"team": "Arsenal", "position": 1, "points": 9, "games_played": 3,
     "wins": 3, "draws": 0, "losses": 0, "goals_for": 8, "goals_against": 1,
     "goal_difference": 7, "recent_form": "WWW"},
    {"team": "Chelsea", "position": 3, "points": 4, "games_played": 3,
     "wins": 1, "draws": 1, "losses": 1, "goals_for": 3, "goals_against": 5,
     "goal_difference": -2, "recent_form": "DWL"},
]


class TestStandingsIngestor:
    """Tests for StandingsIngestor: ingestion, idempotency, leakage, conflicts."""

    def test_ingest_standings_writes_rows(self, ingestor, db_session):
        provider = ingestor.providers[0]
        provider.set_standings("2024-08-19", EPL_ROUND_2)

        count = ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")
        assert count == 2

        rows = db_session.execute(
            select(Standing).where(Standing.league == "EPL", Standing.sport == "soccer")
        ).scalars().all()
        assert len(rows) == 2
        assert {r.team for r in rows} == {"Arsenal", "Chelsea"}
        assert all(r.effective_date == date(2024, 8, 19) for r in rows)

    def test_ingest_standings_idempotent(self, ingestor, db_session):
        provider = ingestor.providers[0]
        provider.set_standings("2024-08-19", [dict(EPL_ROUND_2[0])])

        count1 = ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")
        count2 = ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")

        assert count1 == 1
        assert count2 == 1

        rows = db_session.execute(
            select(Standing).where(
                Standing.league == "EPL",
                Standing.team == "Arsenal",
                Standing.effective_date == date(2024, 8, 19),
            )
        ).scalars().all()
        assert len(rows) == 1

    def test_standings_table_idempotent_reingest_no_duplicates(self, ingestor, db_session):
        """Re-ingesting the same league/season/date must not duplicate rows on the
        ``standings`` table. ``uq_standing`` has no provider column (last writer
        wins), so a second ingest upserts the same keyed rows instead of inserting.
        """
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

    def test_effective_date_strictly_before_fixture(self, ingestor, db_session):
        provider = ingestor.providers[0]
        provider.set_standings("2024-08-19", [dict(EPL_ROUND_2[0])])

        ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")

        rows = db_session.execute(
            select(Standing).where(Standing.team == "Arsenal")
        ).scalars().all()
        assert all(r.effective_date < date(2024, 8, 20) for r in rows)

    def test_get_team_standing_no_leakage(self, ingestor, db_session):
        provider = ingestor.providers[0]
        provider.set_standings("2024-08-19", [dict(EPL_ROUND_2[0])])

        ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")

        st_before = ingestor.get_team_standing("soccer", "EPL", "Arsenal", "2024-08-20")
        assert st_before is not None
        assert st_before.position == 1

        st_after = ingestor.get_team_standing("soccer", "EPL", "Arsenal", "2024-08-19")
        assert st_after is None

    def test_multiple_snapshots_keeps_latest_before_fixture(self, ingestor, db_session):
        provider = ingestor.providers[0]
        provider.set_standings("2024-08-19", [dict(EPL_ROUND_2[0])])

        ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")

        provider.set_standings("2024-08-21", [dict(EPL_ROUND_3[0])])
        ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-21")

        st = ingestor.get_team_standing("soccer", "EPL", "Arsenal", "2024-08-22")
        assert st is not None
        assert st.points == 9
        assert st.effective_date == date(2024, 8, 21)

    def test_conflict_detection_records_provenance(self, ingestor, db_session):
        rows = [
            StandingsRow(
                provider="provider_a", sport="soccer", league="EPL", season="2024",
                team="Arsenal", team_source_id=None, position=1, points=6, games_played=2,
            ),
            StandingsRow(
                provider="provider_b", sport="soccer", league="EPL", season="2024",
                team="Arsenal", team_source_id=None, position=3, points=4, games_played=2,
            ),
        ]

        ingestor._detect_and_record_conflicts(rows, "EPL", "2024", date(2024, 8, 19), "test-ingest")
        db_session.commit()

        provs = db_session.execute(
            select(DataSourceProvenance).where(
                DataSourceProvenance.entity_key == "EPL|2024|Arsenal"
            )
        ).scalars().all()
        assert len(provs) == 2
        assert all(p.status == "conflict" for p in provs)

    def test_single_provider_records_consistent(self, ingestor, db_session):
        rows = [
            StandingsRow(
                provider="provider_a", sport="soccer", league="EPL", season="2024",
                team="Arsenal", team_source_id=None, position=1, points=6, games_played=2,
            ),
        ]

        ingestor._detect_and_record_conflicts(rows, "EPL", "2024", date(2024, 8, 19), "test-ingest")
        db_session.commit()

        prov = db_session.execute(
            select(DataSourceProvenance).where(
                DataSourceProvenance.entity_key == "EPL|2024|Arsenal"
            )
        ).scalars().first()
        assert prov.status == "consistent"
        assert prov.confidence_score == 1.0

    def test_team_performance_computed(self, ingestor, db_session):
        provider = ingestor.providers[0]
        provider.set_standings("2024-08-19", [dict(EPL_ROUND_2[0])])

        ingestor.ingest_standings("soccer", "EPL", "2024", "2024-08-19")

        perfs = db_session.execute(
            select(TeamPerformance).where(TeamPerformance.team == "Arsenal")
        ).scalars().all()
        assert len(perfs) > 0
        ppg = [p for p in perfs if p.metric_name == "points_per_game"]
        assert len(ppg) == 1
        assert ppg[0].metric_value == 3.0
