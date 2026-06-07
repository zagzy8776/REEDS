from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import BacktestRun, Fixture, OddsSnapshot, Team, TeamAlias
from app.db.session import get_db
from app.ml.backtest import walk_forward_backtest
from app.ml.train import train_basketball_model, train_soccer_model
from app.scraper.loaders import ingest_api_basketball_games, ingest_api_football_fixtures
from app.services.data_quality import upsert_team_alias
from app.services.model_registry import register_model
from app.services.predictions import dataframe_from_db, generate_today_predictions
from app.services.community import settle_user_predictions


router = APIRouter()


def require_admin(x_admin_key: str = Header(default="")):
    settings = get_settings()
    if settings.app_env == "production" and settings.admin_api_key in {"", "change-me"}:
        raise HTTPException(status_code=500, detail="Admin API key is not safely configured")
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")


def _date_window(days: int) -> list[str]:
    return [(date.today() + timedelta(days=offset)).isoformat() for offset in range(max(days, 1))]


@router.get("/feed-config", dependencies=[Depends(require_admin)])
def feed_config():
    settings = get_settings()
    return {
        "database_configured": bool(settings.database_url),
        "api_football_key_configured": bool(settings.api_football_key),
        "api_sports_key_configured": bool(settings.api_sports_key),
        "api_basketball_key_configured": bool(settings.api_basketball_key),
        "the_odds_api_key_configured": bool(settings.the_odds_api_key),
        "scheduler_enabled": settings.enable_scheduler,
        "live_ingest_days": settings.live_ingest_days,
        "odds_sport_keys": settings.odds_api_sport_keys,
        "note": "This endpoint intentionally shows only presence/booleans, never secret values.",
    }


@router.post("/ingest-live", dependencies=[Depends(require_admin)])
def ingest_live(days: int | None = None, sport: str = "all", skip_odds: bool = False, db: Session = Depends(get_db)):
    settings = get_settings()
    dates = _date_window(days or settings.live_ingest_days)
    football_key = settings.api_football_key or settings.api_sports_key
    basketball_key = settings.api_basketball_key or settings.api_sports_key
    result: dict = {
        "dates": dates,
        "requested_sport": sport,
        "env": {
            "api_football_or_sports_configured": bool(football_key),
            "api_basketball_or_sports_configured": bool(basketball_key),
            "the_odds_api_configured": bool(settings.the_odds_api_key),
        },
        "ingested": {"soccer": 0, "basketball": 0},
        "skipped": [],
    }
    if sport not in {"all", "soccer", "basketball"}:
        raise HTTPException(status_code=400, detail="sport must be all, soccer, or basketball")
    if sport in {"all", "soccer"}:
        if football_key:
            try:
                result["ingested"]["soccer"] = ingest_api_football_fixtures(
                    db,
                    football_key,
                    dates,
                    include_odds=not skip_odds,
                    the_odds_api_key=settings.the_odds_api_key,
                    the_odds_api_sport_keys=settings.odds_api_sport_keys,
                )
            except Exception as exc:  # noqa: BLE001
                result["skipped"].append({"sport": "soccer", "reason": str(exc)})
        else:
            result["skipped"].append({"sport": "soccer", "reason": "API_FOOTBALL_KEY or API_SPORTS_KEY not configured"})
    if sport in {"all", "basketball"}:
        if basketball_key:
            try:
                result["ingested"]["basketball"] = ingest_api_basketball_games(db, basketball_key, dates)
            except Exception as exc:  # noqa: BLE001
                result["skipped"].append({"sport": "basketball", "reason": str(exc)})
        else:
            result["skipped"].append({"sport": "basketball", "reason": "API_BASKETBALL_KEY or API_SPORTS_KEY not configured"})
    result["fixture_count"] = db.query(Fixture).count()
    return result


@router.post("/train", dependencies=[Depends(require_admin)])
def train(db: Session = Depends(get_db)):
    data = dataframe_from_db(db)
    trained, skipped = [], []
    for sport, trainer in (("soccer", train_soccer_model), ("basketball", train_basketball_model)):
        try:
            result = trainer(data)
            mv = register_model(db, sport, result["model_type"], result["path"], result["accuracy"], result["sample_size"])
            trained.append({"sport": sport, **result, "active": mv.is_active})
        except ValueError as exc:
            skipped.append({"sport": sport, "reason": str(exc)})
    return {"status": "trained", "trained": trained, "skipped": skipped}


