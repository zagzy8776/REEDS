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
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_cors_origins, allow_credentials=True, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])
app.include_router(public.router, prefix="/api")
app.include_router(admin.router, prefix="/api/admin")
app.include_router(live.router, prefix="/api")
app.include_router(fixtures.router, prefix="/api")
app.include_router(model_sync.router)
app.include_router(ml_pipeline.router, prefix="/api")
app.include_router(ai_reads.router, prefix="/api")


_startup_db_ready = False
_startup_db_lock = threading.Lock()


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


def _finish_startup_once() -> None:
    """Install DB-dependent guards + scheduler after the database returns."""
    global _startup_db_ready
    if _startup_db_ready:
        return
    if not _startup_db_lock.acquire(blocking=False):
        return
    try:
        if not _check_database_ready():
            log.warning("Database still unavailable; startup completion deferred")
            return
        from app.services.resource_guard import install_resource_guards
        try:
            install_resource_guards()
        except Exception:
            log.exception("Could not install resource guards")
        from app.services.runtime_hardening import install_provider_runtime_hardening
        try:
            install_provider_runtime_hardening()
        except Exception:
            log.exception("Could not install provider runtime hardening")
        from app.services.prediction_guard import install_prediction_guard
        install_prediction_guard()
        if settings.enable_scheduler:
            from app.services.scheduler import start_scheduler
            start_scheduler()
        _startup_db_ready = True
    finally:
        _startup_db_lock.release()


def _check_database_ready() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        log.warning("Explicit database connectivity check failed")
        return False


def _database_recovery_loop() -> None:
    """Keep the API alive and retry bounded startup steps while Postgres is down."""
    import time
    while True:
        time.sleep(120)
        try:
            _finish_startup_once()
        except Exception:
            log.exception("Deferred startup completion attempt failed")


@app.on_event("startup")
def on_startup():
    global _startup_db_ready
    """Start the API even when PostgreSQL is temporarily unavailable.

    A bounded number of init attempts happen inline; if they fail the process
    still serves /health and public reads, and a background recovery thread
    completes guards/scheduler once the database returns. Optional providers
    and the GitHub release API are never fatal to startup.
    """
    try:
        init_db()
        _startup_db_ready = True
    except Exception:
        log.exception("Database initialization failed; continuing degraded and retrying in the background")
    finally:
        if not _startup_db_ready:
            threading.Thread(target=_database_recovery_loop, name="db-recovery", daemon=True).start()
    threading.Thread(target=_bootstrap_models_background, name="model-bootstrap", daemon=True).start()
    if _startup_db_ready:
        _finish_startup_once()
    else:
        log.warning("Startup deferred until the database is reachable (/ready will report database status)")


@app.get("/health")
def health():
    return {"ok": True, "brand": settings.public_brand_name}


@app.get("/api/health")
def api_health():
    return health()


@app.get("/api/admin/cron-diagnostics", dependencies=[__import__("fastapi").Depends(admin.require_admin)])
def cron_diagnostics():
    value = (settings.cron_secret or "").strip()
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""
    return {"configured": bool(value), "length": len(value), "sha256_prefix": digest}


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
    from app.db.session import SessionLocal
    from app.db.models import ModelVersion, BacktestRun
    db = SessionLocal()
    try:
        latest_times = db.query(ModelVersion.sport.label("sport"), func.max(ModelVersion.trained_at).label("trained_at")).group_by(ModelVersion.sport).subquery()
        model_rows = db.query(ModelVersion).join(latest_times, and_(ModelVersion.sport == latest_times.c.sport, ModelVersion.trained_at == latest_times.c.trained_at)).order_by(ModelVersion.sport.asc()).limit(24).all()
        backtests = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(20).all()
        return {"models": [{"id": m.id, "sport": m.sport, "type": m.model_type, "sample_size": m.sample_size, "accuracy": m.accuracy, "active": m.is_active, "trained_at": m.trained_at} for m in model_rows], "backtests": [{"id": r.id, "sport": r.sport, "model_type": r.model_type, "sample_size": r.sample_size, "accuracy": r.accuracy, "brier_score": r.brier_score, "log_loss": r.log_loss, "created_at": r.created_at} for r in backtests]}
    except Exception as exc:
        log.exception("Model status endpoint failed")
        raise HTTPException(status_code=503, detail="Model status unavailable") from exc
    finally:
        db.close()


@app.get("/api/stats/ai-learning")
def api_stats_ai_learning():
    from app.db.session import SessionLocal
    from app.services.prediction_learning import learning_summary, settle_prediction_outcomes
    db = SessionLocal()
    try:
        settlement = settle_prediction_outcomes(db, lookback_days=90)
        db.commit()
        return {"ok": True, "settlement": settlement, **learning_summary(db)}
    except Exception as exc:
        db.rollback()
        log.exception("AI learning diagnostics failed")
        raise HTTPException(status_code=503, detail="AI learning diagnostics unavailable") from exc
    finally:
        db.close()


@app.get("/api/stats/market-gate")
def api_stats_market_gate():
    """Empirical market-level publication evidence (non-secret)."""
    from app.db.session import SessionLocal
    from app.services.market_gate import market_evidence_summary
    from app.services.redis_cache import cache_get_or_set

    def _build() -> dict:
        db = SessionLocal()
        try:
            return market_evidence_summary(db)
        finally:
            db.close()

    try:
        return cache_get_or_set("stats:market-gate:v1", 180, _build)
    except Exception as exc:
        log.exception("Market evidence statistics failed")
        raise HTTPException(status_code=503, detail="Market evidence unavailable") from exc


@app.get("/api/wake")
def wake(request: Request):
    expected = (settings.cron_secret or "").strip()
    supplied_cron = request.headers.get("x-cron-secret", "").strip()
    admin_supplied = request.headers.get("x-admin-key", "").strip()
    auth = request.headers.get("authorization", "")
    if not supplied_cron and auth.lower().startswith("bearer "):
        supplied_cron = auth[7:].strip()
    auth_method = "none"
    if expected:
        if supplied_cron and secrets.compare_digest(supplied_cron, expected):
            auth_method = "cron_secret"
        else:
            admin_expected = (settings.admin_api_key or "").strip()
            if not admin_expected or not admin_supplied or not secrets.compare_digest(admin_supplied, admin_expected):
                raise HTTPException(status_code=401, detail="Invalid wake credential")
            auth_method = "admin_api_key_fallback"
    elif settings.app_env == "production":
        admin_expected = (settings.admin_api_key or "").strip()
        if not admin_expected or not admin_supplied or not secrets.compare_digest(admin_supplied, admin_expected):
            raise HTTPException(status_code=401, detail="Wake authentication is not safely configured")
        auth_method = "admin_api_key"
    from datetime import date
    from app.db.models import Fixture
    from app.db.session import SessionLocal
    from app.services.coverage_runner import start_coverage_refresh
    db = SessionLocal()
    try:
        today = date.today()
        future_count = db.query(Fixture.id).filter(func.date(Fixture.match_date) >= today, Fixture.source != "coverage_seed").count()
        queued = start_coverage_refresh(reason="cron_wake")
        return {"ok": True, "heartbeat": True, "auth_method": auth_method, "coverage_refresh_queued": queued, "existing_fixtures": future_count}
    except Exception as exc:
        log.exception("Wake endpoint failed")
        raise HTTPException(status_code=503, detail="Wake failed") from exc
    finally:
        db.close()
