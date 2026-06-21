import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import BacktestRun
from app.db.session import SessionLocal
from app.ml.backtest import walk_forward_backtest
from app.ml.calibration import fit_soccer_platt_calibrator
from app.ml.train import train_basketball_model, train_soccer_model
from app.scraper.loaders import ingest_allsportsapi_events, ingest_api_basketball_games, ingest_api_football_fixtures, ingest_apifootball_com_events, ingest_football_data_org_matches, ingest_sportmonks_football_fixtures, ingest_thesportsdb_events, sync_live_scores, refresh_odds_from_the_odds_api
from app.services.community import settle_user_predictions
from app.services.coverage_seed import ensure_multisport_showcase
from app.services.model_registry import register_model
from app.services.predictions import dataframe_from_db, generate_today_predictions


log = logging.getLogger(__name__)


def _date_window(days: int) -> list[str]:
     return [(date.today() + timedelta(days=offset)).isoformat() for offset in range(max(days, 1))]


def seed_local_csv_history(db: Session) -> int:
    """Load 30+ historical CSV files sitting in data/raw into the database for model training."""
    from pathlib import Path
    from app.scraper.loaders import load_basketball_csv, load_football_csv
    root = Path("data/raw")
    files = sorted(root.rglob("*.csv"))
    count = 0
    for path in files:
        lowered = str(path).lower()
        sport = "basketball" if "basket" in lowered or "nba" in lowered else "soccer"
        parts = [p.upper() for p in path.parts]
        known = ["EPL", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "CHAMPIONSHIP",
                 "EREDIVISIE", "PORTUGAL", "BELGIUM", "SCOTLAND", "TURKEY"]
        league = "Football"
        for item in known:
            if item in parts or item.lower() in str(path).lower():
                league = item.replace("_", " ")
                break
        try:
            if sport == "basketball":
                loaded = load_basketball_csv(db, str(path), league=league, season="Historical")
            else:
                loaded = load_football_csv(db, str(path), league=league, season="Historical")
            count += loaded
        except Exception:
            continue
    db.commit()
    return count