@router.post("/predict", dependencies=[Depends(require_admin)])
def predict(db: Session = Depends(get_db)):
    return {"generated": generate_today_predictions(db)}


@router.post("/backtest", dependencies=[Depends(require_admin)])
def backtest(db: Session = Depends(get_db)):
    data = dataframe_from_db(db)
    completed, skipped = [], []
    for sport in ("soccer", "basketball"):
        try:
            result = walk_forward_backtest(data[data.get("sport", sport) == sport].copy(), sport)
            run = BacktestRun(
                sport=sport,
                model_type=result["model_type"],
                split_strategy=result["split_strategy"],
                sample_size=result["sample_size"],
                accuracy=result["accuracy"],
                brier_score=result["brier_score"],
                log_loss=result["log_loss"],
                metrics=result["metrics"],
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            completed.append({"id": run.id, **result})
        except ValueError as exc:
            skipped.append({"sport": sport, "reason": str(exc)})
    return {"status": "backtested", "completed": completed, "skipped": skipped}


@router.post("/community/settle", dependencies=[Depends(require_admin)])
def settle_community_predictions(db: Session = Depends(get_db)):
    return settle_user_predictions(db)


@router.post("/team-aliases", dependencies=[Depends(require_admin)])
def create_team_alias(payload: dict, db: Session = Depends(get_db)):
    sport = payload.get("sport", "soccer")
    canonical_name = payload.get("canonical_name")
    alias = payload.get("alias")
    if not canonical_name or not alias:
        raise HTTPException(status_code=400, detail="canonical_name and alias are required")
    return upsert_team_alias(db, sport, canonical_name, alias, source="admin")


@router.get("/team-aliases", dependencies=[Depends(require_admin)])
def list_team_aliases(sport: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    query = db.query(TeamAlias, Team).join(Team, TeamAlias.team_id == Team.id)
    if sport:
        query = query.filter(TeamAlias.sport == sport)
    rows = query.order_by(TeamAlias.created_at.desc()).limit(min(limit, 500)).all()
    return [{"id": a.id, "sport": a.sport, "alias": a.alias, "canonical_name": t.canonical_name, "source": a.source} for a, t in rows]


@router.get("/odds-snapshots", dependencies=[Depends(require_admin)])
def list_odds_snapshots(fixture_id: int | None = None, limit: int = 200, db: Session = Depends(get_db)):
    query = db.query(OddsSnapshot)
    if fixture_id:
        query = query.filter(OddsSnapshot.fixture_id == fixture_id)
    rows = query.order_by(OddsSnapshot.captured_at.desc()).limit(min(limit, 500)).all()
    return [{"id": o.id, "fixture_id": o.fixture_id, "prediction_id": o.prediction_id, "phase": o.phase, "market": o.market, "home_odds": o.home_odds, "draw_odds": o.draw_odds, "away_odds": o.away_odds, "source": o.source, "captured_at": o.captured_at} for o in rows]


@router.post("/odds-snapshots", dependencies=[Depends(require_admin)])
def create_odds_snapshot(payload: dict, db: Session = Depends(get_db)):
    fixture_id = payload.get("fixture_id")
    phase = payload.get("phase", "closing")
    market = payload.get("market", "1X2")
    if not fixture_id:
        raise HTTPException(status_code=400, detail="fixture_id is required")
    fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    snapshot = OddsSnapshot(
        fixture_id=fixture_id,
        prediction_id=payload.get("prediction_id"),
        phase=phase,
        market=market,
        bookmaker=payload.get("bookmaker"),
        home_odds=payload.get("home_odds"),
        draw_odds=payload.get("draw_odds"),
        away_odds=payload.get("away_odds"),
        line=payload.get("line"),
        over_odds=payload.get("over_odds"),
        under_odds=payload.get("under_odds"),
        source=payload.get("source", "admin"),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return {"id": snapshot.id, "fixture_id": snapshot.fixture_id, "prediction_id": snapshot.prediction_id, "phase": snapshot.phase, "market": snapshot.market}
