import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, public
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import init_db


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


@app.on_event("startup")
def on_startup():
    init_db()
    if settings.enable_scheduler:
        from app.services.scheduler import start_scheduler

        start_scheduler()


@app.get("/health")
def health():
    return {"ok": True, "brand": settings.public_brand_name}


@app.get("/api/health")
def api_health():
    return health()


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
def wake():
    """Cron-job.org keep-alive ping. Keeps Render from sleeping, auto-generates
    predictions if today's board is empty, and syncs live scores."""
    from datetime import date
    from sqlalchemy import func
    from app.db.models import Fixture, Prediction
    from app.db.session import SessionLocal
    from app.services.predictions import generate_today_predictions
    from app.scraper.loaders import sync_live_scores
    from app.core.config import get_settings

    db = SessionLocal()
    generated = 0
    scores_synced = {}
    try:
        # Sync live/finished scores first
        s = get_settings()
        scores_synced = sync_live_scores(
            db,
            s.api_football_key or s.api_sports_key,
            s.api_basketball_key or s.api_sports_key,
        )
        # Generate predictions if board is empty
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
    except Exception:
        log.exception("Wake endpoint failed")
    finally:
        db.close()
    return {"ok": True, "scores_synced": scores_synced, "generated": generated}
