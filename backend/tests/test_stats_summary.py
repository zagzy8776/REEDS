"""Tests for stats summary and fixture status contract."""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Fixture
from app.services.prediction_learning import build_learning_context


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


def test_learning_context_empty(db):
    context = build_learning_context(db)
    assert "settled_predictions" in context
    assert "recent_wins" in context
    assert "recent_losses" in context
    assert "recent_accuracy" in context
    assert "confidence_buckets" in context
    assert context["settled_predictions"] == 0
    assert context["recent_wins"] == 0
    assert context["recent_losses"] == 0
    assert context["recent_accuracy"] is None
    assert context["confidence_buckets"] == {}


def test_fixture_serialize_has_required_fields():
    from app.api.fixtures import _serialize_fixture

    fx = type("Fixture", (), {
        "id": 1,
        "sport": "soccer",
        "league": "Test",
        "season": "2025",
        "match_date": date(2026, 9, 10),
        "home_team": "A",
        "away_team": "B",
        "home_score": None,
        "away_score": None,
        "home_odds": 2.0,
        "draw_odds": 3.0,
        "away_odds": 2.5,
        "source": "test",
        "extra": {},
    })()
    payload = _serialize_fixture(fx, include_extra=False)
    assert "has_odds" in payload
    assert payload["has_odds"] is True
    assert payload["sport"] == "soccer"
    assert payload["result_label"] == "upcoming"
