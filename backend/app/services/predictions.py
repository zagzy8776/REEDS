import logging
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Fixture, OddsSnapshot, Prediction
from app.services.prediction_quality import annotate_quality, evaluate_publication


PUBLISH_THRESHOLDS = {
    "1X2": 55,
    "Moneyline": 55,
    "Goals": 55,
    "BTTS": 55,
    "Both Teams to Score": 55,
    "Double Chance": 58,
    "Over/Under 1.5": 58,
    "Over/Under 2.5": 55,
    "Over/Under 3.5": 58,
    "Spread": 60,
    "Point Spread": 60,
    "Run Line": 58,
    "Total Points": 55,
    "Total Runs": 55,
    "Total Games": 55,
    "Correct Score": 101,
}


def should_publish_pick(item: dict) -> bool:
    threshold = PUBLISH_THRESHOLDS.get(item.get("market", ""), 68)
    return float(item.get("confidence", 0)) >= threshold and item.get("risk_level") != "High"


def choose_provisional_public_pick(items: list[dict]) -> dict | None:
    candidates = [item for item in items if item.get("market") != "Correct Score"]
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("confidence", 0)))


def select_public_picks(items: list[dict], max_picks: int = 4) -> set[int]:
    """Select only quality-approved public picks; never bypass quality controls."""
    published: set[int] = set()
    for idx, raw_item in enumerate(items):
        item = annotate_quality(raw_item)
        if should_publish_pick(item) and evaluate_publication(item)[0]:
            published.add(idx)

    # Do not fill remaining slots with weak predictions. Internal predictions remain
    # stored for diagnostics, but customers only see picks that clear the gate.
    if len(published) > max_picks:
        ranked = sorted(
            published,
            key=lambda idx: float(items[idx].get("confidence", 0)),
            reverse=True,
        )
        published = set(ranked[:max_picks])
    return published


def explain_prediction_item(item: dict, fixture: Fixture) -> dict:
    reasoning = str(item.get("reasoning") or "").strip()
    meta = item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else {}
    summary = str(meta.get("summary") or "").strip()
    market_logic = str(meta.get("market_logic") or "").strip()
    factors = meta.get("factors") if isinstance(meta.get("factors"), list) else []
    factor_text = "; ".join(
        f"{factor.get('label')}: {factor.get('value')}"
        for factor in factors[:3]
        if isinstance(factor, dict) and factor.get("label") is not None
    )
    if not reasoning:
        pieces = [
            f"The model chose {item.get('pick')} in the {item.get('market')} market at {item.get('confidence')}% confidence.",
            market_logic or summary or f"It compared available {fixture.sport.replace('_', ' ')} history, recent scoring/results, and home/away context.",
        ]
        if factor_text:
            pieces.append(f"Key signals: {factor_text}.")
        pieces.append(f"Risk is marked {item.get('risk_level', 'Medium')} based on confidence and available data depth.")
        reasoning = " ".join(pieces)
    elif "risk" not in reasoning.lower():
        reasoning = f"{reasoning} Risk is marked {item.get('risk_level', 'Medium')} based on confidence and available data depth."
    item["reasoning"] = reasoning
    item["engine_meta"] = {
        "summary": summary or f"LOYAL EDGE reviewed {fixture.sport.replace('_', ' ')} fixture context before selecting this market.",
        **meta,
        "customer_explanation": reasoning,
    }
    return item


