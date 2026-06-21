from datetime import date, timedelta
import threading

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import BacktestRun, Fixture, OddsSnapshot, Team, TeamAlias
from app.db.session import get_db
from app.ml.backtest import walk_forward_backtest
from app.ml.train import train_basketball_model, train_soccer_model
from app.scraper.api_clients import ApiFootballClient
from app.scraper.loaders import ingest_allsportsapi_events, ingest_api_basketball_games, ingest_api_football_fixtures, ingest_apifootball_com_events, ingest_football_data_org_matches, ingest_sportmonks_football_fixtures, ingest_thesportsdb_events, load_football_csv, load_basketball_csv, sync_live_scores, refresh_odds_from_the_odds_api
from app.services.data_quality import upsert_team_alias
from app.services.model_registry import register_model
from app.services.predictions import dataframe_from_db, generate_today_predictions
from app.services.community import settle_user_predictions
from app.services.coverage_seed import ensure_multisport_showcase


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
        "api_football_com_key_configured": bool(settings.api_football_com_key),
        "api_sports_key_configured": bool(settings.api_sports_key),
        "sportmonks_api_key_configured": bool(settings.sportmonks_api_key),
        "football_data_api_key_configured": bool(settings.football_data_api_key),
        "api_basketball_key_configured": bool(settings.api_basketball_key),
        "allsportsapi_key_configured": bool(settings.allsportsapi_key),
        "allsportsapi_sports": settings.allsportsapi_sport_list,
        "thesportsdb_enabled": settings.thesportsdb_enabled,
        "thesportsdb_sports": settings.thesportsdb_sport_list,
        "thesportsdb_max_calls": settings.thesportsdb_max_calls,
        "the_odds_api_key_configured": bool(settings.the_odds_api_key),
        "scheduler_enabled": settings.enable_scheduler,
        "live_ingest_days": settings.live_ingest_days,
        "odds_sport_keys": settings.odds_api_sport_keys,
        "note": "This endpoint intentionally shows only presence/booleans, never secret values.",
    }


