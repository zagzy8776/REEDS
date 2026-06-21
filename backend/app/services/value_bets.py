"""Value betting engine.

Pipeline:
  1. Fetch upcoming fixtures + odds from SportyBet
  2. Strip bookmaker overround → fair probabilities
  3. Run our ML model to get predicted probabilities
  4. Compare: if model_prob × bookmaker_odds > MIN_EDGE → value bet found
  5. Size with Kelly Criterion
  6. Store as ValueBet records, expose via API

Mathematical foundation:
  - Overround stripping: fair_prob = raw_implied / sum(all_implied)
  - Value condition: model_prob × decimal_odds > 1.0
  - Kelly fraction: f* = (b×p - q) / b  where b = odds-1, p = model_prob, q = 1-p
  - We use fractional Kelly (0.25×f*) to protect bankroll from model error
"""

import logging
from datetime import date, datetime
from typing import NamedTuple

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture, Prediction
from app.scraper.sportybet import fetch_all_sports
from app.services.predictions import dataframe_from_db
from app.utils.team_names import normalize_team_name

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MIN_VALUE_EDGE = 1.04          # model_prob × odds must exceed this (4% edge min)
MAX_KELLY_FRACTION = 0.05      # never risk more than 5% of bankroll on one bet
KELLY_MULTIPLIER = 0.25        # fractional Kelly — conservative sizing
MIN_ODDS = 1.30                # ignore very short odds (too risky even with edge)
MAX_ODDS = 15.0                # ignore very long shots (model unreliable)
MIN_MODEL_CONFIDENCE = 0.45    # model must be at least 45% confident


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def strip_overround(home_odds: float, draw_odds: float | None, away_odds: float | None) -> dict:
    """Remove bookmaker margin and return fair (zero-vig) probabilities.

    For 2-way markets (no draw), pass draw_odds=None.
    """
    try:
        implied = {}
        total_implied = 0.0

        if home_odds and home_odds > 1.0:
            implied["home"] = 1.0 / home_odds
            total_implied += implied["home"]
        if draw_odds and draw_odds > 1.0:
            implied["draw"] = 1.0 / draw_odds
            total_implied += implied["draw"]
        if away_odds and away_odds > 1.0:
            implied["away"] = 1.0 / away_odds
            total_implied += implied["away"]

        if total_implied == 0:
            return {}

        overround = total_implied - 1.0
        fair = {k: v / total_implied for k, v in implied.items()}
        return {
            "fair_home": fair.get("home"),
            "fair_draw": fair.get("draw"),
            "fair_away": fair.get("away"),
            "overround": round(overround, 4),
            "margin_pct": round(overround / total_implied * 100, 2),
        }
    except (TypeError, ValueError, ZeroDivisionError):
        return {}


def kelly_fraction(model_prob: float, decimal_odds: float) -> float:
    """Full Kelly fraction for a single outcome.

    Returns 0 if no edge exists. Always cap at MAX_KELLY_FRACTION.
    """
    if decimal_odds <= 1.0 or model_prob <= 0:
        return 0.0
    b = decimal_odds - 1.0          # net profit per unit staked
    p = model_prob
    q = 1.0 - p
    f = (b * p - q) / b
    if f <= 0:
        return 0.0
    return min(f * KELLY_MULTIPLIER, MAX_KELLY_FRACTION)


def value_score(model_prob: float, decimal_odds: float) -> float:
    """Expected value score: model_prob × odds. > 1.0 = positive EV."""
    try:
        return round(float(model_prob) * float(decimal_odds), 4)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Model probability extraction
# ---------------------------------------------------------------------------

