"""Market evidence tracking for REEDS.

Market evidence remains useful for transparency, calibration, ROI/CLV and
performance reporting. It is intentionally NOT a prerequisite for generating
or showing a model prediction. Bookmaker odds are optional enrichment.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Fixture, MarketEvidence, OddsSnapshot, Prediction

log = logging.getLogger(__name__)

MIN_PUBLIC_SAMPLE = 20
MIN_RECENT_SAMPLE = 8
MIN_EMPIRICAL_ACCURACY = 0.45
MIN_RECENT_ACCURACY = 0.40
MAX_LOSS_STREAK = 5
RECENT_WINDOW_DAYS = 30

MODEL_TRAINED_MARKETS = {
    "soccer": {"1X2", "Moneyline"},
    "basketball": {"Moneyline"},
    "american_football": {"Moneyline"},
    "tennis": {"Moneyline"},
    "hockey": {"Moneyline"},
    "cricket": {"Moneyline"},
    "rugby": {"Moneyline"},
    "baseball": {"Moneyline"},
}

ANALYTICAL_MARKETS = {
    "Double Chance", "Over/Under 1.5", "Over/Under 2.5", "Over/Under 3.5",
    "Both Teams to Score", "BTTS", "Goals", "Correct Score", "Spread",
    "Point Spread", "Run Line", "Total Points", "Total Runs", "Total Games",
}


def market_is_model_trained(sport: str, market: str) -> bool:
    return market in MODEL_TRAINED_MARKETS.get(str(sport).strip().lower(), set())


def _outcome(prediction: Prediction, fixture: Fixture) -> bool | None:
    from app.services.prediction_learning import prediction_result
    return prediction_result(prediction, fixture)


def _selected_odds(prediction: Prediction, snapshot: OddsSnapshot | None) -> float | None:
    if snapshot is None:
        return None
    from app.services.market_metrics import selected_decimal_odds
    return selected_decimal_odds(prediction, snapshot)


def _latest_public_rows(db: Session, limit: int = 12000):
    cutoff = date.today() - timedelta(days=max(1, RECENT_WINDOW_DAYS * 3))
    rows = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(
            Prediction.status == "active",
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
            Fixture.match_date >= cutoff,
        )
        .order_by(Prediction.fixture_id.asc(), Prediction.market.asc(), Prediction.version.desc())
        .limit(limit)
        .all()
    )
    latest: dict[tuple[int, str], tuple[Prediction, Fixture]] = {}
    for prediction, fixture in rows:
        latest.setdefault((prediction.fixture_id, prediction.market), (prediction, fixture))
    return list(latest.values())


def _historical_evidence_for(db: Session, sport: str, market: str) -> dict:
    row = db.query(MarketEvidence).filter(MarketEvidence.sport == sport, MarketEvidence.market == market).first()
    if row is None:
        return {"settled": 0, "wins": 0, "losses": 0, "pushes": 0, "brier_sum": 0.0, "brier_count": 0, "odds_count": 0, "roi": 0.0, "has_odds": False}
    return {
        "settled": row.historical_settled or 0,
        "wins": row.historical_wins or 0,
        "losses": row.historical_losses or 0,
        "pushes": 0,
        "brier_sum": row.historical_brier_sum or 0.0,
        "brier_count": row.historical_brier_count or 0,
        "odds_count": row.historical_odds_count or 0,
        "roi": row.historical_roi_units or 0.0,
        "has_odds": bool(row.historical_has_odds),
    }


def _persist_evidence(db: Session, record: dict) -> None:
    try:
        existing = db.query(MarketEvidence).filter_by(sport=record["sport"], market=record["market"]).first()
        if existing is None:
            existing = MarketEvidence(sport=record["sport"], market=record["market"])
            db.add(existing)
        for key in (
            "settled", "wins", "losses", "pushes", "recent_settled", "recent_wins", "recent_losses",
            "accuracy", "recent_accuracy", "brier_score", "expected_value", "roi_units", "last_loss_streak",
            "publication_blocked", "block_reasons", "is_model_trained", "historical_settled",
            "historical_wins", "historical_losses", "historical_accuracy", "historical_brier_sum",
            "historical_brier_count", "historical_odds_count", "historical_roi_units", "historical_has_odds",
        ):
            setattr(existing, key, record.get(key))
        existing.updated_at = func.now()
    except Exception:
        log.exception("Could not persist market evidence for %s|%s", record.get("sport"), record.get("market"))


def compute_market_evidence(db: Session) -> dict[str, dict]:
    """Compute evidence for reporting; never used as a model eligibility gate."""
    rows = _latest_public_rows(db)
    by_segment: dict[tuple[str, str], dict[str, Any]] = {}
    for prediction, fixture in rows:
        outcome = _outcome(prediction, fixture)
        if outcome is None:
            continue
        seg = by_segment.setdefault((fixture.sport, prediction.market), {
            "settled": 0, "wins": 0, "losses": 0, "pushes": 0,
            "recent_settled": 0, "recent_wins": 0, "recent_losses": 0,
            "loss_streak": 0, "max_loss_streak": 0, "brier_sum": 0.0,
            "brier_count": 0, "profit": 0.0, "ev_count": 0,
        })
        seg["settled"] += 1
        if outcome:
            seg["wins"] += 1
            seg["loss_streak"] = 0
        else:
            seg["losses"] += 1
            seg["loss_streak"] += 1
        seg["max_loss_streak"] = max(seg["max_loss_streak"], seg["loss_streak"])
        if fixture.match_date >= date.today() - timedelta(days=RECENT_WINDOW_DAYS):
            seg["recent_settled"] += 1
            seg["recent_wins"] += int(outcome)
            seg["recent_losses"] += int(not outcome)
        snapshot = (
            db.query(OddsSnapshot)
            .filter(OddsSnapshot.prediction_id == prediction.id, OddsSnapshot.phase == "published")
            .order_by(OddsSnapshot.captured_at.desc()).first()
        )
        odds = _selected_odds(prediction, snapshot)
        if odds and odds > 1.0:
            seg["profit"] += (odds - 1.0) if outcome else -1.0
            seg["ev_count"] += 1
        confidence = float(prediction.confidence or 0.0)
        if 0.0 < confidence <= 100.0:
            p = confidence / 100.0
            seg["brier_sum"] += (1.0 - p) ** 2 if outcome else p ** 2
            seg["brier_count"] += 1

    result: dict[str, dict] = {}
    for (sport, market), seg in by_segment.items():
        hist = _historical_evidence_for(db, sport, market)
        settled = seg["settled"]
        wins = seg["wins"]
        total_settled = settled + hist["settled"]
        total_wins = wins + hist["wins"]
        accuracy = wins / settled if settled else None
        recent_accuracy = seg["recent_wins"] / seg["recent_settled"] if seg["recent_settled"] else None
        brier = seg["brier_sum"] / seg["brier_count"] if seg["brier_count"] else None
        roi = seg["profit"] / seg["ev_count"] if seg["ev_count"] else None
        combined_accuracy = total_wins / total_settled if total_settled else accuracy
        reasons: list[str] = []
        if total_settled < MIN_PUBLIC_SAMPLE:
            reasons.append(f"insufficient settled sample ({total_settled} < {MIN_PUBLIC_SAMPLE})")
        if combined_accuracy is not None and combined_accuracy < MIN_EMPIRICAL_ACCURACY:
            reasons.append(f"empirical accuracy {combined_accuracy:.1%} below {MIN_EMPIRICAL_ACCURACY:.0%}")
        if seg["recent_settled"] >= MIN_RECENT_SAMPLE and recent_accuracy is not None and recent_accuracy < MIN_RECENT_ACCURACY:
            reasons.append(f"recent accuracy {recent_accuracy:.1%} below {MIN_RECENT_ACCURACY:.0%}")
        if seg["max_loss_streak"] >= MAX_LOSS_STREAK:
            reasons.append(f"repeated losses (streak of {seg['max_loss_streak']})")
        record = {
            "sport": sport, "market": market, "settled": settled, "wins": wins,
            "losses": seg["losses"], "pushes": seg["pushes"],
            "recent_settled": seg["recent_settled"], "recent_wins": seg["recent_wins"],
            "recent_losses": seg["recent_losses"], "accuracy": accuracy,
            "recent_accuracy": recent_accuracy, "brier_score": brier,
            "expected_value": roi, "roi_units": round(seg["profit"], 4),
            "last_loss_streak": seg["loss_streak"],
            "publication_blocked": False,
            "block_reasons": reasons,
            "is_model_trained": market_is_model_trained(sport, market),
            "historical_settled": hist["settled"], "historical_wins": hist["wins"],
            "historical_losses": hist["losses"], "historical_accuracy": hist["wins"] / hist["settled"] if hist["settled"] else None,
            "historical_brier_sum": round(hist["brier_sum"], 4), "historical_brier_count": hist["brier_count"],
            "historical_odds_count": hist["odds_count"], "historical_roi_units": round(hist["roi"], 4),
            "historical_has_odds": hist["has_odds"], "total_settled": total_settled,
            "combined_accuracy": combined_accuracy,
        }
        result[f"{sport}|{market}"] = record
        _persist_evidence(db, record)
    return result


def market_publication_policy(db: Session, sport: str, market: str) -> tuple[bool, list[str]]:
    """Return whether a model market may be shown.

    Market evidence is advisory only. It can explain confidence/track record,
    but it cannot suppress a prediction generated by the model.
    """
    return True, []


def market_evidence_summary(db: Session) -> dict:
    rows = db.query(MarketEvidence).order_by(MarketEvidence.sport.asc(), MarketEvidence.market.asc()).limit(500).all()
    return {
        "markets": [
            {
                "sport": row.sport, "market": row.market, "settled": row.settled,
                "wins": row.wins, "losses": row.losses,
                "accuracy": round(row.accuracy, 4) if row.accuracy is not None else None,
                "recent_accuracy": round(row.recent_accuracy, 4) if row.recent_accuracy is not None else None,
                "brier_score": round(row.brier_score, 4) if row.brier_score is not None else None,
                "expected_value": round(row.expected_value, 4) if row.expected_value is not None else None,
                "publication_blocked": False,
                "block_reasons": row.block_reasons or [],
                "is_model_trained": row.is_model_trained,
                "total_settled": (row.settled or 0) + (row.historical_settled or 0),
                "combined_accuracy": (
                    round(((row.wins or 0) + (row.historical_wins or 0)) / ((row.settled or 0) + (row.historical_settled or 0)), 4)
                    if ((row.settled or 0) + (row.historical_settled or 0)) else None
                ),
                "historical_settled": row.historical_settled or 0,
                "historical_wins": row.historical_wins or 0,
                "historical_losses": row.historical_losses or 0,
                "historical_accuracy": round(row.historical_accuracy, 4) if row.historical_accuracy is not None else None,
            }
            for row in rows
        ],
        "note": "Market evidence is tracked for transparency and performance analysis. It does not gate model prediction generation.",
    }