@router.get("/diagnostics/api-football", dependencies=[Depends(require_admin)])
def api_football_diagnostics(date_: str | None = None):
    """Safely test API-Football without exposing the configured API key."""

    settings = get_settings()
    api_key = settings.api_football_key or settings.api_sports_key
    target_date = date_ or date.today().isoformat()
    result = {
        "provider": "api_sports_football",
        "date": target_date,
        "key_configured": bool(api_key),
        "base_url": "https://v3.football.api-sports.io",
        "response_count": 0,
        "errors": None,
        "message": None,
        "sample": [],
    }
    if not api_key:
        result["message"] = "API_FOOTBALL_KEY or API_SPORTS_KEY is not configured"
        return result
    try:
        payload = ApiFootballClient(api_key).fixtures_by_date(target_date)
    except Exception as exc:  # noqa: BLE001
        result["message"] = str(exc)
        return result
    rows = payload.get("response", []) if isinstance(payload, dict) else []
    result["response_count"] = len(rows)
    if isinstance(payload, dict):
        result["errors"] = payload.get("errors") or None
        result["message"] = payload.get("message") or payload.get("note")
    for item in rows[:5]:
        fixture = item.get("fixture", {}) if isinstance(item, dict) else {}
        league = item.get("league", {}) if isinstance(item, dict) else {}
        teams = item.get("teams", {}) if isinstance(item, dict) else {}
        result["sample"].append({
            "fixture_id": fixture.get("id"),
            "date": fixture.get("date"),
            "league": league.get("name"),
            "country": league.get("country"),
            "home": (teams.get("home") or {}).get("name"),
            "away": (teams.get("away") or {}).get("name"),
            "status": fixture.get("status"),
        })
    return result


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
            "api_football_com_configured": bool(settings.api_football_com_key),
            "sportmonks_configured": bool(settings.sportmonks_api_key),
            "football_data_org_configured": bool(settings.football_data_api_key),
            "api_basketball_or_sports_configured": bool(basketball_key),
            "allsportsapi_configured": bool(settings.allsportsapi_key),
            "thesportsdb_enabled": settings.thesportsdb_enabled,
            "the_odds_api_configured": bool(settings.the_odds_api_key),
        },
        "ingested": {"soccer": 0, "api_sports_football": 0, "apifootball_com": 0, "sportmonks": 0, "football_data_org": 0, "basketball": 0, "allsportsapi": 0, "thesportsdb": 0},
        "skipped": [],
    }
    allowed_sports = {"all", "soccer", "basketball", "multisport", "cricket", "tennis", "american_football", "baseball", "hockey", "rugby", "volleyball", "handball", "mma", "motorsport"}
    if sport not in allowed_sports:
        raise HTTPException(status_code=400, detail=f"sport must be one of: {', '.join(sorted(allowed_sports))}")
    if sport in {"all", "soccer"}:
        if football_key:
            try:
                result["ingested"]["api_sports_football"] = ingest_api_football_fixtures(
                    db,
                    football_key,
                    dates,
                    include_odds=not skip_odds,
                    the_odds_api_key=settings.the_odds_api_key,
                    the_odds_api_sport_keys=settings.odds_api_sport_keys,
                )
                result["ingested"]["soccer"] += result["ingested"]["api_sports_football"]
            except Exception as exc:  # noqa: BLE001
                result["skipped"].append({"provider": "api_sports_football", "sport": "soccer", "reason": str(exc)})
        else:
            result["skipped"].append({"provider": "api_sports_football", "sport": "soccer", "reason": "API_FOOTBALL_KEY or API_SPORTS_KEY not configured"})
        if settings.api_football_com_key:
            try:
                result["ingested"]["apifootball_com"] = ingest_apifootball_com_events(db, settings.api_football_com_key, dates)
                result["ingested"]["soccer"] += result["ingested"]["apifootball_com"]
            except Exception as exc:  # noqa: BLE001
                result["skipped"].append({"provider": "apifootball_com", "sport": "soccer", "reason": str(exc)})
        if settings.sportmonks_api_key:
            try:
                result["ingested"]["sportmonks"] = ingest_sportmonks_football_fixtures(db, settings.sportmonks_api_key, dates)
                result["ingested"]["soccer"] += result["ingested"]["sportmonks"]
            except Exception as exc:  # noqa: BLE001
                result["skipped"].append({"provider": "sportmonks", "sport": "soccer", "reason": str(exc)})
        if settings.football_data_api_key:
            try:
                result["ingested"]["football_data_org"] = ingest_football_data_org_matches(db, settings.football_data_api_key, dates)
                result["ingested"]["soccer"] += result["ingested"]["football_data_org"]
            except Exception as exc:  # noqa: BLE001
                result["skipped"].append({"provider": "football_data_org", "sport": "soccer", "reason": str(exc)})
    if sport in {"all", "basketball"}:
        if basketball_key:
            try:
                result["ingested"]["basketball"] = ingest_api_basketball_games(db, basketball_key, dates)
            except Exception as exc:  # noqa: BLE001
                result["skipped"].append({"sport": "basketball", "reason": str(exc)})
        else:
            result["skipped"].append({"sport": "basketball", "reason": "API_BASKETBALL_KEY or API_SPORTS_KEY not configured"})
    if sport in {"all", "multisport", "basketball", "cricket", "tennis", "american_football", "baseball", "hockey", "rugby", "volleyball", "handball", "mma", "motorsport"}:
        if settings.allsportsapi_key:
            try:
                requested = settings.allsportsapi_sport_list
                single_sport_map = {
                    "basketball": ["basketball"],
                    "cricket": ["cricket"],
                    "tennis": ["tennis"],
                    "american_football": ["american-football"],
                    "baseball": ["baseball"],
                    "hockey": ["hockey"],
                    "volleyball": ["volleyball"],
                    "handball": ["handball"],
                }
                requested = single_sport_map.get(sport, requested)
                result["ingested"]["allsportsapi"] = ingest_allsportsapi_events(db, settings.allsportsapi_key, dates, requested)
            except Exception as exc:  # noqa: BLE001
                result["skipped"].append({"provider": "allsportsapi", "reason": str(exc)})
        else:
            result["skipped"].append({"provider": "allsportsapi", "reason": "ALLSPORTSAPI_KEY not configured"})
        if settings.thesportsdb_enabled:
            try:
                sportsdb_single_sport_map = {
                    "basketball": ["Basketball"],
                    "cricket": ["Cricket"],
                    "tennis": ["Tennis"],
                    "american_football": ["American Football"],
                    "baseball": ["Baseball"],
                    "hockey": ["Ice Hockey"],
                    "rugby": ["Rugby"],
                    "mma": ["Fighting"],
                    "motorsport": ["Motorsport"],
                }
                requested_sportsdb = sportsdb_single_sport_map.get(sport, settings.thesportsdb_sport_list)
                result["ingested"]["thesportsdb"] = ingest_thesportsdb_events(db, settings.thesportsdb_api_key, dates, requested_sportsdb, settings.thesportsdb_max_calls)
            except Exception as exc:  # noqa: BLE001
                result["skipped"].append({"provider": "thesportsdb", "reason": str(exc)})
    result["fixture_count"] = db.query(Fixture).count()
    return result


