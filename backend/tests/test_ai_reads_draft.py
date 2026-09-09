"""Tests for per-fixture AI Reads: internal (unpublished) reads are returned as
clearly-labelled drafts so a Match Hub / AI Reads click always has something to
show, while published evidence still returns the tracked "ready" reads first.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Fixture, Prediction
from app.services.prediction_learning import prediction_result


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


def _fixture(db, *, days_from_today=5):
    fx = Fixture(
        sport="soccer",
        league="Premier League",
        season="2024/25",
        match_date=date.today() + timedelta(days=days_from_today),
        home_team="Draft Home",
        away_team="Draft Away",
        source="test",
    )
    db.add(fx)
    db.flush()
    return fx


def _prediction(db, fx, *, published=False):
    pr = Prediction(
        fixture_id=fx.id,
        version=1,
        status="active",
        market="1X2",
        pick="Home",
        confidence=66.0,
        edge_score=2.0,
        risk_level="Medium",
        reasoning="test read",
        is_published=published,
    )
    db.add(pr)
    db.flush()
    return pr


def test_draft_reads_returned_when_only_internal_picks_exist(db):
    """Internal reads must surface as labelled drafts, not as an empty board."""
    from app.api.ai_reads import ai_reads

    fx = _fixture(db)
    _prediction(db, fx, published=False)
    result = ai_reads(fx.id, db)
    assert result["status"] == "draft"
    assert len(result["predictions"]) == 1
    assert result["predictions"][0]["is_published"] is False
    assert "Draft analysis" in result["message"]


def test_published_reads_take_precedence_over_drafts(db):
    """Tracked published reads are the primary surface; drafts only fill the void."""
    from app.api.ai_reads import ai_reads

    fx = _fixture(db)
    _prediction(db, fx, published=True)
    _prediction(db, fx, published=False)
    result = ai_reads(fx.id, db)
    assert result["status"] == "ready"
    assert all(p["is_published"] for p in result["predictions"])


def test_no_reads_future_fixture_returns_preparing_not_found(db):
    """A future fixture with zero predictions is 'preparing', never a 404."""
    from app.api.ai_reads import ai_reads

    fx = _fixture(db)
    result = ai_reads(fx.id, db)
    assert result["status"] in {"preparing", "draft", "ready"}


def test_serializer_tags_draft_provenance(db):
    from app.api.public import serialize_prediction

    fx = _fixture(db, days_from_today=-5)
    fx.home_score = 2
    fx.away_score = 1
    pr = _prediction(db, fx, published=False)
    assert prediction_result(pr, fx) is True
    payload = serialize_prediction(pr, fx)
    assert payload["is_published"] is False
    assert payload["result"] == "won"