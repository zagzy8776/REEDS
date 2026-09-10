"""Tests for the REEDS product intelligence layer: feedback records,
post-match analysis, performance/calibration analytics, alerts, verdicts,
match intelligence timeline, and followed-history pagination."""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Fixture, ModelFeedback, OddsSnapshot, Prediction, UserFollow


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


def _fixture(db, home="Arsenal", away="Chelsea", goals=(2, 0), match_date=None):
    fx = Fixture(
        sport="soccer",
        league="Premier League",
        season="2025",
        match_date=match_date or date.today() - timedelta(days=2),
        home_team=home,
        away_team=away,
        home_score=goals[0],
        away_score=goals[1],
        home_odds=1.72,
        draw_odds=3.6,
        away_odds=4.8,
        source="test",
        extra={},
    )
    db.add(fx)
    db.flush()
    return fx


def _prediction(db, fx, confidence=64.0, published=True, engine_meta=None):
    p = Prediction(
        fixture_id=fx.id,
        version=1,
        status="active",
        market="1X2",
        pick="Home Win",
        confidence=confidence,
        edge_score=8.0,
        risk_level="Low",
        reasoning="Model read.",
        is_published=published,
        engine_meta=engine_meta or {},
    )
    db.add(p)
    db.flush()
    return p


def _settle(db, prediction, fixture):
    from app.services.prediction_learning import settle_prediction_outcomes
    settle_prediction_outcomes(db, lookback_days=30)
    db.commit()
    db.refresh(prediction)
    return prediction


def test_record_feedback_creates_struct(row_factory_fixture=None):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    fx = _fixture(session)
    p = _prediction(session, fx, confidence=64.0)
    session.commit()

    from app.services.feedback import record_feedback
    from app.services.prediction_learning import prediction_result

    won = prediction_result(p, fx)
    row = record_feedback(session, p, fx, won)
    session.commit()

    assert row is not None
    assert row.actual_result == "won"
    assert row.predicted_probability == pytest.approx(0.64)
    assert row.brier_score == pytest.approx((0.64 - 1.0) ** 2)
    assert row.final_score == "2-0"
    assert row.fixture_id == fx.id
    assert row.error_type == "validated_outcome"
    session.close()
    engine.dispose()


def test_post_match_analysis_structure(db):
    fx = _fixture(db)
    p = _prediction(db, fx, confidence=64.0)
    db.commit()
    from app.services.feedback import record_feedback, post_match_analysis

    record_feedback(db, p, fx, True)
    db.commit()

    analysis = post_match_analysis(db, p.id)
    assert analysis is not None
    assert analysis["section_title"] == "Why REEDS was right"
    assert analysis["result"] == "won"
    assert analysis["expected"]["probability"] == 64.0
    assert analysis["final_score"] == "2-0"
    assert analysis["probability_error"] == pytest.approx(0.36)


def test_post_match_loss_reports_failed_signals(db):
    fx = _fixture(db, goals=(1, 3))
    p = _prediction(db, fx, confidence=79.0, engine_meta={"factors": [{"label": "Home form", "value": "W W W"}]})
    db.commit()
    from app.services.feedback import record_feedback, post_match_analysis

    record_feedback(db, p, fx, False)
    db.commit()
    analysis = post_match_analysis(db, p.id)
    assert analysis["result"] == "lost"
    assert analysis["section_title"] == "Why REEDS missed"
    assert analysis["primary_error"] == "high_confidence_loss"
    assert any("Home form" in s for s in (analysis["failed_signals"] or []))


def test_aggregate_feedback_flags_only_with_sample(db):
    from app.services.feedback import aggregate_feedback, record_feedback

    empty = aggregate_feedback(db)
    assert empty["total_feedback_records"] == 0

    for i in range(3):
        fx = _fixture(db, home=f"Team{i}", goals=(0, 2), match_date=date.today() - timedelta(days=1 + i))
        p = _prediction(db, fx, confidence=90.0)
        db.commit()
        record_feedback(db, p, fx, False)
    db.commit()
    result = aggregate_feedback(db, min_sample=5)
    assert result["total_feedback_records"] == 3
    # small sample never produces a flagged pattern
    assert result["flagged_patterns"] == []

    for i in range(5):
        fx = _fixture(db, home=f"Lost{i}", goals=(0, 1), match_date=date.today() - timedelta(days=5 + i))
        p = _prediction(db, fx, confidence=92.0)
        db.commit()
        record_feedback(db, p, fx, False)
    db.commit()
    result = aggregate_feedback(db, min_sample=5)
    flagged = [s for s in result["flagged_patterns"] if s["sport"] == "soccer" and s["dimension"] == "90-100"]
    assert flagged, "high-confidence repeated losses should surface as a candidate"


