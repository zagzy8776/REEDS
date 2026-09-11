import hashlib
import logging
import secrets
import threading
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import and_, text, func

from app.api import admin, public, live, fixtures, model_sync, ml_pipeline, ai_reads, community, analytics, fixture_cleanup
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
app.include_router(fixture_cleanup.router, prefix="/api/admin")
app.include_router(live.router, prefix="/api")
app.include_router(fixtures.router, prefix="/api")
app.include_router(model_sync.router)
app.include_router(ml_pipeline.router, prefix="/api")
app.include_router(ai_reads.router, prefix="/api")
app.include_router(community.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
