from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import Fixture, ModelVersion, Prediction
from app.db.session import get_db
from app.services.predictions import build_combo


router = APIRouter()


def prediction_result(p: Prediction, f: Fixture) -> bool | None:
    if f.home_score is None or f.away_score is None:
        return None

    home_score, away_score = f.home_score, f.away_score
    pick = p.pick.lower()
    market = p.market.lower()

    if market in {"1x2", "moneyline"}:
        if "home" in pick:
            return home_score > away_score
        if "away" in pick:
            return away_score > home_score
        if "draw" in pick:
            return home_score == away_score
    if market == "goals":
        total = home_score + away_score
        if "over 2.5" in pick:
            return total > 2.5
        if "under 2.5" in pick:
            return total < 2.5
    if market == "btts":
        both_scored = home_score > 0 and away_score > 0
        if "yes" in pick:
            return both_scored
        if "no" in pick:
            return not both_scored
    if market == "correct score":
        return pick == f"{home_score}-{away_score}"

    # Spread/total lines need the actual bookmaker line to grade correctly.
    return None


def serialize_prediction(p: Prediction, f: Fixture) -> dict:
    result = prediction_result(p, f)
    return {
        "id": p.id,
        "fixture_id": f.id,
        "sport": f.sport,
        "league": f.league,
        "match_date": f.match_date,
        "home_team": f.home_team,
        "away_team": f.away_team,
        "market": p.market,
        "pick": p.pick,
        "confidence": p.confidence,
        "edge_score": p.edge_score,
        "risk_level": p.risk_level,
        "reasoning": p.reasoning,
        "is_premium": p.is_premium,
        "result": "pending" if result is None else "won" if result else "lost",
    }


@router.get("/predictions/today")
def today(db: Session = Depends(get_db)):
    rows = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(Prediction.is_published == True, Fixture.match_date >= date.today()).order_by(Prediction.confidence.desc()).limit(100).all()
    return [serialize_prediction(p, f) for p, f in rows]


@router.get("/predictions/combo")
def combo_endpoint(legs: int = 3, min_confidence: float = 60, db: Session = Depends(get_db)):
    out = [serialize_prediction(p, f) for p, f in build_combo(db, legs, min_confidence)]
    combined = round(sum(x["confidence"] for x in out) / len(out), 1) if out else 0
    return {"label": "LOYAL EDGE 3-Leg Combo", "combined_confidence": combined, "risk_level": "Low" if combined >= 72 else "Medium", "legs": out}


@router.get("/stats/backtest")
def stats(db: Session = Depends(get_db)):
    models = db.query(ModelVersion).order_by(ModelVersion.trained_at.desc()).limit(10).all()
    rows = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(Fixture.home_score != None, Fixture.away_score != None).all()
    settled = []
    for p, f in rows:
        result = prediction_result(p, f)
        if result is not None:
            settled.append((p, f, result))

    by_sport: dict[str, dict] = {}
    by_market: dict[str, dict] = {}
    confidence_buckets = {"70+": {"total": 0, "wins": 0}, "60-69": {"total": 0, "wins": 0}, "<60": {"total": 0, "wins": 0}}

    for p, f, won in settled:
        sport_row = by_sport.setdefault(f.sport, {"sport": f.sport, "total": 0, "wins": 0})
        market_row = by_market.setdefault(p.market, {"market": p.market, "total": 0, "wins": 0})
        for row in (sport_row, market_row):
            row["total"] += 1
            row["wins"] += 1 if won else 0
        bucket = "70+" if p.confidence >= 70 else "60-69" if p.confidence >= 60 else "<60"
        confidence_buckets[bucket]["total"] += 1
        confidence_buckets[bucket]["wins"] += 1 if won else 0

    def with_hit_rate(row: dict) -> dict:
        total = row["total"]
        return {**row, "hit_rate": round((row["wins"] / total) * 100, 1) if total else 0}

    return {
        "brand": "LOYAL EDGE",
        "note": "Metrics are historical validation estimates and settled-pick tracking, not guarantees.",
        "models": [{"sport": m.sport, "type": m.model_type, "accuracy": m.accuracy, "sample_size": m.sample_size, "active": m.is_active, "trained_at": m.trained_at} for m in models],
        "results": {
            "settled_picks": len(settled),
            "wins": sum(1 for _, _, won in settled if won),
            "losses": sum(1 for _, _, won in settled if not won),
            "hit_rate": round((sum(1 for _, _, won in settled if won) / len(settled)) * 100, 1) if settled else 0,
            "by_sport": [with_hit_rate(x) for x in by_sport.values()],
            "by_market": [with_hit_rate(x) for x in by_market.values()],
            "confidence_buckets": [{"bucket": k, **with_hit_rate(v)} for k, v in confidence_buckets.items()],
        },
    }
