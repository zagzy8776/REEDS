"""Tests for the Prediction Autopsy engine: error classification, qualitative
signal attribution, persistence on feedback rows, conditional pattern
aggregation, and the three-level prediction explainer."""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Fixture, ModelFeedback, Prediction


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


def _fixture(db, goals=(0, 2), league="Premier League", odds=None, match_date=None):
    odds = odds or (1.72, 3.6, 4.8)
    fx = Fixture(
        sport="soccer",
        league=league,
        season="2025",
        match_date=match_date or (date.today() - timedelta(days=2)),
        home_team="Arsenal",
        away_team="Chelsea",
        home_score=goals[0],
        away_score=goals[1],
        home_odds=odds[0],
        draw_odds=odds[1],
        away_odds=odds[2],
        source="test",
        extra={},
    )
    db.add(fx)
    db.flush()
    return fx


def _prediction(db, fx, confidence=70.0, edge=9.0, factors=None, probabilities=None, quality=None, published=True):
    p = Prediction(
        fixture_id=fx.id,
        version=1,
        status="active",
        market="1X2",
        pick="Home Win",
        confidence=confidence,
        edge_score=edge,
        risk_level="Low",
        reasoning="Model read.",
        is_published=published,
        engine_meta={
            "factors": factors or [],
            "probabilities": probabilities or {"home_win": confidence / 100.0, "draw": 0.22, "away_win": 0.20},
            "publication_quality": quality,
        },
    )
    db.add(p)
    db.flush()
    return p


def test_classify_error_overconfidence_and_defense(db):
    from app.services.autopsy import classify_error
    fx = _fixture(db)
    p = _prediction(db, fx, confidence=80.0, factors=[{"label": "Opponent defense", "value": 78}])
    errors = classify_error(p, fx, won=False, factors=[{"label": "Opponent defense", "value": 78}], market_supported=True, disabled_reason=None)
    types = {e["type"] for e in errors}
    assert "probability_overconfidence" in types
    assert "defensive_signal_failure" in types
    assert "market_signal_failure" in types
    assert "high_edge_loss" in types


def test_classify_error_market_contradiction_no_odds(db):
    from app.services.autopsy import classify_error
    fx = _fixture(db, odds=(5.0, 4.2, 1.65))
    p = _prediction(db, fx, confidence=62.0, edge=2.0)
    errors = classify_error(p, fx, won=False, factors=[], market_supported=False, disabled_reason=None)
    assert any(e["type"] == "market_contradiction" for e in errors)


def test_classify_error_underconfidence_on_win(db):
    from app.services.autopsy import classify_error
    fx = _fixture(db, goals=(2, 1))
    p = _prediction(db, fx, confidence=50.0, edge=1.0)
    errors = classify_error(p, fx, won=True, factors=[], market_supported=None, disabled_reason=None)
    assert any(e["type"] == "probability_underconfidence" for e in errors)


def test_classify_error_variance_default(db):
    from app.services.autopsy import classify_error
    fx = _fixture(db)
    p = _prediction(db, fx, confidence=60.0, edge=4.0)
    errors = classify_error(p, fx, won=False, factors=[], market_supported=None, disabled_reason=None)
    assert any(e["type"] == "outcome_variance" for e in errors)


def test_defense_strong_flag_threshold(db):
    from app.services.autopsy import defense_strong_flag
    assert defense_strong_flag([{"label": "Opponent defensive rating", "value": 78}]) is True
    assert defense_strong_flag([{"label": "Opponent defensive rating", "value": 55}]) is False
    assert defense_strong_flag([{"label": "Home form", "value": 9}]) is False
    assert defense_strong_flag([{"label": "Opponent defense", "value": "elite"}]) is True
    assert defense_strong_flag([], disabled_reason="insufficient settled sample") is True


def test_build_autopsy_structure(db):
    from app.services.autopsy import build_autopsy
    fx = _fixture(db)
    p = _prediction(db, fx, confidence=75.0, factors=[{"label": "Home xG output", "value": 2.1}, {"label": "Opponent defense", "value": 74}], quality={"accepted": False, "reasons": ["no settled sample"]}, published=False)
    autopsy = build_autopsy(db, p, fx, won=False, disabled_reason="no settled sample")
    assert autopsy["result"] == "lost"
    assert autopsy["defense_strong"] is True
    assert autopsy["error_classifications"][0]["type"] == "low_evidence_loss"
    assert {c["type"] for c in autopsy["error_classifications"]} >= {"probability_overconfidence", "defensive_signal_failure"}
    assert autopsy["signal_attribution"]["contradicted"], "cited factors must be attributed on a loss"
    assert "limitation" in autopsy


