"""Market-level publication gate based on empirical evidence.

The public board must not publish a market merely because its mathematical
confidence exceeds a generic threshold. This module tracks, per (sport, market):

  * settled prediction counts, wins, losses, pushes
  * overall and recent empirical accuracy
  * Brier score where probabilities are available
  * expected value / ROI when bookmaker odds exist
  * recent loss streaks

A market with insufficient evidence, poor recent accuracy, or repeated losses
is blocked from public publication while remaining internally evaluated.

Which markets are "model-trained" vs analytically derived (Poisson/heuristic)
is declared here so analytical markets face a higher evidence bar.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Fixture, MarketEvidence, OddsSnapshot, Prediction

log = logging.getLogger(__name__)

# Minimum settled sample before a market may appear publicly.
MIN_PUBLIC_SAMPLE = 20
# Minimum recent (last 30 days) settled sample for the recent-accuracy gate.
MIN_RECENT_SAMPLE = 8
# Below this empirical accuracy a market is blocked regardless of confidence.
MIN_EMPIRICAL_ACCURACY = 0.45
MIN_RECENT_ACCURACY = 0.40
# Consecutive losses that suppress a market even with a decent overall sample.
MAX_LOSS_STREAK = 5
# Markets derived from the trained classifier / directly learned.
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
# Analytical markets (Poisson / heuristic) — these need stronger evidence.
ANALYTICAL_MARKETS = {
    "Double Chance",
    "Over/Under 1.5",
    "Over/Under 2.5",
    "Over/Under 3.5",
    "Both Teams to Score",
    "BTTS",
    "Goals",
    "Correct Score",
    "Spread",
    "Point Spread",
    "Run Line",
    "Total Points",
    "Total Runs",
    "Total Games",
}

RECENT_WINDOW_DAYS = 30


def market_is_model_trained(sport: str, market: str) -> bool:
    """Return whether the market is directly learned by the trained classifier."""
    return market in MODEL_TRAINED_MARKETS.get(str(sport).strip().lower(), set())


def _outcome(prediction: Prediction, fixture: Fixture) -> bool | None:
    """Resolve a settled outcome without re-implementing every market rule.

    The settlement logic lives in prediction_learning.prediction_result; this
    wrapper keeps a single source of truth.
    """
    from app.services.prediction_learning import prediction_result
    return prediction_result(prediction, fixture)


def _selected_odds(prediction: Prediction, snapshot: OddsSnapshot | None) -> float | None:
    if snapshot is None:
        return None
    from app.services.market_metrics import selected_decimal_odds
    return selected_decimal_odds(prediction, snapshot)


def _latest_public_rows(db: Session, limit: int = 12000):
    """Settled active predictions, deduplicated to newest version per fixture+market.

    Both published and internally-retained (is_published=False) active picks are
    included: internal picks exist precisely so a market can build settled
    evidence before it may appear publicly. Active-only + newest-version
    deduplication guarantees superseded predictions are never double-counted
    and a loss is recorded exactly once per fixture+market.
    """
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
        key = (prediction.fixture_id, prediction.market)
        if key not in latest:
            latest[key] = (prediction, fixture)
    return list(latest.values())


def compute_market_evidence(db: Session) -> dict[str, dict]:
    """Compute and persist per (sport, market) evidence from settled public picks."""
    rows = _latest_public_rows(db)
    by_segment: dict[tuple[str, str], dict[str, Any]] = {}
    for prediction, fixture in rows:
        outcome = _outcome(prediction, fixture)
        if outcome is None:
            continue
        segment = by_segment.setdefault(
            (fixture.sport, prediction.market),
            {
                "settled": 0, "wins": 0, "losses": 0, "pushes": 0,
                "recent_settled": 0, "recent_wins": 0, "recent_losses": 0,
                "loss_streak": 0, "max_loss_streak": 0,
                "brier_sum": 0.0, "brier_count": 0,
                "profit": 0.0, "ev_count": 0,
            },
        )
        segment["settled"] += 1
        if outcome:
            segment["wins"] += 1
            segment["loss_streak"] = 0
        else:
            segment["losses"] += 1
            segment["loss_streak"] += 1
        segment["max_loss_streak"] = max(segment["max_loss_streak"], segment["loss_streak"])

        is_recent = fixture.match_date >= date.today() - timedelta(days=RECENT_WINDOW_DAYS)
        if is_recent:
            segment["recent_settled"] += 1
            if outcome:
                segment["recent_wins"] += 1
            else:
                segment["recent_losses"] += 1

        # EV/ROI from the published odds snapshot when available.
        snapshot = (
            db.query(OddsSnapshot)
            .filter(OddsSnapshot.prediction_id == prediction.id, OddsSnapshot.phase == "published")
            .order_by(OddsSnapshot.captured_at.desc())
            .first()
        )
        odds = _selected_odds(prediction, snapshot)
        if odds and odds > 1.0:
            segment["profit"] += (odds - 1.0) if outcome else -1.0
            segment["ev_count"] += 1

        confidence = float(prediction.confidence or 0.0)
        if 0.0 < confidence <= 100.0:
            p = confidence / 100.0
            segment["brier_sum"] += (1.0 - p) ** 2 if outcome else p ** 2
            segment["brier_count"] += 1

    result: dict[str, dict] = {}
    for (sport, market), seg in by_segment.items():
        settled = seg["settled"]
        wins = seg["wins"]
        losses = seg["losses"]
        accuracy = (wins / settled) if settled else None
        recent_accuracy = (seg["recent_wins"] / seg["recent_settled"]) if seg["recent_settled"] else None
        brier = (seg["brier_sum"] / seg["brier_count"]) if seg["brier_count"] else None
        roi = (seg["profit"] / seg["ev_count"]) if seg["ev_count"] else None

        reasons: list[str] = []
        blocked = False
        if settled < MIN_PUBLIC_SAMPLE:
            blocked = True
            reasons.append(f"insufficient settled sample ({settled} < {MIN_PUBLIC_SAMPLE})")
        if accuracy is not None and accuracy < MIN_EMPIRICAL_ACCURACY:
            blocked = True
            reasons.append(f"empirical accuracy {accuracy:.1%} below {MIN_EMPIRICAL_ACCURACY:.0%}")
        if seg["recent_settled"] >= MIN_RECENT_SAMPLE and recent_accuracy is not None and recent_accuracy < MIN_RECENT_ACCURACY:
            blocked = True
            reasons.append(f"recent accuracy {recent_accuracy:.1%} below {MIN_RECENT_ACCURACY:.0%}")
        if seg["max_loss_streak"] >= MAX_LOSS_STREAK:
            blocked = True
            reasons.append(f"repeated losses (streak of {seg['max_loss_streak']})")
        if not market_is_model_trained(sport, market) and settled < MIN_PUBLIC_SAMPLE * 2:
            blocked = True
            if not reasons:
                reasons.append("analytical market needs a larger settled sample before public publication")

        record = {
            "sport": sport,
            "market": market,
            "settled": settled,
            "wins": wins,
            "losses": losses,
            "pushes": seg["pushes"],
            "recent_settled": seg["recent_settled"],
            "recent_wins": seg["recent_wins"],
            "recent_losses": seg["recent_losses"],
            "accuracy": accuracy,
            "recent_accuracy": recent_accuracy,
            "brier_score": brier,
            "expected_value": roi,
            "roi_units": round(seg["profit"], 4),
            "last_loss_streak": seg["loss_streak"],
            "publication_blocked": blocked,
            "block_reasons": reasons,
            "is_model_trained": market_is_model_trained(sport, market),
        }
        result[f"{sport}|{market}"] = record
        _persist_evidence(db, record)
    return result


def _persist_evidence(db: Session, record: dict) -> None:
    try:
        existing = (
            db.query(MarketEvidence)
            .filter_by(sport=record["sport"], market=record["market"])
            .first()
        )
        if existing is None:
            existing = MarketEvidence(sport=record["sport"], market=record["market"])
            db.add(existing)
        for key in (
            "settled", "wins", "losses", "pushes", "recent_settled", "recent_wins",
            "recent_losses", "accuracy", "recent_accuracy", "brier_score",
            "expected_value", "roi_units", "last_loss_streak", "publication_blocked",
            "block_reasons", "is_model_trained",
        ):
            setattr(existing, key, record[key])
        existing.updated_at = func.now()
    except Exception:
        log.exception("Could not persist market evidence for %s|%s", record.get("sport"), record.get("market"))


def market_publication_policy(db: Session, sport: str, market: str) -> tuple[bool, list[str]]:
    """Return (allowed, reasons) for publishing a market publicly.

    Falls back to a conservative block when no evidence exists yet.
    """
    sport = str(sport or "").strip().lower()
    market = str(market or "")
    evidence = (
        db.query(MarketEvidence)
        .filter_by(sport=sport, market=market)
        .first()
    )
    if evidence is None:
        return False, ["no settled market evidence yet"]
    if evidence.publication_blocked:
        return False, list(evidence.block_reasons or [])
    if evidence.settled < MIN_PUBLIC_SAMPLE:
        return False, [f"insufficient settled sample ({evidence.settled})"]
    return True, []


def market_evidence_summary(db: Session) -> dict:
    """Public-facing, non-secret summary of the market gate."""
    rows = (
        db.query(MarketEvidence)
        .order_by(MarketEvidence.sport.asc(), MarketEvidence.market.asc())
        .limit(500)
        .all()
    )
    return {
        "markets": [
            {
                "sport": row.sport,
                "market": row.market,
                "settled": row.settled,
                "wins": row.wins,
                "losses": row.losses,
                "accuracy": round(row.accuracy, 4) if row.accuracy is not None else None,
                "recent_accuracy": round(row.recent_accuracy, 4) if row.recent_accuracy is not None else None,
                "brier_score": round(row.brier_score, 4) if row.brier_score is not None else None,
                "expected_value": round(row.expected_value, 4) if row.expected_value is not None else None,
                "publication_blocked": row.publication_blocked,
                "block_reasons": row.block_reasons or [],
                "is_model_trained": row.is_model_trained,
            }
            for row in rows
        ],
        "note": "Markets with insufficient evidence or poor empirical accuracy are blocked from public publication.",
    }