def run_daily_learning_pipeline() -> dict:
    """Run the full unattended daily model refresh pipeline.

    Order matters:
    1. Train/register fresh model candidates.
    2. Refresh calibration from the same current database boundary.
    3. Persist walk-forward audit rows where possible.
    4. Generate customer-facing predictions from active models.

    Each stage records failures without hiding earlier successful stages. This keeps
    the scheduler resilient while making deployment logs useful for operations.
    """

    db = SessionLocal()
    settings = get_settings()
    report: dict = {"ingested": {"soccer": 0, "api_sports_football": 0, "apifootball_com": 0, "sportmonks": 0, "football_data_org": 0, "basketball": 0, "allsportsapi": 0, "thesportsdb": 0}, "coverage_seeded": {}, "call_budget": {"live_ingest_days": settings.live_ingest_days, "thesportsdb_max_calls": settings.thesportsdb_max_calls, "odds_skipped_on_scheduler": True}, "community_settled": 0, "trained": [], "calibrated": None, "backtests": [], "skipped": [], "generated_predictions": 0}
    try:
        # Seed historical CSV data if not already loaded
        csv_count = seed_local_csv_history(db)
        if csv_count:
            log.info("Seeded %d historical CSV rows into database", csv_count)

        # Pull free multi-sport data (football-data.co.uk, tennis, NBA, NFL, NHL, IPL)
        # Only runs when the DB has fewer than 5000 rows to avoid re-downloading every day
        from app.db.models import Fixture as _Fixture
        fixture_count = db.query(_Fixture).count()
        if fixture_count < 5000:
            try:
                from app.scraper.free_data import ingest_all_free_sources
                free_result = ingest_all_free_sources(db, max_leagues=20)
                log.info("Free data ingestion: %s", {k: v.get("total", 0) if isinstance(v, dict) else v for k, v in free_result.items()})
                report["free_data_seeded"] = True
            except Exception as exc:  # noqa: BLE001
                log.exception("Free data ingestion failed")
                report["skipped"].append({"stage": "free_data", "reason": str(exc)})

        dates = _date_window(settings.live_ingest_days)
        football_key = settings.api_football_key or settings.api_sports_key
        basketball_key = settings.api_basketball_key or settings.api_sports_key

        if football_key:
            try:
                report["ingested"]["api_sports_football"] = ingest_api_football_fixtures(
                    db,
                    football_key,
                    dates,
                    include_odds=True,
                    the_odds_api_key=settings.the_odds_api_key,
                    the_odds_api_sport_keys=settings.odds_api_sport_keys,
                )
                report["ingested"]["soccer"] += report["ingested"]["api_sports_football"]
            except Exception as exc:  # noqa: BLE001
                log.exception("Daily API-Football ingestion failed")
                report["skipped"].append({"stage": "ingest", "provider": "api_sports_football", "sport": "soccer", "reason": str(exc)})
        else:
            report["skipped"].append({"stage": "ingest", "provider": "api_sports_football", "sport": "soccer", "reason": "API_FOOTBALL_KEY/API_SPORTS_KEY not configured"})

        for provider, key, ingester in (
            ("apifootball_com", settings.api_football_com_key, ingest_apifootball_com_events),
            ("sportmonks", settings.sportmonks_api_key, ingest_sportmonks_football_fixtures),
            ("football_data_org", settings.football_data_api_key, ingest_football_data_org_matches),
        ):
            if not key:
                continue
            try:
                report["ingested"][provider] = ingester(db, key, dates)
                report["ingested"]["soccer"] += report["ingested"][provider]
            except Exception as exc:  # noqa: BLE001
                log.exception("Daily %s ingestion failed", provider)
                report["skipped"].append({"stage": "ingest", "provider": provider, "sport": "soccer", "reason": str(exc)})

        if basketball_key:
            try:
                report["ingested"]["basketball"] = ingest_api_basketball_games(db, basketball_key, dates)
            except Exception as exc:  # noqa: BLE001
                log.exception("Daily API-Basketball ingestion failed")
                report["skipped"].append({"stage": "ingest", "sport": "basketball", "reason": str(exc)})
        else:
            report["skipped"].append({"stage": "ingest", "sport": "basketball", "reason": "API_BASKETBALL_KEY/API_SPORTS_KEY not configured"})

        if settings.allsportsapi_key:
            try:
                report["ingested"]["allsportsapi"] = ingest_allsportsapi_events(db, settings.allsportsapi_key, dates, settings.allsportsapi_sport_list)
            except Exception as exc:  # noqa: BLE001
                log.exception("Daily AllSportsAPI ingestion failed")
                report["skipped"].append({"stage": "ingest", "provider": "allsportsapi", "reason": str(exc)})
        else:
            report["skipped"].append({"stage": "ingest", "provider": "allsportsapi", "reason": "ALLSPORTSAPI_KEY not configured"})

        if settings.thesportsdb_enabled:
            try:
                report["ingested"]["thesportsdb"] = ingest_thesportsdb_events(db, settings.thesportsdb_api_key, dates, settings.thesportsdb_sport_list, settings.thesportsdb_max_calls)
            except Exception as exc:  # noqa: BLE001
                log.exception("Daily TheSportsDB ingestion failed")
                report["skipped"].append({"stage": "ingest", "provider": "thesportsdb", "reason": str(exc)})

        try:
            report["coverage_seeded"] = ensure_multisport_showcase(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("Daily multi-sport coverage seed failed")
            report["skipped"].append({"stage": "coverage_seed", "reason": str(exc)})

        try:
            report["community_settled"] = settle_user_predictions(db)["settled"]
        except Exception as exc:  # noqa: BLE001
            log.exception("Daily community prediction settlement failed")
            report["skipped"].append({"stage": "community_settle", "reason": str(exc)})

        data = dataframe_from_db(db)

        for sport, trainer in (("soccer", train_soccer_model), ("basketball", train_basketball_model)):
            try:
                sport_data = data[data["sport"] == sport].copy() if "sport" in data.columns else data.copy()
                result = trainer(sport_data)
                mv = register_model(db, sport, result["model_type"], result["path"], result["accuracy"], result["sample_size"])
                report["trained"].append({"sport": sport, **result, "active": mv.is_active})
            except Exception as exc:  # noqa: BLE001 - scheduler must log and continue safely
                log.exception("Daily %s training failed", sport)
                report["skipped"].append({"stage": "train", "sport": sport, "reason": str(exc)})

        # Train generic binary models for all other sports with enough data
        try:
            from app.ml.train import train_generic_sport_model
            for sport in ("tennis", "american_football", "hockey", "cricket", "rugby", "baseball"):
                try:
                    sport_data = data[data["sport"] == sport].copy() if "sport" in data.columns else None
                    if sport_data is None or sport_data.empty:
                        continue
                    result = train_generic_sport_model(sport_data, sport)
                    mv = register_model(db, sport, result["model_type"], result["path"], result["accuracy"], result["sample_size"])
                    report["trained"].append({"sport": sport, **result, "active": mv.is_active})
                except Exception as exc:  # noqa: BLE001
                    report["skipped"].append({"stage": "train", "sport": sport, "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("Generic sport training failed")
            report["skipped"].append({"stage": "train_generic", "reason": str(exc)})

        try:
            soccer_data = data[data["sport"] == "soccer"].copy() if "sport" in data.columns else data.copy()
            report["calibrated"] = fit_soccer_platt_calibrator(soccer_data)
        except Exception as exc:  # noqa: BLE001
            log.exception("Daily soccer calibration failed")
            report["skipped"].append({"stage": "calibrate", "sport": "soccer", "reason": str(exc)})

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
                report["backtests"].append({"id": run.id, **result})
            except Exception as exc:  # noqa: BLE001
                log.exception("Daily %s backtest failed", sport)
                report["skipped"].append({"stage": "backtest", "sport": sport, "reason": str(exc)})

        try:
            report["generated_predictions"] = generate_today_predictions(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("Daily prediction generation failed")
            report["skipped"].append({"stage": "predict", "reason": str(exc)})

        # Backfill model-implied odds on any fixture still missing odds after ingestion
        try:
            from app.ml.generic import GenericSportEngine
            from app.ml.ensemble import LoyalEdgeEngine
            from app.services.model_registry import active_model_path
            from app.services.predictions import _backfill_fixture_odds

            today_ref = date.today()
            from app.db.models import Fixture as _Fixture
            missing = (
                db.query(_Fixture)
                .filter(
                    func.date(_Fixture.match_date) >= today_ref,
                    _Fixture.home_odds == None,
                    _Fixture.draw_odds == None,
                    _Fixture.away_odds == None,
                )
                .order_by(_Fixture.match_date.asc())
                .limit(300)
                .all()
            )
            if missing:
                _history = dataframe_from_db(db, max_age_days=90)
                _soccer_engine = LoyalEdgeEngine(active_model_path(db, "soccer"))
                _generic_engine = GenericSportEngine()
                _backfilled = 0
                for _fx in missing:
                    try:
                        if _fx.sport == "soccer":
                            _items = _soccer_engine.predict_soccer(_history, {
                                "sport": _fx.sport, "home_team": _fx.home_team,
                                "away_team": _fx.away_team, "match_date": _fx.match_date,
                                "league": _fx.league, "home_odds": None, "draw_odds": None, "away_odds": None,
                            })
                        else:
                            _items = _generic_engine.predict(_history, {
                                "sport": _fx.sport, "home_team": _fx.home_team,
                                "away_team": _fx.away_team, "match_date": _fx.match_date,
                            })
                        if _backfill_fixture_odds(db, _fx, _items):
                            _backfilled += 1
                    except Exception:
                        continue
                db.commit()
                report["odds_backfilled"] = _backfilled
        except Exception as exc:  # noqa: BLE001
            log.exception("Odds backfill failed")
            report["skipped"].append({"stage": "odds_backfill", "reason": str(exc)})

        return report
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    def daily_job():
        report = run_daily_learning_pipeline()
        log.info("Daily learning pipeline completed: %s", report)

    def live_refresh_job():
        """Refresh fixtures/predictions repeatedly so completed matches make way for upcoming action."""
        report = run_daily_learning_pipeline()
        log.info("Live refresh pipeline completed: %s", report)

    scheduler.add_job(
        daily_job,
        "cron",
        hour=6,
        minute=0,
        id="daily_learning_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        live_refresh_job,
        "interval",
        hours=3,
        id="live_fixture_refresh_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Score sync every 15 minutes so users see live/final scores
    def score_sync_job():
        db = SessionLocal()
        settings = get_settings()
        try:
            result = sync_live_scores(
                db,
                settings.api_football_key or settings.api_sports_key,
                settings.api_basketball_key or settings.api_sports_key,
            )
            # Also refresh odds for all configured sports from The Odds API
            if settings.the_odds_api_key:
                odds_result = refresh_odds_from_the_odds_api(
                    db,
                    settings.the_odds_api_key,
                    settings.odds_api_sport_keys,
                )
                result["odds_refreshed"] = odds_result.get("updated", 0)
            log.info("Score sync: %s", result)
        except Exception:
            log.exception("Score sync job failed")
        finally:
            db.close()

    scheduler.add_job(
        score_sync_job,
        "interval",
        minutes=15,
        id="live_score_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Live event sync every 60 seconds during live matches (goals, cards, lineups)
    def live_event_job():
        from app.services.live_events import sync_live_events
        db = SessionLocal()
        settings = get_settings()
        try:
            key = settings.api_football_key or settings.api_sports_key
            result = sync_live_events(db, key)
            if result["new_events"]:
                log.info("Live events: %s", result)
        except Exception:
            log.exception("Live event sync job failed")
        finally:
            db.close()

    scheduler.add_job(
        live_event_job,
        "interval",
        seconds=60,
        id="live_event_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Free data ingestion weekly — pulls football-data.co.uk + OpenFootball
    def free_data_job():
        from app.scraper.free_data import ingest_all_free_sources
        db = SessionLocal()
        try:
            result = ingest_all_free_sources(db, max_leagues=6)
            log.info("Free data ingestion: %s", result)
        except Exception:
            log.exception("Free data ingestion job failed")
        finally:
            db.close()

    scheduler.add_job(
        free_data_job,
        "cron",
        day_of_week="sun",
        hour=3,
        minute=0,
        id="free_data_weekly",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        daily_job,
        "date",
        run_date=datetime.utcnow() + timedelta(seconds=30),
        id="startup_learning_pipeline",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    return scheduler