@router.post("/refresh-board", dependencies=[Depends(require_admin)])
def refresh_board(days: int | None = None, db: Session = Depends(get_db)):
    """Low-click admin refresh: ingest free-tier feeds, then generate predictions."""
    ingest_report = ingest_live(days=days, sport="all", skip_odds=True, db=db)
    coverage_seeded = ensure_multisport_showcase(db)
    generated = generate_today_predictions(db)
    return {"ingest": ingest_report, "coverage_seeded": coverage_seeded, "generated_predictions": generated}


@router.post("/coverage-seed", dependencies=[Depends(require_admin)])
def coverage_seed(db: Session = Depends(get_db)):
    seeded = ensure_multisport_showcase(db)
    generated = generate_today_predictions(db)
    return {"coverage_seeded": seeded, "generated_predictions": generated}


@router.post("/train", dependencies=[Depends(require_admin)])
def train(db: Session = Depends(get_db)):
    # Load ALL history for training - no date cap
    data = dataframe_from_db(db, max_age_days=None)
    trained, skipped = [], []
    for sport, trainer in (("soccer", train_soccer_model), ("basketball", train_basketball_model)):
        try:
            sport_data = data[data["sport"] == sport].copy() if "sport" in data.columns else data.copy()
            result = trainer(sport_data)
            mv = register_model(db, sport, result["model_type"], result["path"], result["accuracy"], result["sample_size"])
            trained.append({"sport": sport, **result, "active": mv.is_active})
        except ValueError as exc:
            skipped.append({"sport": sport, "reason": str(exc)})
    return {"status": "trained", "trained": trained, "skipped": skipped}


@router.post("/ingest-historical", dependencies=[Depends(require_admin)])
def ingest_historical(league: str | None = None, db: Session = Depends(get_db)):
    """Load local historical CSVs from data/raw into the Render DB for training."""
    from pathlib import Path
    import os
    root = Path(__file__).resolve().parents[2] / "data" / "raw"
    if not root.exists():
        return {"status": "error", "detail": f"data/raw not found at {root}"}
    loaded = {"soccer": 0, "basketball": 0, "errors": []}
    league_filter = (league or "").lower()
    for dirpath, _, filenames in os.walk(root):
        skip = False
        if league_filter:
            if league_filter not in dirpath.lower():
                skip = True
        if skip:
            continue
        for fname in filenames:
            if not fname.lower().endswith(".csv"):
                continue
            fpath = Path(dirpath) / fname
            folder = Path(dirpath).name.lower()
            sport = "basketball" if "basketball" in folder or "basketball" in fname.lower() else "soccer"
            inferred_league = Path(dirpath).name
            try:
                if sport == "basketball":
                    n = load_basketball_csv(db, str(fpath), league=inferred_league)
                    loaded["basketball"] += n
                else:
                    n = load_football_csv(db, str(fpath), league=inferred_league)
                    loaded["soccer"] += n
            except Exception as exc:  # noqa: BLE001
                loaded["errors"].append({"file": str(fpath), "reason": str(exc)})
    return {"status": "done", "loaded": loaded}

@router.post("/sync-scores", dependencies=[Depends(require_admin)])
def sync_scores(db: Session = Depends(get_db)):
    """Pull live and finished scores for today's fixtures from API-Football and API-Basketball."""
    settings = get_settings()
    football_key = settings.api_football_key or settings.api_sports_key
    basketball_key = settings.api_basketball_key or settings.api_sports_key
    result = sync_live_scores(db, football_key, basketball_key)
    return {"synced": result}


