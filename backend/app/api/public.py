from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import Fixture, ModelVersion, Prediction
from app.db.session import get_db
from app.services.predictions import build_combo


router = APIRouter()


def serialize_prediction(p: Prediction, f: Fixture) -> dict:
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
    }


@router.get("/predictions/today")
def today(db: Session = Depends(get_db)):
    rows = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(Prediction.is_published == True).order_by(Prediction.confidence.desc()).limit(100).all()
    return [serialize_prediction(p, f) for p, f in rows]


@router.get("/predictions/combo")
def combo_endpoint(legs: int = 3, min_confidence: float = 60, db: Session = Depends(get_db)):
    out = [serialize_prediction(p, f) for p, f in build_combo(db, legs, min_confidence)]
    combined = round(sum(x["confidence"] for x in out) / len(out), 1) if out else 0
    return {"label": "LOYAL EDGE 3-Leg Combo", "combined_confidence": combined, "risk_level": "Low" if combined >= 72 else "Medium", "legs": out}


@router.get("/stats/backtest")
def stats(db: Session = Depends(get_db)):
    models = db.query(ModelVersion).order_by(ModelVersion.trained_at.desc()).limit(10).all()
    return {"brand": "LOYAL EDGE", "note": "Metrics are historical validation estimates, not guarantees.", "models": [{"sport": m.sport, "type": m.model_type, "accuracy": m.accuracy, "sample_size": m.sample_size, "active": m.is_active, "trained_at": m.trained_at} for m in models]}
