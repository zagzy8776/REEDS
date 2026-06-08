from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.models import BacktestRun, Fixture, ModelVersion, OddsSnapshot, Prediction, UserPrediction
from app.db.session import get_db
from app.services.community import community_leaderboard, fixture_consensus
from app.services.market_metrics import roi_clv_summary
from app.services.predictions import build_combo, compound_combo_probability


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
        "version": p.version,
        "status": p.status,
        "published_at": p.published_at,
        "result": "pending" if result is None else "won" if result else "lost",
    }


@router.get("/predictions/today")
def today(league: str | None = None, market: str | None = None, risk: str | None = None, min_confidence: float = 0, db: Session = Depends(get_db)):
    query = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(Prediction.is_published == True, Prediction.status == "active", Fixture.match_date >= date.today(), Prediction.confidence >= min_confidence)
    if league:
        query = query.filter(Fixture.league == league)
    if market:
        query = query.filter(Prediction.market == market)
    if risk:
        query = query.filter(Prediction.risk_level == risk)
    rows = query.order_by(Prediction.confidence.desc()).limit(100).all()
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
    }


@router.get("/fixtures/upcoming")
def upcoming_fixtures(scope: str = "upcoming", sport: str | None = None, league: str | None = None, limit: int = 300, db: Session = Depends(get_db)):
    query = db.query(Fixture).filter(Fixture.sport.in_(["soccer", "basketball"]))
    normalized_scope = scope.lower().strip()
    if normalized_scope == "upcoming":
        query = query.filter(Fixture.match_date >= date.today())
    elif normalized_scope in {"live", "today"}:
        query = query.filter(Fixture.match_date == date.today())
    elif normalized_scope in {"results", "old", "past"}:
        query = query.filter(Fixture.match_date < date.today())
    elif normalized_scope == "all":
        pass
    else:
        raise HTTPException(status_code=400, detail="scope must be upcoming, live, results, or all")
    if sport:
        query = query.filter(Fixture.sport == sport)
    if league:
        query = query.filter(Fixture.league == league)
    order_date = Fixture.match_date.desc() if normalized_scope in {"results", "old", "past"} else Fixture.match_date.asc()
    rows = query.order_by(order_date, Fixture.league.asc()).limit(min(limit, 500)).all()
    return [
        {
            "id": f.id,
            "sport": f.sport,
            "league": f.league,
            "season": f.season,
            "match_date": f.match_date,
            "home_team": f.home_team,
            "away_team": f.away_team,
            "home_score": f.home_score,
            "away_score": f.away_score,
            "total_goals": (f.home_score + f.away_score) if f.home_score is not None and f.away_score is not None else None,
            "result_label": "pending" if f.home_score is None or f.away_score is None else "home_win" if f.home_score > f.away_score else "away_win" if f.away_score > f.home_score else "draw",
            "home_odds": f.home_odds,
            "draw_odds": f.draw_odds,
            "away_odds": f.away_odds,
            "source": f.source,
            "has_odds": any([f.home_odds, f.draw_odds, f.away_odds]),
            "api_status": (f.extra or {}).get("status") if isinstance(f.extra, dict) else None,
            "odds_source": (f.extra or {}).get("odds_source") if isinstance(f.extra, dict) else None,
        }
        for f in rows
    ]


@router.get("/fixtures/status")
def fixtures_status(db: Session = Depends(get_db)):
    total = db.query(Fixture).count()
    upcoming = db.query(Fixture).filter(Fixture.match_date >= date.today()).count()
    today = db.query(Fixture).filter(Fixture.match_date == date.today()).count()
    results = db.query(Fixture).filter(Fixture.match_date < date.today()).count()
    with_scores = db.query(Fixture).filter(Fixture.home_score != None, Fixture.away_score != None).count()
    with_odds = db.query(Fixture).filter((Fixture.home_odds != None) | (Fixture.draw_odds != None) | (Fixture.away_odds != None)).count()
    latest = db.query(Fixture).order_by(Fixture.match_date.desc()).first()
    api_rows = db.query(Fixture).filter(Fixture.source.in_(["api_football", "api_basketball"])).count()
    sample_rows = db.query(Fixture).filter(Fixture.source == "sample").count()
    return {
        "checked_at": datetime.utcnow(),
        "total": total,
        "upcoming": upcoming,
        "today": today,
        "results": results,
        "with_scores": with_scores,
        "with_odds": with_odds,
        "api_rows": api_rows,
        "sample_rows": sample_rows,
        "latest_match_date": latest.match_date if latest else None,
        "feed_health": "empty" if total == 0 else "needs_live_api" if api_rows == 0 else "active",
        "public_note": "If this says empty or needs_live_api, add API keys on Render and run live ingestion/scheduler. No secret values are exposed here.",
    }


@router.post("/community/predictions")
async def submit_user_prediction(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
    else:
        form = await request.form()
        payload = dict(form)
    fixture_id = payload.get("fixture_id")
    username = str(payload.get("username", "")).strip()[:80]
    market = str(payload.get("market", "")).strip()[:50]
    pick = str(payload.get("pick", "")).strip()[:120]
    analysis = str(payload.get("analysis_text", "")).strip()[:1000]
    if not fixture_id or not username or not market or not pick:
        raise HTTPException(status_code=400, detail="fixture_id, username, market, and pick are required")
    fixture = db.query(Fixture).filter(Fixture.id == int(fixture_id), Fixture.match_date >= date.today()).first()
    if not fixture:
        raise HTTPException(status_code=404, detail="Upcoming fixture not found")
    row = UserPrediction(fixture_id=fixture.id, username=username, market=market, pick=pick, analysis_text=analysis or None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "status": "submitted"}


@router.get("/community/fixtures/{fixture_id}")
def community_for_fixture(fixture_id: int, db: Session = Depends(get_db)):
    return fixture_consensus(db, fixture_id)


@router.get("/community/leaderboard")
def leaderboard(limit: int = 50, db: Session = Depends(get_db)):
    return community_leaderboard(db, min(limit, 100))


@router.get("/stats/backtest")
def stats(db: Session = Depends(get_db)):
    models = db.query(ModelVersion).order_by(ModelVersion.trained_at.desc()).limit(10).all()
    backtests = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(10).all()
    rows = db.query(Prediction, Fixture).join(Fixture, Prediction.fixture_id == Fixture.id).filter(Prediction.is_published == True, Fixture.home_score != None, Fixture.away_score != None).all()
    odds_snapshot_count = db.query(OddsSnapshot).count()
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
        "backtests": [{"sport": b.sport, "type": b.model_type, "strategy": b.split_strategy, "accuracy": b.accuracy, "brier_score": b.brier_score, "log_loss": b.log_loss, "sample_size": b.sample_size, "created_at": b.created_at, "metrics": b.metrics} for b in backtests],
        "data_quality": {"odds_snapshots": odds_snapshot_count, "note": "ROI and CLV become meaningful after published and closing odds are captured consistently."},
        "market_proof": roi_clv_summary(db),
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