@router.post("/refresh-odds", dependencies=[Depends(require_admin)])
def refresh_odds(db: Session = Depends(get_db)):
    """Refresh live odds from The Odds API for all configured sport keys.

    Call this manually when you want to force-refresh odds without waiting for
    the 15-minute scheduler cycle. Works for soccer, NBA, NFL, NHL, MLB, tennis, etc.
    """
    settings = get_settings()
    if not settings.the_odds_api_key:
        return {"error": "THE_ODDS_API_KEY not configured", "updated": 0}
    result = refresh_odds_from_the_odds_api(db, settings.the_odds_api_key, settings.odds_api_sport_keys)
    return {"refreshed": result}


@router.post("/backfill-odds", dependencies=[Depends(require_admin)])
def backfill_odds(db: Session = Depends(get_db)):
    """Backfill model-implied odds on all upcoming fixtures that have no bookmaker odds.

    Runs the prediction engine on every upcoming fixture without bookmaker odds and
    writes fair-value implied odds back to the fixture row. Safe to call repeatedly —
    it never overwrites real bookmaker odds, only fills gaps.
    """
    from app.ml.generic import GenericSportEngine
    from app.ml.ensemble import LoyalEdgeEngine
    from app.services.model_registry import active_model_path
    from app.services.predictions import _backfill_fixture_odds, dataframe_from_db

    today_ref = date.today()
    fixtures = (
        db.query(Fixture)
        .filter(
            func.date(Fixture.match_date) >= today_ref,
            Fixture.home_odds == None,
            Fixture.draw_odds == None,
            Fixture.away_odds == None,
        )
        .order_by(Fixture.match_date.asc())
        .limit(500)
        .all()
    )
    if not fixtures:
        return {"backfilled": 0, "message": "No fixtures missing odds"}

    history = dataframe_from_db(db, max_age_days=90)
    soccer_engine = LoyalEdgeEngine(active_model_path(db, "soccer"))
    generic_engine = GenericSportEngine()
    backfilled = 0
    errors = 0

    for fx in fixtures:
        try:
            if fx.sport == "soccer":
                items = soccer_engine.predict_soccer(history, {
                    "sport": fx.sport, "home_team": fx.home_team, "away_team": fx.away_team,
                    "match_date": fx.match_date, "league": fx.league,
                    "home_odds": None, "draw_odds": None, "away_odds": None,
                })
            else:
                items = generic_engine.predict(history, {
                    "sport": fx.sport, "home_team": fx.home_team,
                    "away_team": fx.away_team, "match_date": fx.match_date,
                })
            if _backfill_fixture_odds(db, fx, items):
                backfilled += 1
        except Exception:
            errors += 1
            continue

    db.commit()
    return {"backfilled": backfilled, "errors": errors, "total_checked": len(fixtures)}


@router.post("/ingest-free", dependencies=[Depends(require_admin)])
def ingest_free(max_leagues: int = 3, db: Session = Depends(get_db)):
    """Pull free historical data — call this 5-6 times to load all leagues.

    Each call downloads max_leagues leagues (default 3) to stay within
    Render's 30-second request window. Call repeatedly until all leagues loaded.

    Round 1: max_leagues=3  (EPL, La Liga, Serie A)
    Round 2: max_leagues=3  (Bundesliga, Ligue 1, Eredivisie)
    ...etc

    After all rounds, call /train to retrain models.
    """
    from app.scraper.free_data import ingest_all_free_sources
    result = ingest_all_free_sources(db, max_leagues=min(max_leagues, 5))
    total = sum(v.get("total", 0) if isinstance(v, dict) else 0 for v in result.values())
    fixture_count = db.query(Fixture).count()
    return {
        "status": "done",
        "fixtures_loaded_this_run": total,
        "total_fixtures_in_db": fixture_count,
        "result": {k: v.get("total", v) if isinstance(v, dict) else v for k, v in result.items()},
        "next_step": "Call /train when fixture count reaches 20,000+" if fixture_count < 20000 else "Ready to train — call /train now",
    }


@router.get("/training-status", dependencies=[Depends(require_admin)])
def training_status(db: Session = Depends(get_db)):
    """Quick check — how many rows do we have and what models are active."""
    from app.db.models import ModelVersion
    fixture_count = db.query(Fixture).count()
    soccer_count = db.query(Fixture).filter(Fixture.sport == "soccer", Fixture.home_score != None).count()
    active_models = db.query(ModelVersion).filter(ModelVersion.is_active == True).all()
    return {
        "total_fixtures": fixture_count,
        "soccer_completed": soccer_count,
        "ready_to_train": soccer_count >= 1000,
        "active_models": [
            {"sport": m.sport, "type": m.model_type, "rows": m.sample_size, "accuracy": round(m.accuracy * 100, 1)}
            for m in active_models
        ],
        "recommendation": (
            "✅ Run /train now" if soccer_count >= 5000
            else f"📥 Need more data — run /ingest-free (have {soccer_count} soccer rows, need 5000+)"
        ),
    }


