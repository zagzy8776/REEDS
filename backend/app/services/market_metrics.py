from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Fixture, OddsSnapshot, Prediction


def selected_decimal_odds(prediction: Prediction, snapshot: OddsSnapshot) -> float | None:
    """Return the decimal odds that correspond to the public pick.

    This first version supports 1X2/moneyline-style odds. Spread/total ROI needs
    line-specific bookmaker odds and push handling, so those markets are skipped
    until the odds feed stores complete line data.
    """

    pick = prediction.pick.lower()
    market = prediction.market.lower()
    if market in {"1x2", "moneyline"}:
        if "home" in pick:
            return snapshot.home_odds
        if "away" in pick:
            return snapshot.away_odds
        if "draw" in pick:
            return snapshot.draw_odds
    return None


def prediction_won(prediction: Prediction, fixture: Fixture) -> bool | None:
    if fixture.home_score is None or fixture.away_score is None:
        return None
    pick = prediction.pick.lower()
    market = prediction.market.lower()
    if market in {"1x2", "moneyline"}:
        if "home" in pick:
            return fixture.home_score > fixture.away_score
        if "away" in pick:
            return fixture.away_score > fixture.home_score
        if "draw" in pick:
            return fixture.home_score == fixture.away_score
    if market == "goals":
        total = fixture.home_score + fixture.away_score
        if "over 2.5" in pick:
            return total > 2.5
        if "under 2.5" in pick:
            return total < 2.5
    if market == "btts":
        both_scored = fixture.home_score > 0 and fixture.away_score > 0
        if "yes" in pick:
            return both_scored
        if "no" in pick:
            return not both_scored
    return None


def latest_snapshot(db: Session, prediction_id: int, phase: str) -> OddsSnapshot | None:
    return (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.prediction_id == prediction_id, OddsSnapshot.phase == phase)
        .order_by(OddsSnapshot.captured_at.desc())
        .first()
    )


def roi_clv_summary(db: Session) -> dict:
    rows = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(Prediction.is_published == True, Fixture.home_score != None, Fixture.away_score != None)
        .all()
    )
    roi_total = roi_profit = clv_total = clv_positive = 0
    by_market: dict[str, dict] = {}

    for pred, fx in rows:
        published = latest_snapshot(db, pred.id, "published")
        if not published:
            continue
        published_odds = selected_decimal_odds(pred, published)
        won = prediction_won(pred, fx)
        if published_odds and won is not None:
            roi_total += 1
            profit = (published_odds - 1) if won else -1
            roi_profit += profit
            market_row = by_market.setdefault(pred.market, {"market": pred.market, "bets": 0, "profit": 0.0, "clv_total": 0, "clv_positive": 0})
            market_row["bets"] += 1
            market_row["profit"] += profit

            closing = latest_snapshot(db, pred.id, "closing")
            if closing:
                closing_odds = selected_decimal_odds(pred, closing)
                if closing_odds:
                    clv_total += 1
                    market_row["clv_total"] += 1
                    # For decimal odds, beating the close means the published price
                    # was higher than the closing price for the same selection.
                    if published_odds > closing_odds:
                        clv_positive += 1
                        market_row["clv_positive"] += 1

    return {
        "tracked_bets": roi_total,
        "profit_units": round(roi_profit, 2),
        "roi_percent": round((roi_profit / roi_total) * 100, 2) if roi_total else 0,
        "clv_tracked": clv_total,
        "positive_clv_rate": round((clv_positive / clv_total) * 100, 2) if clv_total else 0,
        "by_market": [
            {
                **row,
                "profit": round(row["profit"], 2),
                "roi_percent": round((row["profit"] / row["bets"]) * 100, 2) if row["bets"] else 0,
                "positive_clv_rate": round((row["clv_positive"] / row["clv_total"]) * 100, 2) if row["clv_total"] else 0,
            }
            for row in by_market.values()
        ],
        "note": "ROI is calculated as flat 1-unit staking on supported settled markets. CLV requires matching closing odds snapshots.",
    }