def _get_model_probs(
    history: pd.DataFrame,
    fixture: dict,
) -> dict[str, float] | None:
    """Run our ML engines and return win/draw/loss probabilities."""
    try:
        sport = fixture.get("sport", "soccer")

        if sport == "soccer":
            from app.ml.ensemble import LoyalEdgeEngine
            from app.services.model_registry import active_model_path
            from app.db.session import SessionLocal
            db = SessionLocal()
            try:
                path = active_model_path(db, "soccer")
            finally:
                db.close()
            engine = LoyalEdgeEngine(path)
            items = engine.predict_soccer(history, {
                "sport": "soccer",
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "match_date": fixture.get("match_date"),
                "league": fixture.get("league", ""),
                "home_odds": fixture.get("home_odds"),
                "draw_odds": fixture.get("draw_odds"),
                "away_odds": fixture.get("away_odds"),
            })
            # Extract from 1X2 market
            for item in items:
                if item.get("market") == "1X2":
                    meta = item.get("engine_meta", {})
                    probs = meta.get("probabilities", {})
                    if probs:
                        return {
                            "home": probs.get("home_win", 0.33),
                            "draw": probs.get("draw", 0.33),
                            "away": probs.get("away_win", 0.33),
                        }
        else:
            from app.ml.generic import GenericSportEngine
            engine = GenericSportEngine()
            items = engine.predict(history, {
                "sport": sport,
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "match_date": fixture.get("match_date"),
            })
            for item in items:
                if item.get("market") in ("Moneyline", "1X2"):
                    meta = item.get("engine_meta", {})
                    probs = meta.get("probabilities", {})
                    if probs:
                        return {
                            "home": probs.get("home_win", 0.5),
                            "draw": probs.get("draw"),
                            "away": probs.get("away_win", 0.5),
                        }
    except Exception as exc:
        log.debug("Model prob extraction failed for %s vs %s: %s",
                  fixture.get("home_team"), fixture.get("away_team"), exc)
    return None


# ---------------------------------------------------------------------------
# Value bet detection
# ---------------------------------------------------------------------------

class ValueBet(NamedTuple):
    home_team: str
    away_team: str
    league: str
    sport: str
    match_date: str | None
    selection: str           # "home" | "draw" | "away"
    bookmaker_odds: float
    fair_odds: float         # 1 / fair_probability
    model_prob: float
    bookmaker_fair_prob: float
    value_score: float       # model_prob × odds — should be > 1.0
    edge_pct: float          # (value_score - 1) × 100
    kelly_fraction: float    # fraction of bankroll to stake
    recommended_stake_pct: float
    sportybet_match_id: str
    source: str


