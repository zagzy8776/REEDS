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
    """Queue one coalesced coverage refresh without blocking HTTP."""
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
            report = run_lightweight_refresh()
            log.info(
                "Background coverage refresh complete: providers=%s coverage=%s purged_showcase=%s reason=%s",
                report.get("ingested", {}),
                report.get("coverage_recovery"),
                report.get("purged_showcase", 0),
                reason,
            )
        except Exception:
            log.exception("Background coverage refresh failed: reason=%s", reason)
        finally:
            _worker_lock.release()

    threading.Thread(target=worker, name="coverage-refresh", daemon=True).start()
    return True
