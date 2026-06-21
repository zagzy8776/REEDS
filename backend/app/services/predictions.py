import logging
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Fixture, OddsSnapshot, Prediction


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


def explain_prediction_item(item: dict, fixture: Fixture) -> dict:
    """Guarantee every pick has a clear user-facing explanation.

    Engines normally provide reasoning, but fallback/new-sport feeds can be thin.
    This keeps customer copy transparent by always answering: what was picked,
    how confident the model is, what risk applies, and what signals were checked.
    """

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
        pieces.append(f"Risk is marked {item.get('risk_level', 'Medium')} because confidence and data depth are not guarantees.")
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
    """Load fixtures into a DataFrame.

    Args:
        max_age_days: Cut-off in days. Pass None to load all history (for training).
                      Defaults to 180 days for prediction serving to avoid OOM on free tier.
    """
    rows = db.query(Fixture)
    if max_age_days is not None:
        cutoff = date.today() - timedelta(days=max_age_days)
        rows = rows.filter(func.date(Fixture.match_date) >= cutoff)
    rows = rows.limit(120000).all()
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
    # Supersede by exact market match
    db.query(Prediction).filter(
        Prediction.fixture_id == fixture_id,
        Prediction.market == market,
        Prediction.status == "active",
    ).update({"status": "superseded", "superseded_at": datetime.utcnow()})
    # Also supersede legacy market names that were renamed (e.g., "Total Points" → "Total Games" for tennis)
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
        fixture_id=fx.id,
        prediction_id=pred.id,
        phase=phase,
        market=pred.market,
        home_odds=fx.home_odds,
        draw_odds=fx.draw_odds,
        away_odds=fx.away_odds,
        source=fx.source or "fixture",
    ))


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model-implied odds derivation
# ---------------------------------------------------------------------------

def _prob_to_decimal_odds(prob: float, margin: float = 0.05) -> float | None:
    """Convert a probability to decimal odds with a small vigorish margin.

    Example: 52% probability → 1/0.52 * (1 - 0.05) ≈ 1.83
    Returns None for zero/invalid probabilities.
    """
    try:
        p = float(prob)
        if p <= 0 or p > 1:
            return None
        return round((1.0 / p) * (1.0 - margin), 2)
    except (TypeError, ValueError):
        return None


def _backfill_fixture_odds(db: Session, fx: Fixture, items: list[dict]) -> bool:
    """Write model-implied odds back to the fixture when no bookmaker odds exist.

    Looks for the 1X2/Moneyline item in the engine output, extracts win/draw/loss
    probabilities from engine_meta, converts to decimal odds, and stores them on
    the fixture row. Clearly marked as odds_source='model_implied' in extra so the
    frontend can distinguish them from real bookmaker odds.

    Returns True if odds were written.
    """
    if fx.home_odds is not None or fx.draw_odds is not None or fx.away_odds is not None:
        return False  # real bookmaker odds already present — don't overwrite

    # Find probability data from 1X2 or Moneyline item
    probs: dict | None = None
    for item in items:
        market = str(item.get("market", "")).lower()
        if market in {"1x2", "moneyline"}:
            meta = item.get("engine_meta") or {}
            probs = meta.get("probabilities") or {}
            break

    if not probs:
        return False

    # Soccer: home_win / draw / away_win
    home_p = probs.get("home_win")
    draw_p = probs.get("draw")
    away_p = probs.get("away_win")

    # Basketball/other sports: home_win / away_win (no draw)
    if home_p is None:
        home_p = probs.get("home_win")
    if away_p is None:
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


