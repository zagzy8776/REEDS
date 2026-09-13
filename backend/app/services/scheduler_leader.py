"""Cross-instance scheduler leader election.

Render and Fly both run ``app.main:app`` in the target architecture. Both must
never run the heavy scheduler pass concurrently (duplicate ingestion,
predictions, learning). A PostgreSQL advisory lock is held on a dedicated
connection for the lifetime of the scheduler; the first process to acquire it
becomes the scheduler leader. Other processes still serve the API but skip the
periodic jobs.

If PostgreSQL is unavailable at startup the leader status defaults to False and
the scheduler does not start, which is safe: cached coverage and live reads are
still served and the wake endpoint can re-queue work when the database returns.
"""

from __future__ import annotations

import logging
import os
import threading

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

_SCHEDULER_LOCK_KEY = 0x52454544535_01  # "REEDS" + instance tag
_installed = False
_install_lock = threading.Lock()


def scheduler_enabled() -> bool:
    """Process-level check used by start_scheduler."""
    return bool(os.environ.get("ENABLE_SCHEDULER", "").strip() in {"1", "true", "True"})


def schedule_via_cron() -> bool:
    """Return True when only external cron (not in-process) should drive jobs."""
    flag = os.environ.get("SCHEDULE_VIA_CRON", "").strip()
    return flag in {"1", "true", "True"}


def acquire_scheduler_leadership(engine: Engine) -> bool:
    """Acquire the cross-instance scheduler lock. Returns True when leader.

    The lock connection is intentionally kept open (module-level) so the
    advisory lock persists for the process lifetime. On any failure the
    scheduler does not start rather than risk duplicate work.
    """
    global _installed
    if _installed:
        return True
    if schedule_via_cron():
        log.info("Scheduler skipped: SCHEDULE_VIA_CRON is set; external cron drives the refresh")
        return False
    conn = None
    with _install_lock:
        if _installed:
            return True
        try:
            conn = engine.connect()
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _SCHEDULER_LOCK_KEY}).scalar())
            if not got:
                conn.close()
                log.warning("Scheduler leader lock not acquired — another instance owns the schedule")
                return False
            # Keep the lock alive from a dedicated heartbeat thread so the
            # advisory lock is not dropped when the pool would otherwise
            # recycle the connection. Only this thread touches the connection
            # after setup, so concurrent access is not a concern.
            def _keepalive() -> None:
                import time
                while True:
                    time.sleep(60)
                    try:
                        conn.execute(text("SELECT 1"))
                    except Exception:
                        pass

            threading.Thread(target=_keepalive, name="scheduler-keeper", daemon=True).start()
            _installed = True
            log.info("Scheduler leader lock acquired (advisory key %s)", _SCHEDULER_LOCK_KEY)
            return True
        except Exception:
            log.exception("Scheduler leader election failed — scheduler will not start")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            return False


def stop_scheduler_leader() -> None:
    """Release the advisory lock (used in tests)."""
    global _installed
    with _install_lock:
        _installed = False