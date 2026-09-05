"""Background prediction runner for keeping HTTP requests fast and reliable."""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_worker_lock = threading.Lock()


def start_prediction_generation(reason: str = "unknown") -> bool:
    """Start one detached prediction build if this process is not already running.

    A fresh SQLAlchemy session is created inside the worker. Request-scoped sessions
    must never be reused after an HTTP response, and the prediction guard provides
    the cross-process PostgreSQL lock when multiple Render instances exist.
    """
    if not _worker_lock.acquire(blocking=False):
        log.info("Prediction build already queued/running; coalescing request (%s)", reason)
        return False

    def worker() -> None:
        try:
            from app.db.session import SessionLocal
            from app.services.predictions import generate_today_predictions

            db = SessionLocal()
            try:
                generated = generate_today_predictions(db)
                log.info("Background prediction build complete: generated=%d reason=%s", generated, reason)
            finally:
                db.close()
        except Exception:
            log.exception("Background prediction build failed: reason=%s", reason)
        finally:
            _worker_lock.release()

    threading.Thread(
        target=worker,
        name="prediction-builder",
        daemon=True,
    ).start()
    return True
