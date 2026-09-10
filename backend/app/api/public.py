import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from app.db.models import BacktestRun, CommunityComment, CommunityPlay, CommunityReaction, Fixture, HistoricalEvaluation, ModelVersion, OddsSnapshot, Prediction, UserPrediction, WinSlip
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


_PICK_SIDE_KEYS = {
    "1x2": ("home_win", "draw", "away_win"),
    "moneyline": ("home_win", "draw", "away_win"),
}


def _market_metrics(p: Prediction, f: Fixture) -> dict:
    """Market implied probability + edge vs REEDS model probability.

    Only computed for 1X2/Moneyline picks where a decimal price for the picked
    side actually exists on the fixture. Never invented for other markets.
    """
    market = str(p.market or "").lower().strip()
    pick = str(p.pick or "").lower().strip()
    odds_map = {
        "home": f.home_odds,
        "away": f.away_odds,
        "draw": f.draw_odds,
    }
    if market not in _PICK_SIDE_KEYS:
        return {"available": False}
    side = "home" if "home" in pick else "away" if "away" in pick else "draw" if "draw" in pick else None
    odds = odds_map.get(side) if side else None
    if not side or not odds or odds <= 1.0:
        return {"available": False}
    engine_meta = p.engine_meta if isinstance(p.engine_meta, dict) else {}
    probabilities = engine_meta.get("probabilities") if isinstance(engine_meta.get("probabilities"), dict) else {}
    prob_keys = _PICK_SIDE_KEYS[market]
    key = {"home": prob_keys[0], "draw": prob_keys[1], "away": prob_keys[2]}[side]
    model_prob = probabilities.get(key)
    if model_prob is None:
        return {"available": False}
    market_prob = 1.0 / odds
    try:
        model_prob = float(model_prob)
        edge_pp = round(model_prob * 100.0 - market_prob * 100.0, 2)
    except (TypeError, ValueError):
        return {"available": False}
    return {
        "available": True,
        "side": side,
        "odds": round(float(odds), 2),
        "market_probability": round(market_prob * 100.0, 1),
        "model_probability": round(model_prob * 100.0, 1),
        "edge_pp": edge_pp,
    }


def _verdict(p: Prediction, metrics: dict) -> dict:
    """REEDS VERDICT label derived strictly from real publication state.

    PUBLISHED + strong confidence/edge  -> STRONG READ
    PUBLISHED (passed the empirical gate) -> REEDS VALUE
    Not published with a market-block/tuned reasons -> ANALYZED — LOW EVIDENCE
    Not published and no usable model output yet -> WATCHLIST — INSUFFICIENT DATA
    """
    engine_meta = p.engine_meta if isinstance(p.engine_meta, dict) else {}
    quality = engine_meta.get("publication_quality") if isinstance(engine_meta.get("publication_quality"), dict) else {}
    reasons = list(quality.get("reasons") or [])
    if p.is_published:
        if float(p.confidence or 0) >= 72.0 and float(p.edge_score or 0) >= 8.0:
            return {"key": "strong_read", "label": "STRONG READ", "detail": "Published read with high confidence and strong model edge.", "level": "published"}
        return {"key": "reeds_value", "label": "REEDS VALUE", "detail": "Published read that passed the empirical market evidence gate.", "level": "published"}
    if reasons:
        return {"key": "analyzed_low_evidence", "label": "ANALYZED — LOW EVIDENCE", "detail": "The model generated a read, but REEDS does not currently have enough validated evidence to classify it as a strong recommendation. " + " ".join(reasons), "level": "analyzed"}
    if engine_meta:
        return {"key": "analyzed_low_evidence", "label": "ANALYZED — LOW EVIDENCE", "detail": "The model generated a read, but publication evidence is not yet established.", "level": "analyzed"}
    return {"key": "watchlist_insufficient", "label": "WATCHLIST — INSUFFICIENT DATA", "detail": "REEDS does not have enough information to form a published read for this match yet.", "level": "analyzed"}


