"""Reliability guard for the prediction-generation pipeline.

Prediction generation is intentionally expensive. Several production paths can
request it (wake, scheduler, admin tools, and public self-heal). This module
installs one process-level lock plus a PostgreSQL advisory lock so those paths
cannot run the same generation job concurrently.
"""

import importlib
import logging
import threading
from functools import wraps

from sqlalchemy import text

log = logging.getLogger(__name__)

_PREDICTION_LOCK_KEY = 918273645
_process_lock = threading.Lock()
_installed = False
_install_lock = threading.Lock()


def _guarded(original):
    @wraps(original)
    def wrapper(db, *args, **kwargs):
        if not _process_lock.acquire(blocking=False):
            log.warning("Prediction generation skipped: another generation is already running")
            return 0

        lock_connection = None
        advisory_locked = False
        try:
            bind = db.get_bind()
            dialect = getattr(getattr(bind, "dialect", None), "name", "")
            if dialect == "postgresql":
                # Use a dedicated connection because Session.commit()/rollback()
                # releases the Session's connection back to the pool. A dedicated
                # connection keeps the session-level advisory lock alive for the
                # entire prediction job and lets us release it deterministically.
                lock_connection = bind.connect()
                advisory_locked = bool(
                    lock_connection.execute(
                        text("SELECT pg_try_advisory_lock(:lock_key)"),
                        {"lock_key": _PREDICTION_LOCK_KEY},
                    ).scalar()
                )
                if not advisory_locked:
                    log.warning("Prediction generation skipped: another worker holds the PostgreSQL lock")
                    return 0

            return original(db, *args, **kwargs)
        finally:
            if advisory_locked and lock_connection is not None:
                try:
                    lock_connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": _PREDICTION_LOCK_KEY},
                    )
                except Exception:
                    log.exception("Failed to release prediction advisory lock")
                finally:
                    lock_connection.close()
            elif lock_connection is not None:
                lock_connection.close()
            _process_lock.release()

    return wrapper


def install_prediction_guard() -> None:
    """Patch every known in-process prediction entry point exactly once."""
    global _installed
    with _install_lock:
        if _installed:
            return

        predictions = importlib.import_module("app.services.predictions")
        guarded = _guarded(predictions.generate_today_predictions)
        predictions.generate_today_predictions = guarded

        for module_name in (
            "app.api.public",
            "app.api.admin",
            "app.services.scheduler",
        ):
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, "generate_today_predictions"):
                    module.generate_today_predictions = guarded
            except Exception:
                log.exception("Could not patch prediction entry point: %s", module_name)

        _installed = True
        log.info("Prediction generation guard installed")
