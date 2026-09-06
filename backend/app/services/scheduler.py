"""Render-safe scheduler for ingestion, coverage recovery, and prediction builds."""
from __future__ import annotations

import gc
import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.scraper.coverage_sources import ingest_bzzoiro_football, ingest_openfoot_football
from app.scraper.loaders import (
    ingest_allsportsapi_events,
    ingest_api_basketball_games,
    ingest_api_football_fixtures,
    ingest_apifootball_com_events,
    ingest_football_data_org_matches,
    ingest_sportmonks_football_fixtures,
    ingest_thesportsdb_events,
    refresh_odds_from_the_odds_api,
    sync_live_scores,
)
from app.services.deep_coverage import purge_showcase_rows, run_deep_coverage
from app.services.fixture_normalizer import normalize_fixture_sports
from app.services.predictions import generate_today_predictions
from app.services.public_football_sources import (
    ingest_fixture_download_football,
    ingest_sporting_events_football,
)

log = logging.getLogger(__name__)


def _date_window(days: int) -> list[str]:
    return [
        (date.today() + timedelta(days=i)).isoformat()
        for i in range(max(days, 1))
    ]


def run_lightweight_refresh() -> dict:
    """Ingest live data, recover thin coverage, normalize it, then build picks."""
    db = SessionLocal()
    settings = get_settings()
    report: dict = {
        "ingested": {},
        "predictions_generated": 0,
        "coverage_recovery": None,
        "normalization": None,
        "purged_showcase": 0,
        "skipped": [],
    }

    try:
        dates = _date_window(settings.live_ingest_days)
        football_key = settings.api_football_key or settings.api_sports_key
        basketball_key = settings.api_basketball_key or settings.api_sports_key

        providers = []
        if football_key:
            providers.append((
                "api_football", ingest_api_football_fixtures,
                (db, football_key, dates),
                {
                    "include_odds": True,
                    "the_odds_api_key": settings.the_odds_api_key,
                    "the_odds_api_sport_keys": settings.odds_api_sport_keys,
                },
            ))
        if settings.sportmonks_api_key:
            providers.append(("sportmonks", ingest_sportmonks_football_fixtures,
                              (db, settings.sportmonks_api_key, dates), {}))
        if settings.football_data_api_key:
            providers.append(("football_data_org", ingest_football_data_org_matches,
                              (db, settings.football_data_api_key, dates), {}))
        if settings.api_football_com_key:
            providers.append(("apifootball_com", ingest_apifootball_com_events,
                              (db, settings.api_football_com_key, dates), {}))
        if settings.bzzoiro_api_key:
            providers.append(("bzzoiro", ingest_bzzoiro_football,
                              (db, settings.bzzoiro_api_key, dates), {}))
        if settings.openfoot_api_key:
            providers.append(("openfoot", ingest_openfoot_football,
                              (db, settings.openfoot_api_key, dates), {}))
        if basketball_key:
            providers.append(("basketball", ingest_api_basketball_games,
                              (db, basketball_key, dates), {}))
        if settings.allsportsapi_key:
            providers.append(("allsportsapi", ingest_allsportsapi_events,
                              (db, settings.allsportsapi_key, dates, settings.allsportsapi_sport_list), {}))
        if settings.thesportsdb_enabled:
            providers.append(("thesportsdb", ingest_thesportsdb_events,
                              (db, settings.thesportsdb_api_key, dates,
                               settings.thesportsdb_sport_list, settings.thesportsdb_max_calls), {}))

        configured_names = {name for name, *_ in providers}
        for expected in (
            "api_football", "sportmonks", "football_data_org", "apifootball_com",
            "bzzoiro", "openfoot", "allsportsapi", "thesportsdb",
        ):
            if expected not in configured_names:
                report["skipped"].append({"provider": expected, "reason": "not configured"})

        for name, fn, args, kwargs in providers:
            try:
                result = fn(*args, **kwargs)
                report["ingested"][name] = int(result or 0)
            except Exception as exc:
                report["skipped"].append({"provider": name, "reason": str(exc)[:300]})
                log.exception("Fixture provider failed: %s", name)

        # Clean old placeholder rows left by earlier releases before coverage
        # recovery and before prediction generation.
        try:
            report["purged_showcase"] = purge_showcase_rows(db)
        except Exception as exc:
            report["skipped"].append({"stage": "purge_showcase", "reason": str(exc)[:300]})

        # Correct provider sport metadata before predictions see the fixtures.
        try:
            report["normalization"] = normalize_fixture_sports(db)
        except Exception as exc:
            report["skipped"].append({"stage": "fixture_normalization", "reason": str(exc)[:300]})

        # Ranged/paged/public sources are the coverage escalator. It runs only
        # while genuine future coverage is below the real production floor.
        try:
            report["coverage_recovery"] = run_deep_coverage(db, min_coverage=300)
        except Exception as exc:
            report["skipped"].append({"stage": "deep_coverage", "reason": str(exc)[:300]})
            log.exception("Deep coverage failed")

        try:
            post = normalize_fixture_sports(db)
            if report["normalization"]:
                report["normalization"]["post_coverage"] = post
            else:
                report["normalization"] = post
        except Exception as exc:
            report["skipped"].append({"stage": "post_coverage_normalization", "reason": str(exc)[:300]})

        try:
            from app.services.community import settle_user_predictions
            settle_user_predictions(db)
        except Exception:
            log.exception("Community settlement failed")

        try:
            report["predictions_generated"] = generate_today_predictions(db)
        except Exception as exc:
            report["skipped"].append({"stage": "predict", "reason": str(exc)[:300]})

        try:
            from app.services.insider_signals import refresh_insider_signals
            refresh_insider_signals(db, odds_api_key=settings.the_odds_api_key)
        except Exception:
            log.exception("Insider signal refresh failed")

        try:
            from app.services.market_intelligence import refresh_line_efficiency
            refresh_line_efficiency(db, days_ahead=3)
        except Exception:
            log.exception("Line efficiency refresh failed")

        return report
    finally:
        db.close()
        gc.collect()


