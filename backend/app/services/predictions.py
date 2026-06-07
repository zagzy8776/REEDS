from datetime import date, datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture, OddsSnapshot, Prediction
from app.ml.basketball import BasketballEngine
from app.ml.ensemble import LoyalEdgeEngine
from app.services.model_registry import active_model


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


def _next_prediction_version(db: Session, fixture_id: int, market: str) -> int:
    latest = (
        db.query(Prediction)
        .filter(Prediction.fixture_id == fixture_id, Prediction.market == market)
        .order_by(Prediction.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


def _supersede_active_prediction(db: Session, fixture_id: int, market: str) -> None:
    db.query(Prediction).filter(
        Prediction.fixture_id == fixture_id,
        Prediction.market == market,
        Prediction.status == "active",
    ).update({"status": "superseded", "superseded_at": datetime.utcnow()})


def _capture_odds_snapshot(db: Session, fx: Fixture, pred: Prediction, phase: str) -> None:
    if fx.home_odds is None and fx.draw_odds is None and fx.away_odds is None:
        return
    db.add(OddsSnapshot(
        fixture_id=fx.id,
        prediction_id=pred.id,
        phase=phase,
        market=pred.market,
        home_odds=fx.home_odds,
        draw_odds=fx.draw_odds,
        away_odds=fx.away_odds,
        source=fx.source or "fixture",
    ))


def generate_today_predictions(db: Session) -> int:
    history = dataframe_from_db(db)
    fixtures = db.query(Fixture).filter(Fixture.match_date >= date.today(), Fixture.sport.in_(["soccer", "basketball"])).order_by(Fixture.match_date.asc()).limit(40).all()
    soccer_model = active_model(db, "soccer")
    basketball_model = active_model(db, "basketball")
    soccer_engine = LoyalEdgeEngine(soccer_model.path if soccer_model else None)
    basketball_engine = BasketballEngine(basketball_model.path if basketball_model else None)
    count = 0
    for fx in fixtures:
        if fx.sport == "basketball":
            model_version_id = basketball_model.id if basketball_model else None
            items = basketball_engine.predict(history, {"home_team": fx.home_team, "away_team": fx.away_team, "match_date": fx.match_date})
        else:
            model_version_id = soccer_model.id if soccer_model else None
            items = soccer_engine.predict_soccer(history, {"home_team": fx.home_team, "away_team": fx.away_team, "match_date": fx.match_date, "league": fx.league, "home_odds": fx.home_odds, "draw_odds": fx.draw_odds, "away_odds": fx.away_odds})
        for item in items:
            is_published = should_publish_pick(item)
            _supersede_active_prediction(db, fx.id, item["market"])
            version = _next_prediction_version(db, fx.id, item["market"])
            pred = Prediction(
                fixture_id=fx.id,
                model_version_id=model_version_id,
                version=version,
                status="active",
                **item,
                is_premium=is_published and item["confidence"] >= 70,
                is_published=is_published,
                published_at=datetime.utcnow() if is_published else None,
            )
            db.add(pred)
            db.flush()
            _capture_odds_snapshot(db, fx, pred, "published" if is_published else "initial")
            count += 1
    db.commit()
    return count


def build_combo(db: Session, legs: int = 3, min_confidence: float = 60):
    picks = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(Prediction.confidence >= min_confidence, Prediction.is_published == True, Prediction.status == "active", Fixture.match_date >= date.today()).order_by(Prediction.confidence.desc()).all()
    selected, teams = [], set()
    for pred, fx in picks:
        if fx.home_team in teams or fx.away_team in teams:
            continue
        selected.append((pred, fx))
        teams.update([fx.home_team, fx.away_team])
        if len(selected) == legs:
            break
    return selected


def compound_combo_probability(predictions: list[Prediction]) -> float:
    probability = 1.0
    for pred in predictions:
        probability *= max(0.0, min(float(pred.confidence) / 100, 1.0))
    return round(probability * 100, 1) if predictions else 0.0
