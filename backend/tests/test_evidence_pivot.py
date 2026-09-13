"""Tests for evidence_pivot — lightweight (sqlite + sqlalchemy only)."""

from datetime import date, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    HistoricalEvaluation,
    MarketEvidence,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _add_evaluation(db, sport, market, outcome, fold_index=0, *, confidence=65.0, has_odds=False):
    eval_row = HistoricalEvaluation(
        fixture_id=1000 + fold_index,
        sport=sport,
        league="Test",
        match_date=date(2024, 1, 1) + timedelta(days=fold_index),
        home_team=f"Home {sport}",
        away_team=f"Away {sport}",
        home_score=2,
        away_score=0,
        market=market,
        pick="Home",
        confidence=confidence,
        outcome=outcome,
        brier_score=0.25 if has_odds else None,
        has_odds=has_odds,
        applied_odds=1.8 if has_odds else None,
        roi_units=0.8 if (has_odds and outcome == "won") else (-1.0 if has_odds else None),
        fold_index=fold_index,
        source="bootstrap",
    )
    db.add(eval_row)
    db.flush()


class TestPivotHistoricalEvidence:
    def test_empty_db_noop(self, db):
        from app.services.evidence_pivot import pivot_historical_evidence
        result = pivot_historical_evidence(db)
        assert result["markets_touched"] == 0

    def test_pivot_populates_historical_columns(self, db):
        from app.services.evidence_pivot import pivot_historical_evidence

        for i in range(30):
            _add_evaluation(db, "soccer", "1X2", "won", fold_index=i)
        for i in range(30, 40):
            _add_evaluation(db, "soccer", "1X2", "lost", fold_index=i)
        db.flush()

        result = pivot_historical_evidence(db)
        assert result["markets_touched"] == 1

        record = db.query(MarketEvidence).filter_by(sport="soccer", market="1X2").first()
        assert record is not None
        assert record.historical_settled == 40
        assert record.historical_wins == 30
        assert record.historical_losses == 10
        assert abs(record.historical_accuracy - 0.75) < 1e-9
        assert record.bootstrap_updated_at is not None

    def test_pivot_idempotent(self, db):
        from app.services.evidence_pivot import pivot_historical_evidence

        for i in range(10):
            _add_evaluation(db, "basketball", "Moneyline", "won", fold_index=i)
        db.flush()

        pivot_historical_evidence(db)
        r1 = db.query(MarketEvidence).filter_by(sport="basketball", market="Moneyline").first()
        assert r1.historical_settled == 10

        for i in range(10, 15):
            _add_evaluation(db, "basketball", "Moneyline", "lost", fold_index=i)
        db.flush()
        pivot_historical_evidence(db)
        r2 = db.query(MarketEvidence).filter_by(sport="basketball", market="Moneyline").first()
        assert r2.historical_settled == 15
        assert r2.historical_wins == 10
        assert r2.historical_losses == 5

    def test_no_odds_remains_zero(self, db):
        from app.services.evidence_pivot import pivot_historical_evidence

        for i in range(5):
            _add_evaluation(db, "soccer", "BTTS", "won", fold_index=i, has_odds=False)
        db.flush()
        pivot_historical_evidence(db)
        record = db.query(MarketEvidence).filter_by(sport="soccer", market="BTTS").first()
        assert record.historical_odds_count == 0
        assert record.historical_has_odds is False
        assert record.historical_roi_units == 0.0
