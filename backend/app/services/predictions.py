from datetime import date, datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture, OddsSnapshot, Prediction
from app.ml.basketball import BasketballEngine
from app.ml.ensemble import LoyalEdgeEngine
from app.services.model_registry import active_model


PUBLISH_THRESHOLDS = {
    "1X2": 55,
    "Moneyline": 55,
    "Goals": 55,
    "BTTS": 55,
    "Double Chance": 58,
    "Over/Under 1.5": 58,
    "Over/Under 3.5": 58,
    "Spread": 60,
    "Total Points": 55,
    "Correct Score": 101,  # never publish as a customer pick by default; too volatile
}


def should_publish_pick(item: dict) -> bool:
    """Strict customer protection filter.

    The engine may produce many internal picks, but only stronger markets should be
    public. This helps avoid sending weak/noisy predictions to customers.
    """

    threshold = PUBLISH_THRESHOLDS.get(item.get("market", ""), 68)
    return float(item.get("confidence", 0)) >= threshold and item.get("risk_level") != "High"


def choose_provisional_public_pick(items: list[dict]) -> dict | None:
    """Keep the board populated when live fixtures exist but strict filters reject all picks.

    The normal publish filter remains conservative. For brand-new live feeds with no
    trained model/odds yet, every generated market can fall just below threshold,
    leaving customers with fixtures but no AI reads. In that case publish one
    clearly-labelled non-correct-score read per fixture so the product is useful
    while still preserving the original confidence and risk level.
    """

    candidates = [item for item in items if item.get("market") != "Correct Score"]
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("confidence", 0)))


def select_public_picks(items: list[dict], max_picks: int = 4) -> set[int]:
    """Publish a healthy mix of markets instead of only the single highest pick.

    Betting users expect multiple angles per fixture: result, safer double-chance,
    totals, and BTTS. We still avoid correct score by default because it is highly
    volatile, but we allow the strongest markets through even while a model is young.
    """

    eligible = [
        (idx, item)
        for idx, item in enumerate(items)
        if item.get("market") != "Correct Score" and item.get("risk_level") != "High"
    ]
    published = {idx for idx, item in eligible if should_publish_pick(item)}
    if len(published) < max_picks:
        for idx, _ in sorted(eligible, key=lambda pair: float(pair[1].get("confidence", 0)), reverse=True):
            published.add(idx)
            if len(published) >= max_picks:
                break
    return published


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
        published_indexes = select_public_picks(items)
        publish_fallback = choose_provisional_public_pick(items) if not published_indexes else None
        for idx, item in enumerate(items):
            is_published = idx in published_indexes or item is publish_fallback
            if item is publish_fallback:
                item = {**item, "reasoning": f"Best available model read for this fixture. {item.get('reasoning', '')}"}
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