@router.post("/train-full", dependencies=[Depends(require_admin)])
def train_full(db: Session = Depends(get_db)):
    """One-click full pipeline: ingest free data → train models → backtest → predict.

    Schedules the pipeline to run immediately via the app's built-in scheduler.
    Returns instantly. Poll GET /api/stats/backtest to see when new models appear.
    """
    from datetime import datetime, timedelta
    try:
        # Re-use the existing scheduler instance to run the pipeline immediately
        from app.services.scheduler import run_daily_learning_pipeline
        import threading
        t = threading.Thread(target=run_daily_learning_pipeline, daemon=True)
        t.start()
        return {
            "status": "started",
            "message": "Full training pipeline started in background thread. Poll /api/stats/backtest to see results as each sport trains.",
            "started_at": datetime.utcnow().isoformat(),
            "stages": ["ingest_free_data", "train_soccer", "train_basketball",
                       "train_tennis", "train_nfl", "train_nhl", "train_cricket",
                       "backtest", "predict", "backfill_odds"],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _train_full_pipeline(db: Session) -> dict:
    from app.scraper.free_data import ingest_all_free_sources
    from app.ml.backtest import walk_forward_backtest
    from app.services.predictions import _backfill_fixture_odds, dataframe_from_db
    from app.ml.generic import GenericSportEngine
    from app.ml.ensemble import LoyalEdgeEngine
    from app.services.model_registry import active_model_path

    report: dict = {
        "stage": "starting",
        "free_data": None,
        "trained": [],
        "skipped": [],
        "backtests": [],
        "generated_predictions": 0,
        "odds_backfilled": 0,
    }

    # 1. Ingest free data
    try:
        report["free_data"] = ingest_all_free_sources(db, max_leagues=20)
        report["stage"] = "data_loaded"
    except Exception as exc:
        report["free_data"] = {"error": str(exc)}

    # 2. Train models on full history — soccer, basketball, + all other sports
    data = dataframe_from_db(db, max_age_days=None)
    from app.ml.train import train_generic_sport_model

    for sport, trainer in (("soccer", train_soccer_model), ("basketball", train_basketball_model)):
        try:
            sport_data = data[data["sport"] == sport].copy() if "sport" in data.columns else data.copy()
            result = trainer(sport_data)
            mv = register_model(db, sport, result["model_type"], result["path"], result["accuracy"], result["sample_size"])
            report["trained"].append({"sport": sport, **result, "active": mv.is_active})
        except Exception as exc:
            report["skipped"].append({"sport": sport, "reason": str(exc)})

    # Generic binary model for all other sports with enough data
    for sport in ("tennis", "american_football", "hockey", "cricket", "rugby", "baseball"):
        try:
            sport_data = data[data["sport"] == sport].copy() if "sport" in data.columns else pd.DataFrame()
            if sport_data.empty:
                continue
            result = train_generic_sport_model(sport_data, sport)
            mv = register_model(db, sport, result["model_type"], result["path"], result["accuracy"], result["sample_size"])
            report["trained"].append({"sport": sport, **result, "active": mv.is_active})
        except Exception as exc:
            report["skipped"].append({"sport": sport, "reason": str(exc)})
    report["stage"] = "models_trained"

    # 3. Backtest
    for sport in ("soccer", "basketball"):
        try:
            sport_data = data[data["sport"] == sport].copy() if "sport" in data.columns else data.copy()
            result = walk_forward_backtest(sport_data, sport)
            run = BacktestRun(
                sport=sport, model_type=result["model_type"],
                split_strategy=result["split_strategy"],
                sample_size=result["sample_size"], accuracy=result["accuracy"],
                brier_score=result["brier_score"], log_loss=result["log_loss"],
                metrics=result["metrics"],
            )
            db.add(run)
            db.commit()
            report["backtests"].append({"sport": sport, "accuracy": result["accuracy"], "sample_size": result["sample_size"]})
        except Exception as exc:
            report["skipped"].append({"stage": "backtest", "sport": sport, "reason": str(exc)})

    # 4. Generate predictions
    try:
        report["generated_predictions"] = generate_today_predictions(db)
    except Exception as exc:
        report["skipped"].append({"stage": "predict", "reason": str(exc)})

    # 5. Backfill odds
    try:
        history = dataframe_from_db(db, max_age_days=90)
        soccer_engine = LoyalEdgeEngine(active_model_path(db, "soccer"))
        generic_engine = GenericSportEngine()
        today_ref = date.today()
        missing = db.query(Fixture).filter(
            func.date(Fixture.match_date) >= today_ref,
            Fixture.home_odds == None, Fixture.draw_odds == None, Fixture.away_odds == None,
        ).limit(500).all()
        backfilled = 0
        for fx in missing:
            try:
                if fx.sport == "soccer":
                    items = soccer_engine.predict_soccer(history, {
                        "sport": fx.sport, "home_team": fx.home_team, "away_team": fx.away_team,
                        "match_date": fx.match_date, "league": fx.league,
                        "home_odds": None, "draw_odds": None, "away_odds": None,
                    })
                else:
                    items = generic_engine.predict(history, {
                        "sport": fx.sport, "home_team": fx.home_team,
                        "away_team": fx.away_team, "match_date": fx.match_date,
                    })
                if _backfill_fixture_odds(db, fx, items):
                    backfilled += 1
            except Exception:
                continue
        db.commit()
        report["odds_backfilled"] = backfilled
    except Exception as exc:
        report["skipped"].append({"stage": "odds_backfill", "reason": str(exc)})

    report["stage"] = "complete"
    return {"status": "complete", "report": report}


@router.post("/sync-events", dependencies=[Depends(require_admin)])
def sync_events(db: Session = Depends(get_db)):
    """Manually trigger live event sync (goals, cards, lineups) for in-progress matches."""
    from app.services.live_events import sync_live_events
    settings = get_settings()
    key = settings.api_football_key or settings.api_sports_key
    result = sync_live_events(db, key)
    return {"synced": result}


@router.post("/scan-value", dependencies=[Depends(require_admin)])
def scan_value(sport: str | None = None, min_edge: float = 1.04, db: Session = Depends(get_db)):
    """Scrape SportyBet and run the value betting engine immediately.

    Returns all fixtures where our model finds a mathematical edge over
    SportyBet's overround-stripped fair probabilities.
    """
    from app.services.value_bets import run_value_scan
    sports = [sport] if sport else None
    return run_value_scan(db, sports=sports, min_edge=min_edge)


@router.post("/predict", dependencies=[Depends(require_admin)])
def predict(db: Session = Depends(get_db)):
    return {"generated": generate_today_predictions(db)}


@router.get("/diagnose-predict", dependencies=[Depends(require_admin)])
def diagnose_predict(db: Session = Depends(get_db)):
    """Diagnose why predictions are not being generated."""
    from app.ml.generic import GenericSportEngine
    today_ref = date.today()
    raw = db.query(Fixture).filter(
        func.date(Fixture.match_date) >= today_ref,
        Fixture.home_score == None,
        Fixture.away_score == None,
    ).limit(5).all()
    sample_items = []
    if raw:
        engine = GenericSportEngine()
        import pandas as pd
        fx = raw[0]
        try:
            items = engine.predict(pd.DataFrame(), {"sport": fx.sport, "home_team": fx.home_team, "away_team": fx.away_team, "match_date": fx.match_date})
            sample_items = items
        except Exception as exc:
            sample_items = [{"error": str(exc)}]
    return {
        "today": today_ref.isoformat(),
        "raw_fixtures_found": len(raw),
        "sample_fixtures": [{"id": f.id, "sport": f.sport, "home_team": f.home_team, "away_team": f.away_team, "match_date": str(f.match_date)} for f in raw],
        "sample_engine_output": sample_items,
    }


@router.post("/backtest", dependencies=[Depends(require_admin)])
def backtest(db: Session = Depends(get_db)):
    data = dataframe_from_db(db)
    completed, skipped = [], []
    for sport in ("soccer", "basketball"):
        try:
            sport_data = data[data["sport"] == sport].copy() if "sport" in data.columns else data.copy()
            result = walk_forward_backtest(sport_data, sport)
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
