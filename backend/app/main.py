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
    """Fast cron heartbeat; enqueue heavy fixture/model work and return immediately."""
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
    from app.db.models import Fixture, Prediction
    from app.db.session import SessionLocal
    from app.services.prediction_runner import start_prediction_generation
    from app.services.coverage_runner import start_coverage_refresh
    from app.scraper.loaders import sync_live_scores

    db = SessionLocal()
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
            .filter(
                func.date(Fixture.match_date) >= today,
                func.date(Fixture.match_date) <= coverage_horizon,
                Fixture.source != "coverage_seed",
            )
            .count()
        )
        all_future_count = (
            db.query(Fixture.id)
            .filter(func.date(Fixture.match_date) >= today, Fixture.source != "coverage_seed")
            .count()
        )

        # Never perform the expensive multi-provider fanout inside the cron HTTP
        # request. This prevents external cron services from timing out while the
        # worker continues ingestion in-process. The refresh itself fans out to
        # all configured API + public/web sources and then lets prediction work run.
        coverage_queued = start_coverage_refresh(reason="cron_wake")

        active_today = (
            db.query(Prediction.id)
            .join(Fixture, Prediction.fixture_id == Fixture.id)
            .filter(
                Prediction.is_published == True,
                Prediction.status == "active",
                func.date(Fixture.match_date) == today,
                Fixture.source != "coverage_seed",
            )
            .count()
        )

        prediction_queued = False
        if active_today == 0 and (all_future_count > 0 or upcoming_count > 0):
            prediction_queued = start_prediction_generation(reason="cron_wake_empty_board")

        return {
            "ok": True,
            "scores_synced": scores_synced,
            "coverage_refresh_queued": coverage_queued,
            "coverage_trigger": "cron_fanout",
            "existing_fixtures": all_future_count,
            "upcoming_48h": upcoming_count,
            "prediction_build_queued": prediction_queued,
        }
    except Exception as exc:
        log.exception("Wake endpoint failed")
        # Cron should only be marked failed for an authentication/routing error;
        # report transient application errors as a successful heartbeat so the
        # external scheduler keeps waking the Render service.
        return {"ok": False, "heartbeat": True, "error": str(exc)[:300]}
    finally:
        db.close()
