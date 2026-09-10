"""Performance / calibration analytics and data-freshness reporting.

Computed from the official settled public record only. Historical walk-forward
evaluations and backtests are reported separately and never mixed into these
live numbers. All multi-row work is done against bounded, deduplicated rows
with snapshot maps loaded in two queries (no N+1).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Fixture, MatchEvent, MatchLineup, ModelVersion, OddsSnapshot, Prediction

log = logging.getLogger(__name__)

CONFIDENCE_BANDS = [("<50", 0, 50), ("50-60", 50, 60), ("60-70", 60, 70), ("70-80", 70, 80), ("80-90", 80, 90), ("90-100", 90, 101)]


def _settled_rows(db: Session, limit: int = 20000) -> list[tuple[Prediction, Fixture, bool]]:
    """Deduplicated settled published predictions, newest first."""
    from app.services.prediction_learning import prediction_result

    rows = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(
            Prediction.is_published == True,
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
        )
        .order_by(Fixture.match_date.desc(), Prediction.version.desc(), Prediction.id.desc())
        .limit(limit)
        .all()
    )
    latest: dict[tuple[int, str], tuple[Prediction, Fixture]] = {}
    for prediction, fixture in rows:
        key = (fixture.id, prediction.market)
        latest.setdefault(key, (prediction, fixture))
    resolved: list[tuple[Prediction, Fixture, bool]] = []
    for prediction, fixture in latest.values():
        outcome = prediction_result(prediction, fixture)
        if outcome is not None:
            resolved.append((prediction, fixture, outcome))
    return resolved


def _snapshot_map(db: Session, prediction_ids: list[int], phase: str) -> dict[int, OddsSnapshot]:
    if not prediction_ids:
        return {}
    rows = (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.prediction_id.in_(prediction_ids), OddsSnapshot.phase == phase)
        .order_by(OddsSnapshot.prediction_id.asc(), OddsSnapshot.captured_at.desc())
        .all()
    )
    result: dict[int, OddsSnapshot] = {}
    for snap in rows:
        result.setdefault(snap.prediction_id, snap)
    return result


def _roi_for(db: Session, rows: list[tuple[Prediction, Fixture, bool]]) -> dict:
    from app.services.market_metrics import selected_decimal_odds

    ids = [p.id for p, _, _ in rows]
    published = _snapshot_map(db, ids, "published")
    closing = _snapshot_map(db, ids, "closing")
    bets = profit = clv_total = clv_positive = 0
    for prediction, _fixture, won in rows:
        snap = published.get(prediction.id)
        if snap is None:
            continue
        odds = selected_decimal_odds(prediction, snap)
        if odds is None or odds <= 1.0:
            continue
        bets += 1
        profit += (odds - 1.0) if won else -1.0
        closes = closing.get(prediction.id)
        if closes:
            closing_odds = selected_decimal_odds(prediction, closes)
            if closing_odds:
                clv_total += 1
                if odds > closing_odds:
                    clv_positive += 1
    return {
        "tracked_bets": bets,
        "profit_units": round(profit, 2),
        "roi_percent": round((profit / bets) * 100.0, 2) if bets else 0.0,
        "clv_tracked": clv_total,
        "positive_clv_rate": round((clv_positive / clv_total) * 100.0, 2) if clv_total else 0.0,
    }


def _segment(n: int, wins: int) -> dict:
    return {
        "predictions": n,
        "wins": wins,
        "losses": n - wins,
        "accuracy": round((wins / n) * 100.0, 1) if n else 0.0,
    }


def performance_summary(db: Session) -> dict[str, Any]:
    rows = _settled_rows(db)
    today = date.today()
    last_7 = [r for r in rows if r[1].match_date >= today - timedelta(days=7)]
    last_30 = [r for r in rows if r[1].match_date >= today - timedelta(days=30)]
    last_100 = rows[:100]

    by_sport: dict[str, list[int]] = {}
    by_market: dict[str, list[int]] = {}
    bands: dict[str, dict[str, float | int]] = {
        label: {"n": 0, "wins": 0, "conf": 0.0, "brier": 0.0, "logloss": 0.0} for label, _, _ in CONFIDENCE_BANDS
    }
    by_side: dict[str, dict[str, float | int]] = {}
    for prediction, _fixture, won in rows:
        win_flag = int(won)
        sport = _fixture.sport or "unknown"
        market = prediction.market or "unknown"
        by_sport.setdefault(sport, [0, 0])[0] += 1
        by_sport[sport][1] += win_flag
        by_market.setdefault(market, [0, 0])[0] += 1
        by_market[market][1] += win_flag

        confidence = float(prediction.confidence)
        p = max(0.0, min(1.0, confidence / 100.0))
        y = float(win_flag)
        for label, low, high in CONFIDENCE_BANDS:
            if low <= confidence < high:
                band = bands[label]
                band["n"] += 1
                band["wins"] += win_flag
                band["conf"] += confidence
                band["brier"] += (p - y) ** 2
                eps = 1e-9
                band["logloss"] += -(y * __import__("math").log(p + eps) + (1 - y) * __import__("math").log(1 - p + eps))
                break

        side = "home" if "home" in prediction.pick.lower() else "away" if "away" in prediction.pick.lower() else "draw" if "draw" in prediction.pick.lower() else "other"
        by_side.setdefault(side, {"n": 0, "wins": 0})["n"] += 1
        by_side[side]["wins"] += win_flag

    calibration = []
    for label, _, _ in CONFIDENCE_BANDS:
        band = bands[label]
        n = int(band["n"])
        calibration.append({
            "band": label,
            "sample": n,
            "predicted": round(float(band["conf"]) / n, 1) if n else None,
            "actual": round(int(band["wins"]) / n * 100.0, 1) if n else None,
            "brier_score": round(float(band["brier"]) / n, 3) if n else None,
            "log_loss": round(float(band["logloss"]) / n, 3) if n else None,
        })

    return {
        "label": "LIVE RECORD",
        "label_note": "Official live/public predictions only. Backtests and historical walk-forward evaluations are shown separately and are not mixed into these numbers.",
        "segments": {
            "all_time": _segment(len(rows), sum(1 for _, _, r in rows if r)),
            "last_7_days": _segment(len(last_7), sum(1 for _, _, r in last_7 if r)),
            "last_30_days": _segment(len(last_30), sum(1 for _, _, r in last_30 if r)),
            "last_100": _segment(len(last_100), sum(1 for _, _, r in last_100 if r)),
        },
        "roi": _roi_for(db, rows),
        "by_sport": [{"sport": k, **_segment(v[0], v[1])} for k, v in sorted(by_sport.items())],
        "by_market": [{"market": k, **_segment(v[0], v[1])} for k, v in sorted(by_market.items())],
        "by_side": [
            {"side": k, **_segment(int(v["n"]), int(v["wins"]))} for k, v in sorted(by_side.items())
        ],
        "calibration": calibration,
        "sample_size": len(rows),
        "as_of": datetime.utcnow().isoformat(),
    }


def data_status(db: Session) -> dict[str, Any]:
    """Lightweight freshness read with real timestamps (no fabricated liveness)."""
    now = datetime.utcnow()

    def _latest(model, column):
        return db.query(func.max(column)).scalar()

    fixtures_at = _latest(Fixture, Fixture.created_at)
    odds_at = _latest(OddsSnapshot, OddsSnapshot.captured_at)
    lineups_at = _latest(MatchLineup, MatchLineup.created_at)
    events_at = _latest(MatchEvent, MatchEvent.created_at)
    model_at = _latest(ModelVersion, ModelVersion.trained_at)

    live_now = 0
    try:
        rows = db.query(Fixture).filter(func.date(Fixture.match_date) == date.today()).limit(400).all()
        live_now = sum(1 for fx in rows if bool((fx.extra or {}).get("live")))
    except Exception:
        log.exception("Could not count live fixtures")

    def _age(value, label):
        if value is None:
            return {"updated_at": None, "status": "unavailable", "age_seconds": None, "label": label}
        seconds = max(0, int((now - value).total_seconds()))
        return {
            "updated_at": value.isoformat(),
            "age_seconds": seconds,
            "status": "fresh" if seconds < 3600 else "stale",
            "label": label,
        }

    return {
        "as_of": now.isoformat(),
        "fixtures": _age(fixtures_at, "Fixtures"),
        "odds": _age(odds_at, "Odds"),
        "lineups": _age(lineups_at, "Lineups"),
        "live_events": _age(events_at, "Live Stats"),
        "model": _age(model_at, "Model"),
        "live_now": live_now,
        "note": "Timestamps are the actual most-recent database update time. Nothing is shown as live unless a fresh write exists.",
    }