"""Tests for the market-level publication gate.

Covers Phase 5/6/7 behaviour: markets are gated on settled empirical evidence,
not generic confidence; analytical (Poisson/heuristic) markets need a larger
sample; recent poor performance and loss streaks suppress a market; superseded
prediction versions are never double-counted; missing evidence blocks by
default.
"""

from datetime import date, timedelta
from itertools import count

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    Fixture,
    MarketEvidence,
    Prediction,
)

_SEQ = count()


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


def _add_settled(db, sport, market, pick, home, away, *, days_ago=5, version=1):
    seq = next(_SEQ)
    fx = Fixture(
        sport=sport,
        league="Test League",
        season="2025",
        match_date=date.today() - timedelta(days=days_ago),
        home_team=f"Home {sport} {market} {seq} {home}{away}",
        away_team=f"Away {sport} {market} {seq}",
        home_score=home,
        away_score=away,
        source="test",
    )
    db.add(fx)
    db.flush()
    pr = Prediction(
        fixture_id=fx.id,
        version=version,
        status="active",
        market=market,
        pick=pick,
        confidence=60.0,
        edge_score=1.0,
        risk_level="Medium",
        reasoning="test",
        is_published=True,
    )
    db.add(pr)
    db.flush()
    return fx, pr


# ---------------------------------------------------------------------------
# Settlement resolution (learning loop correctness)
# ---------------------------------------------------------------------------


def test_over_1_5_finishing_1_0_is_a_loss(db):
    from app.services.prediction_learning import prediction_result

    fx, pr = _add_settled(db, "soccer", "Over/Under 1.5", "Over 1.5", 1, 0)
    assert prediction_result(pr, fx) is False


def test_goalless_draw_loses_home_and_over_picks(db):
    from app.services.prediction_learning import prediction_result

    fx, home_pick = _add_settled(db, "soccer", "1X2", "Home", 0, 0)
    assert prediction_result(home_pick, fx) is False
    fx2, over_pick = _add_settled(db, "soccer", "Over/Under 2.5", "Over 2.5", 0, 0)
    assert prediction_result(over_pick, fx2) is False


def test_basic_settlement_cases(db):
    from app.services.prediction_learning import prediction_result

    fx, pr = _add_settled(db, "soccer", "1X2", "Home", 2, 0)
    assert prediction_result(pr, fx) is True
    fx2, draw = _add_settled(db, "soccer", "1X2", "Draw", 1, 1)
    assert prediction_result(draw, fx2) is True
    fx3, btts = _add_settled(db, "soccer", "BTTS", "Yes", 1, 1)
    assert prediction_result(btts, fx3) is True
    fx4, under = _add_settled(db, "soccer", "Over/Under 1.5", "Under 1.5", 1, 0)
    assert prediction_result(under, fx4) is True


def test_market_is_model_trained_classification():
    from app.services.market_gate import market_is_model_trained

    assert market_is_model_trained("soccer", "1X2") is True
    assert market_is_model_trained("basketball", "Moneyline") is True
    # Analytical / derived markets are NOT model-trained.
    assert market_is_model_trained("soccer", "Double Chance") is False
    assert market_is_model_trained("soccer", "Over/Under 2.5") is False


# ---------------------------------------------------------------------------
# Evidence computation + publication gating
# ---------------------------------------------------------------------------


def _fill_winning_market(db, sport, market, pick, n, *, days_ago=5):
    for _ in range(n):
        _add_settled(db, sport, market, pick, 3, 0, days_ago=days_ago)


def test_model_trained_market_with_full_sample_not_blocked(db):
    from app.services.market_gate import MIN_PUBLIC_SAMPLE, compute_market_evidence

    _fill_winning_market(db, "soccer", "1X2", "Home", MIN_PUBLIC_SAMPLE)
    db.flush()
    result = compute_market_evidence(db)
    record = result["soccer|1X2"]
    assert record["settled"] == MIN_PUBLIC_SAMPLE
    assert record["wins"] == MIN_PUBLIC_SAMPLE
    assert record["accuracy"] == 1.0
    assert record["publication_blocked"] is False
    assert record["is_model_trained"] is True


