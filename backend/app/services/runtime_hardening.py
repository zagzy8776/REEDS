"""Runtime safety wrappers for the multi-provider scheduler.

Keeps provider failures isolated and prevents a bad transaction or optional
provider from poisoning the rest of the refresh cycle.
"""
from __future__ import annotations

from functools import wraps
import logging

log = logging.getLogger(__name__)


def install_provider_runtime_hardening() -> None:
    try:
        import app.services.scheduler as scheduler
        import app.scraper.deep_coverage as deep
        import app.scraper.loaders as loaders

        original_upsert = getattr(loaders, "upsert_fixture", None)
        if original_upsert and not getattr(original_upsert, "_reeds_hardened", False):
            def hardened_upsert(db, fixture):
                try:
                    return original_upsert(db, fixture)
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        log.exception("Fixture rollback failed")
                    raise
            hardened_upsert._reeds_hardened = True
            loaders.upsert_fixture = hardened_upsert
            if hasattr(scheduler, "upsert_fixture"):
                scheduler.upsert_fixture = hardened_upsert
            if hasattr(deep, "upsert_fixture"):
                deep.upsert_fixture = hardened_upsert

        def wrap_module_fn(module, name: str):
            current = getattr(module, name, None)
            if current is None or getattr(current, "_reeds_provider_hardened", False):
                return
            @wraps(current)
            def wrapped(*args, **kwargs):
                try:
                    return current(*args, **kwargs)
                except Exception:
                    db = args[0] if args and hasattr(args[0], "rollback") else None
                    if db is not None:
                        try:
                            db.rollback()
                        except Exception:
                            log.exception("Provider rollback failed: %s", name)
                    raise
            wrapped._reeds_provider_hardened = True
            setattr(module, name, wrapped)
            if module is scheduler:
                return

        provider_names = [
            "ingest_api_football_fixtures",
            "ingest_sportmonks_football_fixtures",
            "ingest_football_data_org_matches",
            "ingest_apifootball_com_events",
            "ingest_bzzoiro_football",
            "ingest_openfoot_football",
            "ingest_api_basketball_games",
            "ingest_allsportsapi_events",
            "ingest_thesportsdb_events",
            "ingest_web_score_sources",
        ]
        for name in provider_names:
            wrap_module_fn(scheduler, name)

        # Deep coverage historically had a second, unauthenticated OpenFoot path.
        # Replace that path with the credential-aware loader already used by the
        # normal scheduler.
        current_openfoot = getattr(deep, "_ingest_openfoot", None)
        if current_openfoot and not getattr(current_openfoot, "_reeds_openfoot_safe", False):
            from app.scraper.coverage_sources import ingest_openfoot_football
            def safe_openfoot(db, dates):
                from app.core.config import get_settings
                key = get_settings().openfoot_api_key
                if not key:
                    log.info("OpenFoot skipped: API key not configured")
                    return 0
                return ingest_openfoot_football(db, key, dates)
            safe_openfoot._reeds_openfoot_safe = True
            deep._ingest_openfoot = safe_openfoot

        # Patch the deep-coverage runner's global reference as well.
        if hasattr(deep, "_ingest_openfoot"):
            deep._ingest_openfoot = getattr(deep, "_ingest_openfoot")

        log.info("Runtime provider hardening installed")
    except Exception:
        log.exception("Runtime provider hardening installation failed")