def dataframe_from_db(db: Session, max_age_days: int | None = 180) -> pd.DataFrame:
    rows = db.query(Fixture)
    if max_age_days is not None:
        cutoff = date.today() - timedelta(days=max_age_days)
        rows = rows.filter(func.date(Fixture.match_date) >= cutoff)
    rows = rows.limit(120000).all()
    return pd.DataFrame([{
        "id": r.id, "sport": r.sport, "league": r.league, "season": r.season,
        "match_date": r.match_date, "home_team": r.home_team, "away_team": r.away_team,
        "home_score": r.home_score, "away_score": r.away_score,
        "home_odds": r.home_odds, "draw_odds": r.draw_odds, "away_odds": r.away_odds,
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
    legacy_markets = {
        "Total Games": ["Total Points"],
        "Point Spread": ["Spread"],
        "Run Line": ["Spread"],
        "Both Teams to Score": ["BTTS"],
        "Over/Under 2.5": ["Goals"],
    }
    new_to_old = {v: k for k, vals in legacy_markets.items() for v in vals}
    old_market = new_to_old.get(market)
    if old_market:
        db.query(Prediction).filter(
            Prediction.fixture_id == fixture_id,
            Prediction.market == old_market,
            Prediction.status == "active",
        ).update({"status": "superseded", "superseded_at": datetime.utcnow()})


def _capture_odds_snapshot(db: Session, fx: Fixture, pred: Prediction, phase: str) -> None:
    if fx.home_odds is None and fx.draw_odds is None and fx.away_odds is None:
        return
    db.add(OddsSnapshot(
        fixture_id=fx.id, prediction_id=pred.id, phase=phase, market=pred.market,
        home_odds=fx.home_odds, draw_odds=fx.draw_odds, away_odds=fx.away_odds,
        source=fx.source or "fixture",
    ))


log = logging.getLogger(__name__)


def _prob_to_decimal_odds(prob: float, margin: float = 0.05) -> float | None:
    try:
        p = float(prob)
        if p <= 0 or p > 1:
            return None
        return round((1.0 / p) * (1.0 - margin), 2)
    except (TypeError, ValueError):
        return None


def _backfill_fixture_odds(db: Session, fx: Fixture, items: list[dict]) -> bool:
    if fx.home_odds is not None or fx.draw_odds is not None or fx.away_odds is not None:
        return False
    probs: dict | None = None
    for item in items:
        market = str(item.get("market", "")).lower()
        if market in {"1x2", "moneyline"}:
            meta = item.get("engine_meta") or {}
            probs = meta.get("probabilities") or {}
            break
    if not probs:
        return False
    home_p = probs.get("home_win")
    draw_p = probs.get("draw")
    away_p = probs.get("away_win")
    if not home_p and not away_p:
        return False
    fx.home_odds = _prob_to_decimal_odds(home_p)
    fx.draw_odds = _prob_to_decimal_odds(draw_p) if draw_p else None
    fx.away_odds = _prob_to_decimal_odds(away_p)
    extra = dict(fx.extra or {})
    extra["odds_source"] = "model_implied"
    extra["odds_note"] = "Fair-value odds derived from model probabilities. Not bookmaker prices."
    fx.extra = extra
    return True


def _prediction_signature(item: dict) -> tuple:
    """Stable signature used to avoid needless prediction churn."""
    meta = item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else {}
    probs = meta.get("probabilities") if isinstance(meta.get("probabilities"), dict) else {}
    normalized_probs = tuple(sorted(
        (str(k), round(float(v), 4))
        for k, v in probs.items()
        if isinstance(v, (int, float))
    ))
    return (
        str(item.get("market", "")),
        str(item.get("pick", "")),
        round(float(item.get("confidence", 0)), 3),
        round(float(item.get("edge_score", 0)), 5),
        normalized_probs,
    )


def _existing_prediction_changed(pred: Prediction, item: dict) -> bool:
    """Only version a prediction when the model's actual read changed materially."""
    meta = pred.engine_meta if isinstance(pred.engine_meta, dict) else {}
    old_sig = meta.get("prediction_signature")
    return old_sig != _prediction_signature(item)


def generate_today_predictions(db: Session) -> int:
    from app.ml.generic import GenericSportEngine
    from app.ml.ensemble import LoyalEdgeEngine
    from app.services.model_registry import active_model_path

    today_ref = date.today()
    PRIORITY_LEAGUES = {
        "FIFA World Cup", "UEFA Champions League", "UEFA Europa League",
        "UEFA European Championship", "Copa America", "Africa Cup of Nations",
        "NBA", "NFL", "IPL",
    }
    priority_fixtures = db.query(Fixture).filter(
        func.date(Fixture.match_date) == today_ref,
        Fixture.league.in_(PRIORITY_LEAGUES),
    ).all()
    raw_fixtures = db.query(Fixture).filter(
        func.date(Fixture.match_date) >= today_ref,
    ).order_by(Fixture.match_date.asc(), Fixture.league.asc()).limit(120).all()

    def _is_live(fx: Fixture) -> bool:
        extra = fx.extra if isinstance(fx.extra, dict) else {}
        return bool(extra.get("live")) or str(extra.get("status", "")).upper() in {
            "1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "IN PROGRESS", "HALF TIME",
        }

    raw_fixtures = [
        fx for fx in raw_fixtures
        if (fx.home_score is None and fx.away_score is None) or _is_live(fx)
    ]
    if not raw_fixtures and not priority_fixtures:
        return 0
    by_sport: dict[str, list[Fixture]] = {}
    for fx in raw_fixtures:
        by_sport.setdefault(fx.sport, []).append(fx)
    fixtures = []
    for sport in sorted(by_sport.keys()):
        fixtures.extend(by_sport[sport][:10])
    seen_ids = {fx.id for fx in fixtures}
    fixtures = [fx for fx in priority_fixtures if fx.id not in seen_ids] + fixtures
    fixtures = sorted(fixtures, key=lambda fx: (fx.match_date, fx.league, fx.sport))[:50]

    history = dataframe_from_db(db, max_age_days=90)
    soccer_model_path = active_model_path(db, "soccer")
    soccer_engine = LoyalEdgeEngine(soccer_model_path)
    generic_engine = GenericSportEngine()

    count = 0
    for fx in fixtures:
        try:
            if fx.sport == "soccer":
                items = soccer_engine.predict_soccer(history, {
                    "id": fx.id, "_db": db, "sport": fx.sport,
                    "home_team": fx.home_team, "away_team": fx.away_team,
                    "match_date": fx.match_date, "league": fx.league,
                    "home_odds": fx.home_odds, "draw_odds": fx.draw_odds, "away_odds": fx.away_odds,
                })
            else:
                items = generic_engine.predict(history, {
                    "sport": fx.sport, "home_team": fx.home_team,
                    "away_team": fx.away_team, "match_date": fx.match_date,
                })

            _backfill_fixture_odds(db, fx, items)
            published_indexes = select_public_picks(items)
            for idx, item in enumerate(items):
                item = annotate_quality(explain_prediction_item(item, fx))
                is_published = idx in published_indexes
                signature = _prediction_signature(item)
                meta = dict(item.get("engine_meta") or {})
                meta["prediction_signature"] = signature

                existing = (
                    db.query(Prediction)
                    .filter(
                        Prediction.fixture_id == fx.id,
                        Prediction.market == str(item.get("market", "")),
                        Prediction.status == "active",
                    )
                    .order_by(Prediction.version.desc())
                    .first()
                )
                if existing and not _existing_prediction_changed(existing, item):
                    if existing.is_published != is_published:
                        existing.is_published = is_published
                        existing.published_at = datetime.utcnow() if is_published else None
                    continue

                if existing:
                    existing.status = "superseded"
                    existing.superseded_at = datetime.utcnow()
                version = _next_prediction_version(db, fx.id, str(item.get("market", "")))
                pred = Prediction(
                    fixture_id=fx.id, model_version_id=None, version=version, status="active",
                    market=str(item.get("market", "")), pick=str(item.get("pick", "")),
                    confidence=float(item.get("confidence", 0)), edge_score=float(item.get("edge_score", 0)),
                    risk_level=str(item.get("risk_level", "Medium")), reasoning=str(item.get("reasoning", "")),
                    engine_meta=meta, is_premium=is_published and float(item.get("confidence", 0)) >= 70,
                    is_published=is_published, published_at=datetime.utcnow() if is_published else None,
                )
                db.add(pred)
                db.flush()
                _capture_odds_snapshot(db, fx, pred, "published" if is_published else "initial")
                count += 1
            db.flush()
        except Exception:
            log.exception("Failed to generate predictions for fixture %d (%s: %s vs %s)", fx.id, fx.sport, fx.home_team, fx.away_team)
            db.rollback()
            continue
    db.commit()
    return count


def build_combo(db: Session, legs: int = 3, min_confidence: float = 60):
    today_ref = date.today()
    picks = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(
        Prediction.confidence >= min_confidence, Prediction.is_published == True,
        Prediction.status == "active", func.date(Fixture.match_date) >= today_ref,
    ).order_by(Prediction.confidence.desc()).all()
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
