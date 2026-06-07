from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, public
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import init_db


setup_logging()
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
