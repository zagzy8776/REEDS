import logging
import secrets
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import admin, public, live, fixtures, model_sync
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import init_db, engine


setup_logging()
log = logging.getLogger(__name__)
settings = get_settings()
app = FastAPI(title="LOYAL EDGE API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(public.router, prefix="/api")
app.include_router(admin.router, prefix="/api/admin")
app.include_router(live.router, prefix="/api")
app.include_router(fixtures.router, prefix="/api")
app.include_router(model_sync.router)


def _bootstrap_models_background() -> None:
    try:
        from app.db.session import SessionLocal
        from app.services.model_bootstrap import restore_missing_models
        db = SessionLocal()
        try:
            result = restore_missing_models(db)
            log.info("Model bootstrap: %s", result)
        finally:
            db.close()
    except Exception:
        log.exception("Background model bootstrap failed")


@app.on_event("startup")
def on_startup():
    init_db()

    try:
        from app.services.model_bootstrap import install_quality_training
        install_quality_training()
    except Exception:
        log.exception("Could not install quality training guard")

    threading.Thread(target=_bootstrap_models_background, name="model-bootstrap", daemon=True).start()

    from app.services.prediction_guard import install_prediction_guard
    install_prediction_guard()

    if settings.enable_scheduler:
        from app.services.scheduler import start_scheduler
        start_scheduler()


@app.get("/health")
def health():
    return {"ok": True, "brand": settings.public_brand_name}


@app.get("/api/health")
def api_health():
    return health()


@app.get("/ready")
def readiness():
    from fastapi.responses import JSONResponse
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "ready": True, "database": "ok"}
    except Exception as exc:
        log.exception("Readiness database check failed")
        return JSONResponse(
            status_code=503,
            content={"ok": False, "ready": False, "database": "error", "detail": str(exc)[:200]},
        )


@app.get("/api/readiness")
def api_readiness():
    return readiness()


@app.get("/api/feed-health")
def api_feed_health():
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        return fixtures.fixtures_status(db=db)
    finally:
        db.close()


@app.get("/api/wake")
def wake(request: Request):
    """Cron heartbeat: synchronously recover thin fixture coverage before returning."""
    if settings.cron_secret:
        supplied = request.headers.get("x-cron-secret", "")
        if not supplied:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
        if not supplied or not secrets.compare_digest(supplied, settings.cron_secret):
            raise HTTPException(status_code=401, detail="Invalid cron credential")

    from datetime import date, timedelta
    from sqlalchemy import func
    from fastapi.responses import JSONResponse
    from app.db.models import Fixture, Prediction
    from app.db.session import SessionLocal
    from app.services.prediction_runner import start_prediction_generation
    from app.services.coverage_runner import start_coverage_refresh
    from app.scraper.loaders import sync_live_scores

    db = SessionLocal()
    scores_synced = {}
    prediction_queued = False
    coverage_queued = False
    coverage_trigger = "not_needed"
    coverage_report = None
    error = None

    try:
        scores_synced = sync_live_scores(
            db,
            settings.api_football_key or settings.api_sports_key,
            settings.api_basketball_key or settings.api_sports_key,
        )

        today = date.today()
        coverage_horizon = today + timedelta(days=2)
        upcoming_count = (
            db.query(Fixture.id)
            .filter(func.date(Fixture.match_date) >= today, func.date(Fixture.match_date) <= coverage_horizon, Fixture.source != "coverage_seed")
            .count()
        )
        all_future_count = (
            db.query(Fixture.id)
            .filter(func.date(Fixture.match_date) >= today, Fixture.source != "coverage_seed")
            .count()
        )

        # External cron is the reliable wake path for a Render service that may
        # sleep. Do the real coverage escalation in this request so the process
        # cannot return immediately and then be suspended before background work
        # has persisted the fixtures.
        if all_future_count < 300:
            coverage_trigger = "low_real_fixture_coverage"
            from app.scraper.deep_coverage import run_deep_coverage
            coverage_report = run_deep_coverage(db, min_coverage=300)
            all_future_count = (
                db.query(Fixture.id)
                .filter(func.date(Fixture.match_date) >= today, Fixture.source != "coverage_seed")
                .count()
            )
            log.info(
                "Wake synchronous fixture recovery: before=%s after=%s report=%s",
                coverage_report.get("before") if coverage_report else None,
                all_future_count,
                coverage_report,
            )
        elif upcoming_count < 10:
            # Still queue normal scheduled fanout for a very short near-term gap.
            coverage_trigger = "low_fixtures_next_48h"
            coverage_queued = start_coverage_refresh(reason=f"cron_{coverage_trigger}")

        active_today = (
            db.query(Prediction)
            .join(Fixture, Prediction.fixture_id == Fixture.id)
            .filter(
                Prediction.is_published == True,
                Prediction.status == "active",
                func.date(Fixture.match_date) == today,
                Fixture.source != "coverage_seed",
            )
            .count()
        )

        if active_today == 0 and all_future_count > 0 and not coverage_queued:
            prediction_queued = start_prediction_generation(reason="cron_wake_empty_board")
            log.info("Wake endpoint queued prediction recovery: queued=%s", prediction_queued)
    except Exception as exc:
        error = str(exc)[:300]
        log.exception("Wake endpoint failed")
    finally:
        db.close()

    payload = {
        "ok": error is None,
        "scores_synced": scores_synced,
        "coverage_refresh_queued": coverage_queued,
        "coverage_trigger": coverage_trigger,
        "coverage_report": coverage_report,
        "prediction_build_queued": prediction_queued,
    }
    if error:
        payload["error"] = error
        return JSONResponse(status_code=503, content=payload)
    return payload
