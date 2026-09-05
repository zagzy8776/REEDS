import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from app.db.models import BacktestRun, CommunityComment, CommunityPlay, CommunityReaction, Fixture, ModelVersion, OddsSnapshot, Prediction, UserPrediction, WinSlip
from app.db.session import get_db
from app.services.community import community_leaderboard, community_overview, fixture_consensus, prediction_social_context, experts_list, win_wall, daily_challenge, follow_user, user_profile
from app.services.market_metrics import roi_clv_summary, yield_by_tier
from app.services.predictions import build_combo, compound_combo_probability, generate_today_predictions


router = APIRouter()


def _apply_league_filter(query, league: str):
    """Support exact league values plus friendly searches like worldcup.

    The frontend league dropdown sends exact values such as ``FIFA World Cup``,
    but users also type/share URLs with compact names like ``worldcup``. Match
    both the normal text and a compact no-space/no-hyphen version.
    """

    raw = league.strip()
    compact = raw.lower().replace(" ", "").replace("-", "")
    compact_league = func.replace(func.replace(func.lower(Fixture.league), " ", ""), "-", "")
    return query.filter(
        or_(
            Fixture.league == raw,
            Fixture.league.ilike(f"%{raw}%"),
            compact_league.ilike(f"%{compact}%"),
        )
    )


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
    if market in {"goals", "over/under 2.5", "over/under 1.5", "over/under 3.5"}:
        total = home_score + away_score
        parts = pick.replace("goals", "").strip().split()
        try:
            threshold = float(parts[-1]) if len(parts) > 1 and parts[-1].replace(".", "").isdigit() else 0.0
            if threshold == 0:
                threshold = float(parts[0]) if parts[0].replace(".", "").isdigit() else 2.5
        except (ValueError, IndexError):
            threshold = 2.5
        if "over" in pick:
            return total > threshold
        if "under" in pick:
            return total < threshold
    if market in {"btts", "both teams to score"}:
        both_scored = home_score > 0 and away_score > 0
        if "yes" in pick:
            return both_scored
        if "no" in pick:
            return not both_scored
    if market == "correct score":
        return pick == f"{home_score}-{away_score}"
    if market in {"spread", "point spread", "run line"}:
        parts = pick.replace("home", "").replace("away", "").strip().split()
        try:
            spread = float(parts[-1]) if parts else 0.0
        except (ValueError, IndexError):
            return None
        if pick.startswith("home"):
            return (home_score - away_score) > -spread
        if pick.startswith("away"):
            return (away_score - home_score) > spread
    if market in {"total points", "total runs", "total games"}:
        total = home_score + away_score
        if pick.startswith("over"):
            parts = pick.replace("over", "").strip().split()
            try:
                threshold = float(parts[0]) if parts else 2.5
            except (ValueError, IndexError):
                threshold = 2.5
            return total > threshold
        if pick.startswith("under"):
            parts = pick.replace("under", "").strip().split()
            try:
                threshold = float(parts[0]) if parts else 2.5
            except (ValueError, IndexError):
                threshold = 2.5
            return total < threshold
        if "high" in pick:
            return total >= 2.5
        if "low" in pick:
            return total < 2.5
    if market == "double chance":
        if "home" in pick and "draw" in pick:
            return home_score >= away_score
        if "away" in pick and "draw" in pick:
            return away_score >= home_score
        if "home" in pick and "away" in pick:
            return home_score != away_score

    return None


def serialize_prediction(p: Prediction, f: Fixture) -> dict:
    result = prediction_result(p, f)
    engine_meta = p.engine_meta or {}
    value_info = {}
    if "value_bets" in engine_meta:
        pick_key = f"{p.market}_{p.pick}".replace(" ", "_")
        if pick_key in engine_meta["value_bets"]:
            value_info = engine_meta["value_bets"][pick_key]
    return {
        "id": p.id, "fixture_id": f.id, "sport": f.sport, "league": f.league,
        "match_date": f.match_date, "home_team": f.home_team, "away_team": f.away_team,
        "market": p.market, "pick": p.pick, "confidence": p.confidence,
        "edge_score": p.edge_score, "risk_level": p.risk_level, "reasoning": p.reasoning,
        "analysis": engine_meta, "value_betting": value_info, "is_premium": p.is_premium,
        "version": p.version, "status": p.status, "published_at": p.published_at,
        "result": "pending" if result is None else "won" if result else "lost",
    }