def test_record_feedback_persists_autopsy(db):
    from app.services.feedback import record_feedback
    from app.services.prediction_learning import prediction_result
    fx = _fixture(db)
    p = _prediction(db, fx, confidence=80.0, factors=[{"label": "Opponent defense", "value": 76}])
    db.commit()
    won = prediction_result(p, fx)
    row = record_feedback(db, p, fx, won)
    db.commit()
    assert row.error_classifications, "autopsy classifications persisted"
    assert any(c["type"] == "defensive_signal_failure" for c in row.error_classifications)
    assert row.defense_strong is True
    assert row.signal_attribution.get("contradicted")


def test_post_match_surfaces_autopsy(db):
    from app.services.feedback import post_match_analysis, record_feedback
    from app.services.prediction_learning import prediction_result
    fx = _fixture(db)
    p = _prediction(db, fx, confidence=78.0, factors=[{"label": "Home form", "value": "W W W"}])
    db.commit()
    record_feedback(db, p, fx, prediction_result(p, fx))
    db.commit()
    analysis = post_match_analysis(db, p.id)
    assert "error_classifications" in analysis
    assert "signal_attribution" in analysis
    assert "defense_strong" in analysis


def test_aggregate_patterns_respects_min_sample(db):
    from app.services.autopsy import aggregate_patterns
    assert aggregate_patterns(db, min_sample=5)["pattern_candidates"] == []
    for i in range(10):
        fx = _fixture(db, league="Ligue 1", goals=(0, 1), match_date=date.today() - timedelta(days=i))
        _prediction(db, fx, confidence=95.0, factors=[{"label": "Opponent defense", "value": 85}])
        db.commit()
    from app.services.prediction_learning import settle_prediction_outcomes
    settle_prediction_outcomes(db, lookback_days=60)
    db.commit()
    found = aggregate_patterns(db, min_sample=12, gap_threshold=10.0)
    assert found["pattern_candidates"] == [], "below min_sample must not fire"

    for i in range(4):
        fx = _fixture(db, league="Ligue 1", goals=(0, 1), match_date=date.today() - timedelta(days=12 + i))
        _prediction(db, fx, confidence=95.0, factors=[{"label": "Opponent defense", "value": 85}])
        db.commit()
    settle_prediction_outcomes(db, lookback_days=60)
    db.commit()
    found = aggregate_patterns(db, min_sample=12, gap_threshold=10.0)
    candidates = found["pattern_candidates"]
    assert candidates, "a consistent overconfidence cluster should fire at min_sample"
    defense = [c for c in candidates if c["dimension"] == "defense_context" and c["key"][-1] == "defense"]
    assert defense, "defensive-environment conditional bucket should appear"
    d = defense[0]
    assert d["sample"] >= 14
    assert d["gap_pp"] > 10.0
    assert d["suggested_adjustment_pp"] < 0
    assert d["automatic"] is False and d["validation_required"] is True
    assert "promotion_rule" in d


def test_aggregate_patterns_suggestion_never_auto_applied(db):
    from app.services.autopsy import aggregate_patterns
    result = aggregate_patterns(db, min_sample=1, gap_threshold=0.0)
    assert all(c["automatic"] is False for c in result["pattern_candidates"])
    assert all(c["validation_required"] is True for c in result["pattern_candidates"])


def test_explain_three_levels(db):
    from app.api.public import serialize_prediction
    fx = _fixture(db, match_date=date.today())
    p = _prediction(db, fx, confidence=74.0, edge=9.0, factors=[{"label": "Home form", "value": "W W W"}, {"label": "xG output", "value": 2.1}])
    payload = serialize_prediction(p, fx)
    explain = payload["explain"]
    assert "REEDS leans Home Win" in explain["level_1"]["summary"]
    assert any("Home form" in line for line in explain["level_2"]["evidence_lines"])
    assert explain["level_3"]["model_probability"] == 74.0
    assert explain["level_3"]["edge_score"] == 9.0
    assert explain["level_3"]["confidence_band"] == "70-79"
    assert explain["level_3"]["features_used_count"] == 2