def generate_today_predictions(db: Session) -> int:
    """Generate predictions for today's and upcoming fixtures.

    Covers both pre-match (no score yet) and live in-progress fixtures so
    odds and predictions stay visible while a game is being played.
    Uses LoyalEdgeEngine (Poisson + form + Elo + draw detection) for soccer and
    GenericSportEngine with real history for all other sports.
    History capped at 90 days to stay within Render free tier RAM.
    """
    from app.ml.generic import GenericSportEngine
    from app.ml.ensemble import LoyalEdgeEngine
    from app.services.model_registry import active_model_path

    today_ref = date.today()
    PRIORITY_LEAGUES = {
        "FIFA World Cup", "UEFA Champions League", "UEFA Europa League",
        "UEFA European Championship", "Copa America", "Africa Cup of Nations",
        "NBA", "NFL", "IPL",
    }
    priority_fixtures = (
        db.query(Fixture)
        .filter(
            func.date(Fixture.match_date) == today_ref,
            Fixture.league.in_(PRIORITY_LEAGUES),
        )
        .all()
    )

    # Include pre-match AND live in-progress fixtures (score may exist but game is ongoing)
    raw_fixtures = (
        db.query(Fixture)
        .filter(
            func.date(Fixture.match_date) >= today_ref,
        )
        .order_by(Fixture.match_date.asc(), Fixture.league.asc())
        .limit(120)
        .all()
    )

    # Separate: pre-match (no score) + live (score set but status is live)
    def _is_live(fx: Fixture) -> bool:
        extra = fx.extra if isinstance(fx.extra, dict) else {}
        return bool(extra.get("live")) or str(extra.get("status", "")).upper() in {
            "1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "IN PROGRESS", "HALF TIME",
        }

    def _needs_prediction(fx: Fixture) -> bool:
        # Always generate for pre-match; also generate/refresh for live games
        if fx.home_score is None and fx.away_score is None:
            return True
        return _is_live(fx)

    raw_fixtures = [fx for fx in raw_fixtures if _needs_prediction(fx)]

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

    # Real 90-day history for form/Elo — capped to avoid OOM on free tier
    history = dataframe_from_db(db, max_age_days=90)

    # Load soccer engine once — falls back gracefully if no model file exists
    soccer_model_path = active_model_path(db, "soccer")
    soccer_engine = LoyalEdgeEngine(soccer_model_path)
    generic_engine = GenericSportEngine()

    count = 0
    for fx in fixtures:
        try:
            if fx.sport == "soccer":
                items = soccer_engine.predict_soccer(history, {
                    "sport": fx.sport,
                    "home_team": fx.home_team,
                    "away_team": fx.away_team,
                    "match_date": fx.match_date,
                    "league": fx.league,
                    "home_odds": fx.home_odds,
                    "draw_odds": fx.draw_odds,
                    "away_odds": fx.away_odds,
                })
            else:
                items = generic_engine.predict(history, {"sport": fx.sport, "home_team": fx.home_team, "away_team": fx.away_team, "match_date": fx.match_date})
            published_indexes = select_public_picks(items)
            fallback_idx = None
            if not published_indexes:
                fallback_item = choose_provisional_public_pick(items)
                if fallback_item:
                    fallback_idx = items.index(fallback_item) if fallback_item in items else None

            # Backfill model-implied odds onto fixture if no bookmaker odds exist
            _backfill_fixture_odds(db, fx, items)
            for idx, item in enumerate(items):
                item = explain_prediction_item(item, fx)
                is_published = idx in published_indexes or idx == fallback_idx
                if idx == fallback_idx and fallback_idx is not None:
                    item = {**item, "reasoning": f"Best available model read for this fixture. {item.get('reasoning', '')}"}
                _supersede_active_prediction(db, fx.id, item["market"])
                version = _next_prediction_version(db, fx.id, item["market"])
                pred = Prediction(
                    fixture_id=fx.id,
                    model_version_id=None,
                    version=version,
                    status="active",
                    market=str(item.get("market", "")),
                    pick=str(item.get("pick", "")),
                    confidence=float(item.get("confidence", 0)),
                    edge_score=float(item.get("edge_score", 0)),
                    risk_level=str(item.get("risk_level", "Medium")),
                    reasoning=str(item.get("reasoning", "")),
                    engine_meta=item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else None,
                    is_premium=is_published and float(item.get("confidence", 0)) >= 70,
                    is_published=is_published,
                    published_at=datetime.utcnow() if is_published else None,
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
    picks = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(Prediction.confidence >= min_confidence, Prediction.is_published == True, Prediction.status == "active", func.date(Fixture.match_date) >= today_ref).order_by(Prediction.confidence.desc()).all()
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
