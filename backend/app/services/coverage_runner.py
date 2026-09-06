"""Background runner for recovering missing and low fixture coverage on Render."""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_last_started = 0.0
_COOLDOWN_SECONDS = 20 * 60


def start_coverage_refresh(reason: str = "unknown") -> bool:
    """Queue one lightweight/deep coverage refresh without blocking HTTP."""
    global _last_started

    now = time.monotonic()
    if now - _last_started < _COOLDOWN_SECONDS:
        log.info("Coverage refresh cooldown active; coalescing request (%s)", reason)
        return False
    if not _worker_lock.acquire(blocking=False):
        log.info("Coverage refresh already queued/running; coalescing request (%s)", reason)
        return False

    _last_started = now

    def worker() -> None:
        try:
            from app.services.scheduler import run_lightweight_refresh

            light_report = run_lightweight_refresh()
            deep_report = None
            try:
                from app.db.session import SessionLocal
                from app.scraper.deep_coverage import run_deep_coverage

                db = SessionLocal()
                try:
                    deep_report = run_deep_coverage(db)
                finally:
                    db.close()
            except Exception:
                log.exception("Deep fixture coverage failed: reason=%s", reason)

            log.info(
                "Background coverage refresh complete: lightweight=%s deep=%s reason=%s",
                light_report.get("ingested", {}),
                deep_report,
                reason,
            )
        except Exception:
            log.exception("Background coverage refresh failed: reason=%s", reason)
        finally:
            _worker_lock.release()

    threading.Thread(target=worker, name="coverage-refresh", daemon=True).start()
    return True