def test_analytical_market_blocked_without_larger_sample(db):
    """Double Chance is derived analytically and faces a 2x evidence bar."""
    from app.services.market_gate import MIN_PUBLIC_SAMPLE, compute_market_evidence

    _fill_winning_market(db, "soccer", "Double Chance", "Home or Draw", MIN_PUBLIC_SAMPLE)
    db.flush()
    result = compute_market_evidence(db)
    record = result["soccer|Double Chance"]
    assert record["is_model_trained"] is False
    assert record["publication_blocked"] is True
    assert any("larger settled sample" in r for r in record["block_reasons"])


def test_recent_poor_performance_blocks_even_with_good_overall(db):
    """40 older wins + 8 recent losses: overall fine, recent accuracy 0%."""
    from app.services.market_gate import MIN_RECENT_SAMPLE, compute_market_evidence

    _fill_winning_market(db, "soccer", "Moneyline", "Home", 40, days_ago=60)
    # Recent Away picks on 3-0 fixtures all lose -> recent accuracy 0%.
    _fill_winning_market(db, "soccer", "Moneyline", "Away", MIN_RECENT_SAMPLE, days_ago=5)
    db.flush()
    result = compute_market_evidence(db)
    record = result["soccer|Moneyline"]
    assert record["settled"] == 48
    assert record["recent_settled"] == MIN_RECENT_SAMPLE
    assert record["recent_accuracy"] == 0.0
    assert record["publication_blocked"] is True
    assert any("recent accuracy" in r for r in record["block_reasons"])


def test_superseded_version_counted_exactly_once(db):
    from app.services.market_gate import MIN_PUBLIC_SAMPLE, compute_market_evidence

    # One fixture carries two versions of the same market prediction.
    fx, v1 = _add_settled(db, "soccer", "1X2", "Home", 2, 0, version=1)
    v2 = Prediction(
        fixture_id=fx.id,
        version=2,
        status="active",
        market="1X2",
        pick="Home",
        confidence=65.0,
        edge_score=1.0,
        risk_level="Medium",
        reasoning="updated",
        is_published=True,
    )
    db.add(v2)
    for _ in range(MIN_PUBLIC_SAMPLE - 1):
        _add_settled(db, "soccer", "1X2", "Home", 2, 0)
    db.flush()
    result = compute_market_evidence(db)
    record = result["soccer|1X2"]
    assert record["settled"] == MIN_PUBLIC_SAMPLE  # not MIN_PUBLIC_SAMPLE + 1
    assert record["wins"] == MIN_PUBLIC_SAMPLE


def test_persisted_evidence_backs_publication_policy(db):
    from app.services.market_gate import (
        MIN_PUBLIC_SAMPLE,
        compute_market_evidence,
        market_publication_policy,
    )

    # No evidence yet -> conservative block.
    allowed, reasons = market_publication_policy(db, "soccer", "1X2")
    assert allowed is False
    assert reasons

    _fill_winning_market(db, "soccer", "1X2", "Home", MIN_PUBLIC_SAMPLE)
    db.flush()
    compute_market_evidence(db)
    allowed, reasons = market_publication_policy(db, "soccer", "1X2")
    assert allowed is True
    assert reasons == []

    # Analytical market with the same evidence stays blocked.
    allowed, reasons = market_publication_policy(db, "soccer", "Double Chance")
    assert allowed is False


def test_blocked_evidence_row_is_respected(db):
    db.add(
        MarketEvidence(
            sport="soccer",
            market="Correct Score",
            settled=100,
            wins=30,
            losses=70,
            accuracy=0.30,
            publication_blocked=True,
            block_reasons=["empirical accuracy 30.0% below 45%"],
            is_model_trained=False,
        )
    )
    db.flush()
    from app.services.market_gate import market_publication_policy

    allowed, reasons = market_publication_policy(db, "soccer", "Correct Score")
    assert allowed is False
    assert "empirical accuracy 30.0% below 45%" in reasons


def test_evidence_summary_is_public_and_non_secret(db):
    from app.services.market_gate import (
        MIN_PUBLIC_SAMPLE,
        compute_market_evidence,
        market_evidence_summary,
    )

    _fill_winning_market(db, "soccer", "1X2", "Home", MIN_PUBLIC_SAMPLE)
    db.flush()
    compute_market_evidence(db)
    summary = market_evidence_summary(db)
    assert summary["markets"]
    entry = next(m for m in summary["markets"] if m["sport"] == "soccer" and m["market"] == "1X2")
    assert entry["publication_blocked"] is False
    assert "note" in summary

