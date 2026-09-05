import logging
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import admin, public, live, model_sync
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
app.include_router(model_sync.router)


@app.on_event("startup")
def on_startup():
    init_db()
    try:
        from app.db.session import SessionLocal
        from app.services.model_bootstrap import install_quality_training, restore_missing_models
        install_quality_training()
        db = SessionLocal()
        try:
            result = restore_missing_models(db)
            log.info("Model bootstrap: %s", result)
        finally:
            db.close()
    except Exception:
        # Recovery must never prevent the HTTP process from starting.
        log.exception("Model bootstrap failed during startup")

    from app.services.prediction_guard import install_prediction_guard
    install_prediction_guard()

    if settings.enable_scheduler:
        from app.services.scheduler import start_scheduler
        start_scheduler()


@app.get("/health")
def health():
    """Cheap liveness probe. It intentionally does not require the database."""
    return {"ok": True, "brand": settings.public_brand_name}


@app.get("/api/health")
def api_health():
    return health()


@app.get("/ready")
def readiness():
    """Readiness probe: verify the application can reach its database."""
    from fastapi.responses import JSONResponse
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "ready": True, "database": "ok"}
    except Exception as exc:  # noqa: BLE001
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
    from app.api.public import fixtures_status
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        return fixtures_status(db=db)
    finally:
        db.close()


@app.get("/api/wake")
def wake(request: Request):
    """Cron keep-alive/sync endpoint with optional shared-secret protection."""
    if settings.cron_secret:
        supplied = request.headers.get("x-cron-secret", "")
        if not supplied:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
        if not supplied or not secrets.compare_digest(supplied, settings.cron_secret):
            raise HTTPException(status_code=401, detail="Invalid cron credential")

    from datetime import date
    from sqlalchemy import func
    from fastapi.responses import JSONResponse
    from app.db.models import Fixture, Prediction
    from app.db.session import SessionLocal
    from app.services.predictions import generate_today_predictions
    from app.scraper.loaders import sync_live_scores

    db = SessionLocal()
    generated = 0
    scores_synced = {}
    error = None
    try:
        scores_synced = sync_live_scores(
            db,
            settings.api_football_key or settings.api_sports_key,
            settings.api_basketball_key or settings.api_sports_key,
        )
        active_today = (
            db.query(Prediction)
            .join(Fixture, Prediction.fixture_id == Fixture.id)
            .filter(
                Prediction.is_published == True,
                Prediction.status == "active",
                func.date(Fixture.match_date) == date.today(),
            )
            .count()
        )
        if active_today == 0:
            generated = generate_today_predictions(db)
            log.info("Wake endpoint generated %d predictions", generated)
    except Exception as exc:
        error = str(exc)[:300]
        log.exception("Wake endpoint failed")
    finally:
        db.close()

    payload = {"ok": error is None, "scores_synced": scores_synced, "generated": generated}
    if error:
        payload["error"] = error
        return JSONResponse(status_code=503, content=payload)
    return payload