def records_map(db: Session, combos: set[tuple[str, str]]) -> dict[str, dict]:
    """Batched transparency blocks for a set of ``(sport, market)`` combos.

    Built once per request (never per card) to protect the database plan:
      - LIVE RECORD: published, settled reading in the same sport+market.
      - BACKTEST: latest stored walk-forward backtest for the sport.
      - HISTORICAL EVALUATION: walk-forward out-of-sample evaluation rows.
    """
    if not combos:
        return {}
    key_for = lambda sport, market: f"{sport}::{market}"
    out = {key_for(s, m): {"live_record": None, "backtest": None, "historical_evaluation": None} for s, m in combos}
    if not out:
        return out

    cutoff = date.today() - timedelta(days=730)
    sports = sorted({s for s, _ in combos})
    rows = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(
            Prediction.is_published == True,
            Fixture.sport.in_(sports),
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
            Fixture.match_date >= cutoff,
        )
        .order_by(Fixture.match_date.desc())
        .limit(1500)
        .all()
    )
    live: dict[str, dict[str, int]] = {}
    for p, f in rows:
        key = key_for(f.sport, p.market)
        if key not in out:
            continue
        res = prediction_result(p, f)
        bucket = live.setdefault(key, {"wins": 0, "losses": 0})
        if res is True:
            bucket["wins"] += 1
        elif res is False:
            bucket["losses"] += 1

    for key, bucket in live.items():
        n = bucket["wins"] + bucket["losses"]
        if n == 0:
            continue
        out[key]["live_record"] = {
            "label": "LIVE RECORD",
            "sample": n,
            "wins": bucket["wins"],
            "losses": bucket["losses"],
            "accuracy": round(bucket["wins"] / n * 100.0, 1),
            "note": "Published reads only, settled in real time. Pending picks do not count.",
        }

    backtests = (
        db.query(BacktestRun)
        .filter(BacktestRun.sport.in_(sports))
        .order_by(BacktestRun.created_at.desc())
        .all()
    )
    by_sport_bt: dict[str, BacktestRun] = {}
    for bt in backtests:
        by_sport_bt.setdefault(bt.sport, bt)

    hist_rows = (
        db.query(HistoricalEvaluation.sport, HistoricalEvaluation.outcome, func.count())
        .filter(HistoricalEvaluation.sport.in_(sports))
        .group_by(HistoricalEvaluation.sport, HistoricalEvaluation.outcome)
        .all()
    )
    by_sport_hist: dict[str, dict[str, int]] = {}
    for sport, outcome, count in hist_rows:
        bucket = by_sport_hist.setdefault(sport, {"wins": 0, "total": 0})
        bucket["total"] += count
        if outcome == "won":
            bucket["wins"] += count

    for (sport, market) in combos:
        key = key_for(sport, market)
        bt = by_sport_bt.get(sport)
        if bt and (bt.sample_size or 0) > 0:
            out[key]["backtest"] = {
                "label": "BACKTEST",
                "model_type": bt.model_type,
                "split": bt.split_strategy,
                "sample_size": bt.sample_size,
                "accuracy": round((bt.accuracy or 0.0) * 100.0, 1),
                "note": "Walk-forward out-of-sample, estimated on data prior to going live. Not a record of live picks.",
            }
        hist = by_sport_hist.get(sport)
        if hist and hist["total"] > 0:
            out[key]["historical_evaluation"] = {
                "label": "HISTORICAL EVALUATION",
                "sample": hist["total"],
                "wins": hist["wins"],
                "accuracy": round(hist["wins"] / hist["total"] * 100.0, 1),
                "note": "Walk-forward out-of-sample evaluation rows. Does not add to the live tracked record.",
            }
    return out


def serialize_prediction(p: Prediction, f: Fixture, records: dict | None = None) -> dict:
    result = prediction_result(p, f)
    engine_meta = p.engine_meta or {}
    value_info = {}
    if "value_bets" in engine_meta:
        pick_key = f"{p.market}_{p.pick}".replace(" ", "_")
        if pick_key in engine_meta["value_bets"]:
            value_info = engine_meta["value_bets"][pick_key]
    metrics = _market_metrics(p, f)
    outcome_meta = engine_meta.get("outcome") if isinstance(engine_meta.get("outcome"), dict) else {}
    final_score = outcome_meta.get("final_score")
    if not final_score and f.home_score is not None and f.away_score is not None:
        final_score = f"{f.home_score}-{f.away_score}"
    return {
        "id": p.id, "fixture_id": f.id, "sport": f.sport, "league": f.league,
        "match_date": f.match_date, "home_team": f.home_team, "away_team": f.away_team,
        "market": p.market, "pick": p.pick, "confidence": p.confidence,
        "edge_score": p.edge_score, "risk_level": p.risk_level, "reasoning": p.reasoning,
        "analysis": engine_meta, "value_betting": value_info, "is_premium": p.is_premium,
        "version": p.version, "status": p.status, "published_at": p.published_at,
        "is_published": p.is_published,
        "result": "pending" if result is None else "won" if result else "lost",
        "final_score": final_score,
        "verdict": _verdict(p, metrics),
        "market_metrics": metrics,
        "records": records,
        "responsible_note": "Predictions are probabilistic, not guaranteed.",
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
    combos = {(f.sport, p.market) for p, f in rows}
    records = records_map(db, combos)
    return [serialize_prediction(p, f, records.get(f"{f.sport}::{p.market}")) for p, f in rows]


@router.get("/predictions/history")
def prediction_history(
    sport: str | None = None,
    league: str | None = None,
    market: str | None = None,
    risk: str | None = None,
    result: str | None = None,
    q: str | None = None,
    days: int = 7,
    limit: int = 50,
    page: int = 1,
    paginated: bool = False,
    db: Session = Depends(get_db),
):
    """Public prediction history with optional filters and pagination.

    ``paginated=true`` returns ``{items, total, page, page_size, results}``;
    otherwise the legacy array shape is preserved for existing callers.
    """
    cutoff = date.today() - timedelta(days=max(1, days))
    query = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(
        Prediction.is_published == True, Fixture.match_date >= cutoff,
    )
    if sport:
        query = query.filter(Fixture.sport == sport)
    if league:
        query = _apply_league_filter(query, league)
    if market:
        query = query.filter(Prediction.market == market)
    if risk:
        query = query.filter(Prediction.risk_level == risk)
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(Fixture.home_team.ilike(term), Fixture.away_team.ilike(term)))
    rows = query.order_by(Fixture.match_date.desc(), Prediction.created_at.desc(), Prediction.confidence.desc()).limit(min(limit * 20 + 50, 4000)).all()

    if result in {"won", "lost", "pending"}:
        rows = [r for r in rows if serialize_prediction(r[0], r[1])["result"] == result]

    total = len(rows)
    combos = {(f.sport, p.market) for p, f in rows}
    records = records_map(db, combos)
    if paginated:
        page = max(1, int(page))
        page_size = min(max(1, int(limit)), 200)
        start = (page - 1) * page_size
        rows_page = rows[start:start + page_size]
        items = [serialize_prediction(p, f, records.get(f"{f.sport}::{p.market}")) for p, f in rows_page]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": start + page_size < total,
            "filtered_total": total,
        }
    rows = rows[: min(limit, 200)]
    return [serialize_prediction(p, f, records.get(f"{f.sport}::{p.market}")) for p, f in rows]


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
    records = records_map(db, {(f.sport, p.market)}).get(f"{f.sport}::{p.market}")
    return {
        **serialize_prediction(p, f, records),
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
