from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture, Prediction
from app.ml.basketball import BasketballEngine
from app.ml.ensemble import LoyalEdgeEngine
from app.services.model_registry import active_model_path


PUBLISH_THRESHOLDS = {
    "1X2": 64,
    "Moneyline": 64,
    "Goals": 66,
    "BTTS": 66,
    "Spread": 68,
    "Total Points": 66,
    "Correct Score": 101,  # never publish as a customer pick by default; too volatile
}


def should_publish_pick(item: dict) -> bool:
    """Strict customer protection filter.

    The engine may produce many internal picks, but only stronger markets should be
    public. This helps avoid sending weak/noisy predictions to customers.
    """

    threshold = PUBLISH_THRESHOLDS.get(item.get("market", ""), 68)
    return float(item.get("confidence", 0)) >= threshold and item.get("risk_level") != "High"


def dataframe_from_db(db: Session) -> pd.DataFrame:
    rows = db.query(Fixture).all()
    return pd.DataFrame([{
        "id": r.id,
        "sport": r.sport,
        "league": r.league,
        "season": r.season,
        "match_date": r.match_date,
        "home_team": r.home_team,
        "away_team": r.away_team,
        "home_score": r.home_score,
        "away_score": r.away_score,
        "home_odds": r.home_odds,
        "draw_odds": r.draw_odds,
        "away_odds": r.away_odds,
    } for r in rows])


def generate_today_predictions(db: Session) -> int:
    history = dataframe_from_db(db)
    fixtures = db.query(Fixture).filter(Fixture.match_date >= date.today(), Fixture.sport.in_(["soccer", "basketball"])).order_by(Fixture.match_date.asc()).limit(40).all()
    soccer_engine = LoyalEdgeEngine(active_model_path(db, "soccer"))
    basketball_engine = BasketballEngine(active_model_path(db, "basketball"))
    count = 0
    for fx in fixtures:
        db.query(Prediction).filter_by(fixture_id=fx.id).delete()
        if fx.sport == "basketball":
            items = basketball_engine.predict(history, {"home_team": fx.home_team, "away_team": fx.away_team})
        else:
            items = soccer_engine.predict_soccer(history, {"home_team": fx.home_team, "away_team": fx.away_team})
        for item in items:
            is_published = should_publish_pick(item)
            db.add(Prediction(fixture_id=fx.id, **item, is_premium=is_published and item["confidence"] >= 70, is_published=is_published))
            count += 1
    db.commit()
    return count


def build_combo(db: Session, legs: int = 3, min_confidence: float = 60):
    picks = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(Prediction.confidence >= min_confidence, Prediction.is_published == True).order_by(Prediction.confidence.desc()).all()
    selected, teams = [], set()
    for pred, fx in picks:
        if fx.home_team in teams or fx.away_team in teams:
            continue
        selected.append((pred, fx))
        teams.update([fx.home_team, fx.away_team])
        if len(selected) == legs:
            break
    return selected