def test_performance_summary_segments(db):
    from app.services.analytics import performance_summary

    summary = performance_summary(db)
    assert summary["label"] == "LIVE RECORD"
    assert summary["segments"]["all_time"]["predictions"] == 0

    fx = _fixture(db)
    p = _prediction(db, fx, confidence=64.0)
    db.commit()
    from app.services.prediction_learning import settle_prediction_outcomes

    settle_prediction_outcomes(db, lookback_days=30)
    db.commit()

    summary = performance_summary(db)
    assert summary["segments"]["all_time"]["predictions"] == 1
    assert summary["segments"]["all_time"]["accuracy"] == 100.0
    assert summary["by_sport"][0]["sport"] == "soccer"
    assert any(b["sample"] == 1 for b in summary["calibration"])


def test_data_status_shape(db):
    from app.services.analytics import data_status

    status = data_status(db)
    assert "as_of" in status
    assert status["fixtures"]["status"] == "unavailable"
    assert status["odds"]["status"] == "unavailable"


def test_recent_alerts_derives_settled_events(db):
    from app.services.alerts import recent_alerts

    assert recent_alerts(db) == []
    fx = _fixture(db)
    _prediction(db, fx, confidence=64.0)
    db.commit()
    from app.services.prediction_learning import settle_prediction_outcomes

    settle_prediction_outcomes(db, lookback_days=30)
    db.commit()
    alerts = recent_alerts(db)
    assert any(a["type"] == "prediction_settled" and a["result"] == "won" for a in alerts)


def test_serialize_prediction_verdict_and_market_metrics(db):
    from app.api.public import serialize_prediction

    fx = _fixture(db)
    p = _prediction(db, fx, confidence=64.0, published=False, engine_meta={
        "publication_quality": {"accepted": False, "reasons": ["insufficient settled sample (0 < 20)"]},
        "probabilities": {"home_win": 0.58, "draw": 0.22, "away_win": 0.20},
    })
    payload = serialize_prediction(p, fx)
    assert payload["verdict"]["key"] == "analyzed_low_evidence"
    assert payload["verdict"]["label"] == "ANALYZED — LOW EVIDENCE"
    # market metrics: home pick at 1.72 -> implied 58.1%, model 58% -> edge ~ -0.1pp
    mm = payload["market_metrics"]
    assert mm["available"] is True
    assert mm["side"] == "home"
    assert mm["odds"] == pytest.approx(1.72)
    assert mm["market_probability"] == pytest.approx(58.1, abs=0.2)
    assert mm["model_probability"] == pytest.approx(58.0)

    p.is_published = True
    db.commit()
    payload = serialize_prediction(p, fx)
    assert payload["verdict"]["key"] == "reeds_value"


def test_prediction_history_paginated(db):
    from app.api.public import prediction_history

    for i in range(5):
        fx = _fixture(db, home=f"Team{i}", goals=(1, 0), match_date=date.today() - timedelta(days=i))
        _prediction(db, fx, confidence=60.0 + i)
    db.commit()

    page1 = prediction_history(days=90, limit=3, paginated=True, page=1, db=db)
    assert page1["total"] == 5
    assert len(page1["items"]) == 3
    assert page1["has_more"] is True
    page2 = prediction_history(days=90, limit=3, paginated=True, page=2, db=db)
    assert len(page2["items"]) == 2
    assert page2["has_more"] is False

    only = prediction_history(days=90, limit=50, result="won", db=db)
    assert all(x["result"] == "won" for x in only)

    search = prediction_history(days=90, limit=50, q="Team3", db=db)
    assert len(search) == 1
    assert "Team3" in search[0]["home_team"]


def test_match_intelligence_revisions_and_timeline(db):
    from app.services.match_intelligence import match_intelligence

    fx = _fixture(db, match_date=date.today())
    v1 = _prediction(db, fx, confidence=70.0)
    v1.status = "superseded"
    v2 = _prediction(db, fx, confidence=74.0)
    db.commit()

    info = match_intelligence(db, fx.id)
    assert info is not None
    assert info["revisions"][0]["latest"] is True
    assert len(info["revisions"][0]["versions"]) == 2
    types = {entry["type"] for entry in info["timeline"]}
    assert "model_analysis" in types
    assert "kickoff" in types


def test_toggle_and_list_follows(db):
    from app.services.follows import list_follows, toggle_follow

    result = toggle_follow(db, "alex", "team", "Arsenal")
    assert result["followed"] is True
    followed = list_follows(db, "alex")
    assert any(f["entity_value"] == "Arsenal" for f in followed)
    again = toggle_follow(db, "alex", "team", "Arsenal")
    assert again["followed"] is False


def test_settlement_writes_feedback_only_once(db):
    fx = _fixture(db)
    p = _prediction(db, fx, confidence=64.0)
    db.commit()
    from app.services.prediction_learning import settle_prediction_outcomes

    settle_prediction_outcomes(db, lookback_days=30)
    db.commit()
    count1 = db.query(ModelFeedback).filter(ModelFeedback.prediction_id == p.id).count()
    settle_prediction_outcomes(db, lookback_days=30)
    db.commit()
    count2 = db.query(ModelFeedback).filter(ModelFeedback.prediction_id == p.id).count()
    assert count1 == 1 and count2 == 1