def _prediction_query(db: Session, sport: str | None, league: str | None, market: str | None, risk: str | None, min_confidence: float):
    query = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(
        Prediction.is_published == True,
        Prediction.status == "active",
        func.date(Fixture.match_date) >= func.current_date(),
        Prediction.confidence >= min_confidence,
    )
    if sport:
        query = query.filter(Fixture.sport == sport)
    if league:
        query = _apply_league_filter(query, league)
    if market:
        query = query.filter(Prediction.market == market)
    if risk:
        query = query.filter(Prediction.risk_level == risk)
    return query


@router.get("/predictions/today")
def today(sport: str | None = None, league: str | None = None, market: str | None = None, risk: str | None = None, min_confidence: float = 0, db: Session = Depends(get_db)):
    query = _prediction_query(db, sport, league, market, risk, min_confidence)
    rows = query.order_by(Prediction.confidence.desc()).limit(100).all()
    if not rows:
        # Never make a user request execute the expensive ML build. The external
        # cron/scheduler is responsible for filling the board; this endpoint only
        # queues a recovery build when fixtures exist and immediately returns the
        # currently available board (possibly empty).
        upcoming = db.query(Fixture.id).filter(func.date(Fixture.match_date) >= func.current_date()).first()
        if upcoming:
            try:
                from app.services.prediction_runner import start_prediction_generation
                start_prediction_generation(reason="public-empty-board")
            except Exception:
                log.exception("Could not queue public self-heal prediction generation")
    return [serialize_prediction(p, f) for p, f in rows]


@router.get("/predictions/history")
def prediction_history(sport: str | None = None, days: int = 7, limit: int = 50, db: Session = Depends(get_db)):
    cutoff = date.today() - timedelta(days=days)
    query = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(
        Prediction.is_published == True, Fixture.match_date >= cutoff,
    )
    if sport:
        query = query.filter(Fixture.sport == sport)
    rows = query.order_by(Fixture.match_date.desc(), Prediction.confidence.desc()).limit(min(limit, 200)).all()
    return [serialize_prediction(p, f) for p, f in rows]


@router.get("/predictions/combo")
def combo_endpoint(legs: int = 3, min_confidence: float = 60, db: Session = Depends(get_db)):
    rows = build_combo(db, legs, min_confidence)
    out = [serialize_prediction(p, f) for p, f in rows]
    true_probability = compound_combo_probability([p for p, _ in rows])
    avg_edge = round(sum(x["edge_score"] for x in out) / len(out), 1) if out else 0
    return {"label": "LOYAL EDGE 3-Leg Combo", "combined_confidence": true_probability, "avg_edge_score": avg_edge, "risk_level": "High" if true_probability < 45 else "Medium" if true_probability < 65 else "Low", "legs": out}


@router.get("/predictions/{prediction_id}")
def prediction_detail(prediction_id: int, db: Session = Depends(get_db)):
    row = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(Prediction.id == prediction_id, Prediction.is_published == True).first()
    if not row:
        raise HTTPException(status_code=404, detail="Prediction not found")
    p, f = row
    snapshots = db.query(OddsSnapshot).filter(OddsSnapshot.prediction_id == p.id).order_by(OddsSnapshot.captured_at.desc()).all()
    return {
        **serialize_prediction(p, f),
        "model_version_id": p.model_version_id,
        "engine_summary": "Model output is calibrated where available and filtered by market-specific publish thresholds.",
        "odds_snapshots": [{"phase": o.phase, "market": o.market, "home_odds": o.home_odds, "draw_odds": o.draw_odds, "away_odds": o.away_odds, "bookmaker": o.bookmaker, "captured_at": o.captured_at} for o in snapshots],
        "responsible_note": "Predictions are probabilistic, not guaranteed. Use responsible staking.",
        "community": fixture_consensus(db, f.id),
        "social": prediction_social_context(db, p.id),
    }


@router.get("/community/predictions/{prediction_id}/social")
def prediction_social(prediction_id: int, db: Session = Depends(get_db)):
    return prediction_social_context(db, prediction_id)