def run_daily_learning_pipeline() -> dict:
    return run_lightweight_refresh()


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    settings = get_settings()

    def refresh_job():
        report = run_lightweight_refresh()
        coverage = report.get("coverage_recovery") or {}
        log.info(
            "Refresh: providers=%s coverage=%s predictions=%d purged_showcase=%d skipped=%d",
            report.get("ingested", {}),
            {
                "before": coverage.get("before"),
                "after": coverage.get("after"),
                "target": coverage.get("target"),
            },
            report.get("predictions_generated", 0),
            report.get("purged_showcase", 0),
            len(report.get("skipped", [])),
        )

    scheduler.add_job(
        refresh_job, "interval", hours=2,
        id="lightweight_refresh", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    def public_coverage_job():
        db = SessionLocal()
        try:
            dates = _date_window(settings.live_ingest_days)
            fixture_download = ingest_fixture_download_football(db, dates, max_competitions=32)
            sporting_events = ingest_sporting_events_football(db, dates)
            normalize_fixture_sports(db)
            log.info(
                "Public football coverage: fixture_download=%d sporting_events=%d",
                fixture_download, sporting_events,
            )
        except Exception:
            log.exception("Public football coverage failed")
        finally:
            db.close()
            gc.collect()

    scheduler.add_job(
        public_coverage_job, "interval", hours=6,
        id="public_football_coverage", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    def score_sync_job():
        db = SessionLocal()
        try:
            result = sync_live_scores(
                db,
                settings.api_football_key or settings.api_sports_key,
                settings.api_basketball_key or settings.api_sports_key,
            )
            if settings.the_odds_api_key:
                odds = refresh_odds_from_the_odds_api(
                    db, settings.the_odds_api_key, settings.odds_api_sport_keys
                )
                result["odds_refreshed"] = odds.get("updated", 0)
            log.info("Score sync: %s", result)
        except Exception:
            log.exception("Score sync failed")
        finally:
            db.close()
            gc.collect()

    scheduler.add_job(
        score_sync_job, "interval", minutes=15,
        id="score_sync", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    def live_event_job():
        from app.services.live_events import sync_live_events
        db = SessionLocal()
        try:
            result = sync_live_events(db, settings.api_football_key or settings.api_sports_key)
            if result.get("new_events"):
                log.info("Live events: %s", result)
        except Exception:
            log.exception("Live event sync failed")
        finally:
            db.close()
            gc.collect()

    scheduler.add_job(
        live_event_job, "interval", seconds=60,
        id="live_events", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    def value_scan_job():
        from app.services.value_bets import run_value_scan
        db = SessionLocal()
        try:
            result = run_value_scan(db, sports=["soccer", "basketball", "tennis"])
            if result.get("value_bets_found"):
                log.info(
                    "Value scan: %d bets from %d fixtures",
                    result.get("value_bets_found", 0), result.get("scanned", 0),
                )
        except Exception:
            log.exception("Value scan failed")
        finally:
            db.close()
            gc.collect()

    scheduler.add_job(
        value_scan_job, "interval", minutes=30,
        id="value_scan", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    scheduler.add_job(
        refresh_job, "date",
        run_date=datetime.utcnow() + timedelta(seconds=30),
        id="startup_refresh", replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    log.info("Scheduler started — real coverage recovery enabled")
    return scheduler