def find_value_bets(
    fixtures: list[dict],
    history: pd.DataFrame,
    min_edge: float = MIN_VALUE_EDGE,
) -> list[ValueBet]:
    """Core value detection loop.

    For each SportyBet fixture:
    1. Strip overround → fair probs
    2. Get model probs
    3. Find selections where model_prob × bookie_odds > min_edge
    4. Size with Kelly
    """
    value_bets: list[ValueBet] = []

    for fx in fixtures:
        home_odds = fx.get("home_odds")
        draw_odds = fx.get("draw_odds")
        away_odds = fx.get("away_odds")

        # Need at least home + away odds
        if not home_odds or not away_odds:
            continue

        # Validate odds range
        for o in [home_odds, draw_odds, away_odds]:
            if o and (o < MIN_ODDS or o > MAX_ODDS):
                continue

        # Strip overround
        fair = strip_overround(home_odds, draw_odds, away_odds)
        if not fair:
            continue

        # Get model probabilities
        model_probs = _get_model_probs(history, fx)
        if not model_probs:
            continue

        # Check each selection
        checks = [
            ("home", home_odds, fair.get("fair_home"), model_probs.get("home")),
            ("draw", draw_odds, fair.get("fair_draw"), model_probs.get("draw")),
            ("away", away_odds, fair.get("fair_away"), model_probs.get("away")),
        ]

        for selection, bookie_odds, fair_prob, model_prob in checks:
            if not bookie_odds or not fair_prob or not model_prob:
                continue
            if model_prob < MIN_MODEL_CONFIDENCE:
                continue

            ev = value_score(model_prob, bookie_odds)
            if ev < min_edge:
                continue

            edge_pct = round((ev - 1.0) * 100, 2)
            kf = kelly_fraction(model_prob, bookie_odds)
            fair_o = round(1.0 / fair_prob, 3) if fair_prob > 0 else 0.0

            value_bets.append(ValueBet(
                home_team=fx["home_team"],
                away_team=fx["away_team"],
                league=fx.get("league", ""),
                sport=fx.get("sport", "soccer"),
                match_date=fx.get("match_date"),
                selection=selection,
                bookmaker_odds=round(bookie_odds, 3),
                fair_odds=fair_o,
                model_prob=round(model_prob, 4),
                bookmaker_fair_prob=round(fair_prob, 4),
                value_score=ev,
                edge_pct=edge_pct,
                kelly_fraction=round(kf, 4),
                recommended_stake_pct=round(kf * 100, 2),
                sportybet_match_id=fx.get("sportybet_match_id", ""),
                source="sportybet",
            ))

    # Sort by edge descending
    return sorted(value_bets, key=lambda v: v.edge_pct, reverse=True)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_value_scan(
    db: Session,
    sports: list[str] | None = None,
    min_edge: float = MIN_VALUE_EDGE,
) -> dict:
    """Full pipeline: scrape SportyBet → strip overround → model → value bets.

    Returns structured results ready for the API.
    """
    target_sports = sports or ["soccer", "basketball", "tennis"]

    # 1. Fetch SportyBet fixtures
    try:
        raw_fixtures = fetch_all_sports(target_sports, limit_per_sport=60)
    except Exception as exc:
        log.exception("SportyBet fetch failed")
        return {"error": str(exc), "value_bets": [], "scanned": 0}

    if not raw_fixtures:
        return {
            "value_bets": [],
            "scanned": 0,
            "message": "No fixtures returned from SportyBet. Their API may have changed or rate-limited the request.",
        }

    # 2. Load recent history for model context
    history = dataframe_from_db(db, max_age_days=90)

    # 3. Find value bets
    bets = find_value_bets(raw_fixtures, history, min_edge=min_edge)

    # 4. Serialise
    def _serialise(vb: ValueBet) -> dict:
        return {
            "home_team": vb.home_team,
            "away_team": vb.away_team,
            "league": vb.league,
            "sport": vb.sport,
            "match_date": vb.match_date,
            "selection": vb.selection,
            "bookmaker": "SportyBet",
            "bookmaker_odds": vb.bookmaker_odds,
            "fair_odds": vb.fair_odds,
            "model_probability": f"{vb.model_prob:.1%}",
            "bookmaker_fair_probability": f"{vb.bookmaker_fair_prob:.1%}",
            "value_score": vb.value_score,
            "edge_pct": vb.edge_pct,
            "kelly_fraction": vb.kelly_fraction,
            "recommended_stake_pct": vb.recommended_stake_pct,
            "sportybet_match_id": vb.sportybet_match_id,
            "label": f"{vb.home_team} vs {vb.away_team} — {vb.selection.upper()} @ {vb.bookmaker_odds}",
            "explanation": (
                f"Model estimates {vb.model_prob:.1%} probability for {vb.selection}. "
                f"SportyBet's fair probability (overround removed) is {vb.bookmaker_fair_prob:.1%}. "
                f"Value score {vb.value_score:.3f} — every £100 staked returns £{vb.value_score*100:.0f} "
                f"in expected value. Recommended Kelly stake: {vb.recommended_stake_pct:.1f}% of bankroll."
            ),
            "responsible_note": "Value bets are probabilistic edges, not guaranteed wins. Stake responsibly.",
        }

    return {
        "scanned": len(raw_fixtures),
        "value_bets_found": len(bets),
        "min_edge_used": min_edge,
        "value_bets": [_serialise(vb) for vb in bets[:50]],  # cap at 50
        "top_pick": _serialise(bets[0]) if bets else None,
        "sports_checked": target_sports,
        "overround_note": "Overround stripped from all odds before comparison. Fair probabilities shown.",
    }
