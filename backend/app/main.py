import hashlib
import logging
import secrets
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import and_, text, func

from app.api import admin, public, live, fixtures, model_sync, ml_pipeline, ai_reads
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
    allow_headers=["*"] ,
)
app.include_router(public.router, prefix="/api")
app.include_router(admin.router, prefix="/api/admin")
app.include_router(live.router, prefix="/api")
app.include_router(fixtures.router, prefix="/api")
app.include_router(model_sync.router)
app.include_router(ml_pipeline.router, prefix="/api")
app.include_router(ai_reads.router, prefix="/api")


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


@app.get("/api/admin/cron-diagnostics", dependencies=[__import__("fastapi").Depends(admin.require_admin)])
def cron_diagnostics():
    """Non-secret diagnostic for comparing the running cron credential."""
    value = (settings.cron_secret or "").strip()
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""
    return {
        "configured": bool(value),
        "length": len(value),
        "sha256_prefix": digest,
    }


@app.get("/ready")
def readiness():
    from fastapi.responses import JSONResponse
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "ready": True, "database": "ok"}
    except Exception as exc:
        log.exception("Readiness database check failed")
        return JSONResponse(status_code=503, content={"ok": False, "ready": False, "database": "error", "detail": str(exc)[:200]})


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


@app.get("/api/stats/backtest")
def api_stats_backtest():
    """Fast read-only model diagnostics: only latest model per sport + recent backtests."""
    from app.db.session import SessionLocal
    from app.db.models import ModelVersion, BacktestRun

    db = SessionLocal()
    try:
        latest_times = (
            db.query(
                ModelVersion.sport.label("sport"),
                func.max(ModelVersion.trained_at).label("trained_at"),
            )
            .group_by(ModelVersion.sport)
            .subquery()
        )
        model_rows = (
            db.query(ModelVersion)
            .join(
                latest_times,
                and_(
                    ModelVersion.sport == latest_times.c.sport,
                    ModelVersion.trained_at == latest_times.c.trained_at,
                ),
            )
            .order_by(ModelVersion.sport.asc())
            .limit(24)
            .all()
        )

        backtests = (
            db.query(BacktestRun)
            .order_by(BacktestRun.created_at.desc())
            .limit(20)
            .all()
        )
        return {
            "models": [
                {
                    "id": model.id,
                    "sport": model.sport,
                    "type": model.model_type,
                    "sample_size": model.sample_size,
                    "accuracy": model.accuracy,
                    "active": model.is_active,
                    "trained_at": model.trained_at,
                }
                for model in model_rows
            ],
            "backtests": [
                {
                    "id": run.id,
                    "sport": run.sport,
                    "model_type": run.model_type,
                    "sample_size": run.sample_size,
                    "accuracy": run.accuracy,
                    "brier_score": run.brier_score,
                    "log_loss": run.log_loss,
                    "created_at": run.created_at,
                }
                for run in backtests
            ],
        }
    except Exception as exc:
        log.exception("Model status endpoint failed")
        raise HTTPException(status_code=503, detail="Model status unavailable") from exc
    finally:
        db.close()


@app.get("/api/wake")
def wake(request: Request):
    """Fast authenticated cron heartbeat; enqueue all fixture/model work."""
    if settings.cron_secret:
        supplied = request.headers.get("x-cron-secret", "")
        if not supplied:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:]
        supplied = supplied.strip()
        expected = (settings.cron_secret or "").strip()
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Invalid cron credential")

    from datetime import date
    from app.db.models import Fixture
    from app.db.session import SessionLocal
    from app.services.coverage_runner import start_coverage_refresh

    db = SessionLocal()
    try:
        today = date.today()
        future_count = (
            db.query(Fixture.id)
            .filter(func.date(Fixture.match_date) >= today, Fixture.source != "coverage_seed")
            .count()
        )
        queued = start_coverage_refresh(reason="cron_wake")
        return {"ok": True, "heartbeat": True, "coverage_refresh_queued": queued, "existing_fixtures": future_count}
    except Exception as exc:
        log.exception("Wake endpoint failed")
        return {"ok": False, "heartbeat": True, "error": str(exc)[:300]}
    finally:
        db.close()
