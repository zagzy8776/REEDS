"""
Render-safe scheduler — lightweight jobs only.

Memory budget on Render free tier (512MB):
  Score sync     ~20 MB   (API call + small DB write)
  Live events    ~15 MB
  Refresh job    ~80-120 MB (ingest + 90-day history for predictions)
  Value scan     ~30 MB

Training, calibration, and backtest are intentionally excluded.
Those run on Hugging Face Space where RAM is not a constraint.
"""
import gc
import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.scraper.loaders import (
    ingest_allsportsapi_events,
    ingest_api_basketball_games,
    ingest_api_football_fixtures,
    ingest_thesportsdb_events,
    sync_live_scores,
    refresh_odds_from_the_odds_api,
)
from app.services.community import settle_user_predictions
from app.services.coverage_seed import ensure_multisport_showcase
from app.services.predictions import generate_today_predictions

log = logging.getLogger(__name__)


def _date_window(days: int) -> list[str]:
    return [
        (date.today() + timedelta(days=i)).isoformat()
        for i in range(max(days, 1))
    ]


def seed_local_csv_history(db: Session) -> int:
    """Load local CSVs from data/raw into Neon on first boot (soccer + basketball)."""
    from pathlib import Path
    from app.scraper.loaders import load_basketball_csv, load_football_csv

    root = Path("data/raw")
    if not root.exists():
        return 0
    count = 0
    for path in sorted(root.rglob("*.csv")):
        lowered = str(path).lower()
        sport = "basketball" if ("basket" in lowered or "nba" in lowered) else "soccer"
        league = "Football"
        for known in ["EPL", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1",
                      "CHAMPIONSHIP", "EREDIVISIE", "PORTUGAL", "BELGIUM",
                      "SCOTLAND", "TURKEY"]:
            if known.lower() in lowered:
                league = known.replace("_", " ")
                break
        try:
            if sport == "basketball":
                count += load_basketball_csv(db, str(path), league=league, season="Historical")
            else:
                count += load_football_csv(db, str(path), league=league, season="Historical")
        except Exception:
            continue
    db.commit()
    return count


# ---------------------------------------------------------------------------
# Core lightweight refresh — runs on Render every 2 hours
# ---------------------------------------------------------------------------

def run_lightweight_refresh() -> dict:
    """Ingest live fixtures, generate predictions, sync odds.
    Never loads full history. Never trains. Peak RAM ~100 MB.
    """
    db = SessionLocal()
    settings = get_settings()
    report: dict = {
        "ingested": {},
        "predictions_generated": 0,
        "coverage_seeded": {},
        "skipped": [],
    }

    try:
        dates = _date_window(settings.live_ingest_days)
        football_key   = settings.api_football_key or settings.api_sports_key
        basketball_key = settings.api_basketball_key or settings.api_sports_key

        # ── Live fixture ingestion ────────────────────────────────────────
        if football_key:
            try:
                n = ingest_api_football_fixtures(
                    db, football_key, dates,
                    include_odds=True,
                    the_odds_api_key=settings.the_odds_api_key,
                    the_odds_api_sport_keys=settings.odds_api_sport_keys,
                )
                report["ingested"]["api_football"] = n
            except Exception as exc:
                report["skipped"].append({"provider": "api_football", "reason": str(exc)})

        if basketball_key:
            try:
                n = ingest_api_basketball_games(db, basketball_key, dates)
                report["ingested"]["basketball"] = n
            except Exception as exc:
                report["skipped"].append({"provider": "basketball", "reason": str(exc)})

        if settings.allsportsapi_key:
            try:
                n = ingest_allsportsapi_events(
                    db, settings.allsportsapi_key, dates,
                    settings.allsportsapi_sport_list,
                )
                report["ingested"]["allsportsapi"] = n
            except Exception as exc:
                report["skipped"].append({"provider": "allsportsapi", "reason": str(exc)})

        if settings.thesportsdb_enabled:
            try:
                n = ingest_thesportsdb_events(
                    db, settings.thesportsdb_api_key, dates,
                    settings.thesportsdb_sport_list,
                    settings.thesportsdb_max_calls,
                )
                report["ingested"]["thesportsdb"] = n
            except Exception as exc:
                report["skipped"].append({"provider": "thesportsdb", "reason": str(exc)})

        # ── Coverage seed ─────────────────────────────────────────────────
        try:
            report["coverage_seeded"] = ensure_multisport_showcase(db)
        except Exception as exc:
            report["skipped"].append({"stage": "coverage_seed", "reason": str(exc)})

        # ── Community settlement ──────────────────────────────────────────
        try:
            settle_user_predictions(db)
        except Exception:
            pass

        # ── Predictions (90-day history cap — RAM safe) ───────────────────
        try:
            report["predictions_generated"] = generate_today_predictions(db)
        except Exception as exc:
            report["skipped"].append({"stage": "predict", "reason": str(exc)})

        # ── Insider signals (no DataFrame load) ───────────────────────────
        try:
            from app.services.insider_signals import refresh_insider_signals
            refresh_insider_signals(db, odds_api_key=settings.the_odds_api_key)
        except Exception:
            pass

        # ── Line efficiency (market intelligence) ─────────────────────────
        try:
            from app.services.market_intelligence import refresh_line_efficiency
            refresh_line_efficiency(db, days_ahead=3)
        except Exception:
            pass

        return report

    finally:
        db.close()
        gc.collect()


