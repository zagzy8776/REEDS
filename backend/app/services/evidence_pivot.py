"""Pivot HistoricalEvaluation records into MarketEvidence.historical_* columns.

This is the single merge point between live evidence (settled ``Prediction``
rows, already computed by ``market_gate.compute_market_evidence``) and the
walk-forward historical evidence the bootstrap engine writes.

The pivot only ever maps ``HistoricalEvaluation`` -> ``MarketEvidence``
aggregate columns; it never writes ``Prediction`` rows, never changes the live
counters, and never changes publication thresholds.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.models import HistoricalEvaluation, MarketEvidence

log = logging.getLogger(__name__)


def pivot_historical_evidence(db: Session) -> dict:
    """Aggregate HistoricalEvaluation into MarketEvidence.historical_* columns.

    Returns a provenance summary of markets touched. Idempotent: a second call
    recomputes the aggregates from the evaluation table (upsert on the
    (sport, market) unique key).
    """
    stats = (
        db.query(
            HistoricalEvaluation.sport.label("sport"),
            HistoricalEvaluation.market.label("market"),
            func.count(HistoricalEvaluation.id).label("settled"),
            func.sum(case((HistoricalEvaluation.outcome == "won", 1), else_=0)).label("wins"),
            func.sum(case((HistoricalEvaluation.outcome == "lost", 1), else_=0)).label("losses"),
            func.coalesce(func.sum(HistoricalEvaluation.brier_score), 0.0).label("brier_sum"),
            func.sum(case((HistoricalEvaluation.brier_score.isnot(None), 1), else_=0)).label("brier_count"),
            func.sum(case((HistoricalEvaluation.has_odds == True, 1), else_=0)).label("odds_count"),
            func.coalesce(func.sum(HistoricalEvaluation.roi_units), 0.0).label("roi"),
        )
        .filter(HistoricalEvaluation.outcome.isnot(None))
        .group_by(HistoricalEvaluation.sport, HistoricalEvaluation.market)
        .all()
    )

    touched = 0
    for row in stats:
        sport = str(row.sport or "").strip().lower()
        market = str(row.market or "")
        if not sport or not market:
            continue
        settled = int(row.settled or 0)
        wins = int(row.wins or 0)
        losses = int(row.losses or 0)
        if settled <= 0:
            continue
        brier_count = int(row.brier_count or 0)
        brier_sum = float(row.brier_sum or 0.0)
        odds_count = int(row.odds_count or 0)
        roi = float(row.roi or 0.0)

        record = (
            db.query(MarketEvidence)
            .filter_by(sport=sport, market=market)
            .first()
        )
        if record is None:
            record = MarketEvidence(sport=sport, market=market)
            db.add(record)
        record.historical_settled = settled
        record.historical_wins = wins
        record.historical_losses = losses
        record.historical_accuracy = (wins / settled) if settled else None
        record.historical_brier_sum = brier_sum
        record.historical_brier_count = brier_count
        record.historical_odds_count = odds_count
        record.historical_roi_units = roi
        record.historical_has_odds = odds_count > 0
        record.bootstrap_updated_at = datetime.utcnow()
        touched += 1

    if touched:
        db.commit()
    return {
        "markets_touched": touched,
        "note": "Historical evaluation aggregates mapped into MarketEvidence.historical_* — never public picks.",
    }