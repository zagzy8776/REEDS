"""Scratch SQLite databases for Phase 2 migration tests (no network/creds).

Builds a source "Neon" DB plus Aiven/Cockroach destination DBs using the REAL
ORM models so column names, index names, uniqueness and JSON/timestamp types
match production naming. Adds a ``fixtures_archive`` clone table on the
Cockroach side (the cold Fixture partition has no ORM model, so its schema is
a structural clone of ``Fixture.__table__``).

Only used by tests. Never touches real databases.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import MetaData, create_engine, insert, text
from sqlalchemy.schema import Table

# --- imports that register the ORM models on Base.metadata -------------------
from app.db import models  # noqa: E402,F401
from app.db.rejected_fixture import RejectedFixture  # noqa: E402,F401
from app.db.session import Base  # noqa: E402

HOT_WINDOW = 730


def _clone_fixture_archive(metadata: MetaData) -> Table:
    """Clone the ORM Fixture table into a new name; returns the new Table."""
    src = Base.metadata.tables["fixtures"]
    return src.to_metadata(metadata, name="fixtures_archive")


def make_engine(db_path: str):
    # SQLite URLs must use forward slashes even on Windows.
    return create_engine(f"sqlite:///{db_path.replace(chr(92), '/')}", future=True)


def create_aiven_schema(engine) -> None:
    """Create every table owned by the aiven role (plus none from cockroach)."""
    tables = [Base.metadata.tables[name] for name in (
        "fixtures", "teams", "team_aliases", "model_versions",
        "model_artifacts", "predictions", "odds_snapshots", "market_evidence",
        "model_feedback", "match_events", "match_lineups", "insider_signals",
        "user_predictions", "community_comments", "community_reactions",
        "community_plays", "win_slips", "user_follows", "user_subscriptions",
        "push_subscriptions")]
    Base.metadata.create_all(engine, tables=tables)
    RejectedFixture.__table__.create(engine, checkfirst=True)


def create_cockroach_schema(engine) -> None:
    """historical_evaluation + backtest_runs (ORM) + fixtures_archive clone."""
    tables = [Base.metadata.tables["historical_evaluation"],
              Base.metadata.tables["backtest_runs"]]
    Base.metadata.create_all(engine, tables=tables)
    meta = MetaData()
    _clone_fixture_archive(meta).create(engine, checkfirst=True)


def create_source_schema(engine) -> None:
    """The legacy Neon source holds every ORM table + rejected_fixtures."""
    Base.metadata.create_all(engine)
    RejectedFixture.__table__.create(engine, checkfirst=True)


def seed_fixture(engine, fixture_id: int, *, days_from_today: int,
                 sport: str = "soccer", league: str = "E0",
                 home: str = "Home FC", away: str = "Away FC", **extra) -> None:
    """Insert one fixture row with match_date = today +- days_from_today."""
    match_date = date.today() + timedelta(days=days_from_today)
    with engine.begin() as conn:
        conn.execute(
            insert(Base.metadata.tables["fixtures"]).values(
                id=fixture_id, sport=sport, league=league, season="2425",
                match_date=match_date, home_team=home, away_team=away,
                home_score=None, away_score=None, source="test",
                extra=extra or None, created_at=datetime.utcnow(),
            )
        )


def seed_prediction(engine, pred_id: int, fixture_id: int, *,
                    market: str = "1X2", is_published: bool = True) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(Base.metadata.tables["predictions"]).values(
                id=pred_id, fixture_id=fixture_id, model_version_id=None,
                version=1, status="active", market=market, pick="Home FC",
                confidence=66.0, edge_score=0.02, risk_level="Low",
                reasoning="test", is_premium=False, is_published=is_published,
                engine_meta={"probe": [1, 2, 3]}, published_at=None,
                superseded_at=None, created_at=datetime.utcnow(),
            )
        )


def seed_odds(engine, odds_id: int, fixture_id: int, prediction_id=None) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(Base.metadata.tables["odds_snapshots"]).values(
                id=odds_id, fixture_id=fixture_id, prediction_id=prediction_id,
                phase="initial", market="1X2", home_odds=2.0, source="test",
                captured_at=datetime.utcnow(),
            )
        )


def seed_feedback(engine, fb_id: int, prediction_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(Base.metadata.tables["model_feedback"]).values(
                id=fb_id, prediction_id=prediction_id, fixture_id=1,
                sport="soccer", league="E0", market="1X2", pick="Home FC",
                predicted_probability=0.66, actual_result="won",
                probability_error=0.0, brier_score=0.2, feedback_status="pending",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
        )


def seed_hist_eval(engine, he_id: int, fixture_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(Base.metadata.tables["historical_evaluation"]).values(
                id=he_id, fixture_id=fixture_id, sport="soccer", league="E0",
                match_date=(date.today() - timedelta(days=900)),
                home_team="Home FC", away_team="Away FC", market="1X2",
                pick="Home FC", confidence=60.0, edge_score=0.01,
                outcome="won", has_odds=True, inference_mode="walk_forward_fold",
                fold_index=0, job_id="test", source="test",
                created_at=datetime.utcnow(),
            )
        )


def seed_backtest(engine, bt_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(Base.metadata.tables["backtest_runs"]).values(
                id=bt_id, sport="soccer", model_type="test",
                split_strategy="walk_forward", sample_size=100, accuracy=0.6,
                created_at=datetime.utcnow(),
            )
        )


def hot_fixture_ids(engine) -> set[int]:
    cutoff = (date.today() - timedelta(days=HOT_WINDOW)).isoformat()
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text(
            'SELECT "id" FROM "fixtures" WHERE "match_date" >= :cutoff'
        ), {"cutoff": cutoff})}


def cold_fixture_ids(engine) -> set[int]:
    cutoff = (date.today() - timedelta(days=HOT_WINDOW)).isoformat()
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text(
            'SELECT "id" FROM "fixtures" WHERE "match_date" < :cutoff'
        ), {"cutoff": cutoff})}