# Alias so external callers (admin.py /train-full etc.) don't break
def run_daily_learning_pipeline() -> dict:
    return run_lightweight_refresh()


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    settings = get_settings()

    # ── Lightweight refresh every 2 hours ─────────────────────────────────
    def refresh_job():
        report = run_lightweight_refresh()
        log.info(
            "Refresh: ingested=%s predictions=%d skipped=%d",
            report.get("ingested", {}),
            report.get("predictions_generated", 0),
            len(report.get("skipped", [])),
        )

    scheduler.add_job(
        refresh_job, "interval", hours=2,
        id="lightweight_refresh",
        replace_existing=True, max_instances=1, coalesce=True,
    )

    # ── Score sync every 15 minutes ───────────────────────────────────────
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
        id="score_sync",
        replace_existing=True, max_instances=1, coalesce=True,
    )

    # ── Live events every 60 seconds ─────────────────────────────────────
    def live_event_job():
        from app.services.live_events import sync_live_events
        db = SessionLocal()
        try:
            result = sync_live_events(
                db, settings.api_football_key or settings.api_sports_key
            )
            if result.get("new_events"):
                log.info("Live events: %s", result)
        except Exception:
            log.exception("Live event sync failed")
        finally:
            db.close()
            gc.collect()

    scheduler.add_job(
        live_event_job, "interval", seconds=60,
        id="live_events",
        replace_existing=True, max_instances=1, coalesce=True,
    )

    # ── Value bet scan every 30 minutes ──────────────────────────────────
    def value_scan_job():
        from app.services.value_bets import run_value_scan
        db = SessionLocal()
        try:
            result = run_value_scan(db, sports=["soccer", "basketball", "tennis"])
            found = result.get("value_bets_found", 0)
            if found:
                log.info("Value scan: %d bets from %d fixtures",
                         found, result.get("scanned", 0))
        except Exception:
            log.exception("Value scan failed")
        finally:
            db.close()
            gc.collect()

    scheduler.add_job(
        value_scan_job, "interval", minutes=30,
        id="value_scan",
        replace_existing=True, max_instances=1, coalesce=True,
    )

    # ── Startup: first refresh 30 s after boot ────────────────────────────
    scheduler.add_job(
        refresh_job, "date",
        run_date=datetime.utcnow() + timedelta(seconds=30),
        id="startup_refresh",
        replace_existing=True, max_instances=1,
    )

    scheduler.start()
    log.info("Scheduler started — lightweight mode (training runs on HF Space)")
    return scheduler
