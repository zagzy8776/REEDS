"""Production resource and ingestion guards for the small Render instance.

Render's free Python instance is memory constrained, so prediction refreshes
must not materialize the entire historical database. This module also makes
provider sport metadata authoritative and rejects obviously malformed fixture
rows before they can pollute predictions or future training runs.
"""
from __future__ import annotations

import gc
import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func

from app.db.models import Fixture

log = logging.getLogger(__name__)

MAX_PREDICTION_HISTORY_ROWS = 40_000

_PROVIDER_SPORT_MAP = {
    "football": "soccer",
    "soccer": "soccer",
    "basketball": "basketball",
    "tennis": "tennis",
    "cricket": "cricket",
    "hockey": "hockey",
    "ice hockey": "hockey",
    "baseball": "baseball",
    "american-football": "american_football",
    "american football": "american_football",
    "volleyball": "volleyball",
    "handball": "handball",
    "rugby": "rugby",
    "motorsport": "motorsport",
    "fighting": "mma",
}


def _provider_sport(extra: object) -> str | None:
    if not isinstance(extra, dict):
        return None
    raw = extra.get("provider_sport")
    if raw is None:
        return None
    return _PROVIDER_SPORT_MAP.get(str(raw).strip().lower())


def _looks_malformed(fixture: Fixture) -> bool:
    """Reject payloads that clearly contain multiple fixtures in one team field."""
    home = str(fixture.home_team or "").strip()
    away = str(fixture.away_team or "").strip()
    if not home or not away or len(home) > 120 or len(away) > 120:
        return True
    combined = f"{home} {away}".lower()
    # These patterns indicate a provider returned a flattened list of matches
    # rather than one event. A normal team name may contain a single hyphen.
    if combined.count(" vs ") >= 2 or combined.count(" vs. ") >= 2:
        return True
    if combined.count(" - ") >= 4:
        return True
    return False


def _validate_fixture(fixture: Fixture) -> None:
    provider_sport = _provider_sport(fixture.extra)
    if provider_sport and provider_sport != str(fixture.sport or "").strip().lower():
        log.warning(
            "Correcting provider sport mismatch: provider=%s stored=%s fixture=%s",
            provider_sport,
            fixture.sport,
            fixture.id,
        )
        fixture.sport = provider_sport
    if _looks_malformed(fixture):
        raise ValueError("malformed fixture payload: team fields contain invalid combined-event data")


def bounded_prediction_history(db, max_age_days: int | None = 180) -> pd.DataFrame:
    """Load only completed, point-in-time-safe history needed by predictions."""
    rows = db.query(Fixture)
    if max_age_days is not None:
        cutoff = date.today() - timedelta(days=max_age_days)
        rows = rows.filter(func.date(Fixture.match_date) >= cutoff)
    rows = (
        rows.filter(Fixture.home_score.isnot(None), Fixture.away_score.isnot(None))
        .order_by(Fixture.match_date.desc(), Fixture.id.desc())
        .limit(MAX_PREDICTION_HISTORY_ROWS)
        .all()
    )
    frame = pd.DataFrame(
        [
            {
                "id": r.id,
                "sport": r.sport,
                "league": r.league,
                "season": r.season,
                "match_date": r.match_date,
                "home_team": r.home_team,
                "away_team": r.away_team,
                "home_score": r.home_score,
                "away_score": r.away_score,
                "home_odds": r.home_odds,
                "draw_odds": r.draw_odds,
                "away_odds": r.away_odds,
            }
            for r in rows
        ]
    )
    log.info("Prediction history bounded to %s completed rows", f"{len(frame):,}")
    return frame


def _wrap_upsert(module, name: str, original):
    if original is None or getattr(original, "_reeds_resource_guard", False):
        return original

    def guarded(db, fixture, *args, **kwargs):
        _validate_fixture(fixture)
        return original(db, fixture, *args, **kwargs)

    guarded.__name__ = getattr(original, "__name__", name)
    guarded.__doc__ = getattr(original, "__doc__", None)
    guarded._reeds_resource_guard = True
    setattr(module, name, guarded)
    return guarded


def _repair_existing_provider_mismatches(db) -> int:
    """Repair rows already polluted by the old league-based inference bug."""
    repaired = 0
    rows = (
        db.query(Fixture)
        .filter(Fixture.source.in_(["allsportsapi", "thesportsdb"]))
        .order_by(Fixture.id.desc())
        .limit(10_000)
        .all()
    )
    for fixture in rows:
        provider_sport = _provider_sport(fixture.extra)
        if provider_sport and provider_sport != str(fixture.sport or "").strip().lower():
            fixture.sport = provider_sport
            repaired += 1
    if repaired:
        db.commit()
        log.warning("Repaired %s existing provider sport mismatches", repaired)
    return repaired


def install_resource_guards() -> None:
    """Install lightweight memory and provider-data safeguards once per process."""
    try:
        import app.services.predictions as predictions
        predictions.dataframe_from_db = bounded_prediction_history

        import app.scraper.loaders as loaders
        guarded = _wrap_upsert(loaders, "upsert_fixture", getattr(loaders, "upsert_fixture", None))

        # Some modules imported upsert_fixture directly; replace those references too.
        for module_name in ("app.services.scheduler", "app.scraper.deep_coverage"):
            try:
                module = __import__(module_name, fromlist=["upsert_fixture"])
                if guarded is not None:
                    setattr(module, "upsert_fixture", guarded)
            except Exception:
                log.exception("Could not patch fixture writer: %s", module_name)

        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            _repair_existing_provider_mismatches(db)
        finally:
            db.close()

        log.info(
            "Resource guards installed: prediction history <= %s rows; provider sport authoritative",
            f"{MAX_PREDICTION_HISTORY_ROWS:,}",
        )
    except Exception:
        log.exception("Could not install resource guards")


def collect_after_prediction_job() -> None:
    """Release transient pandas/model objects after scheduled prediction work."""
    collected = gc.collect()
    log.info("Prediction job garbage collection completed: %s objects", collected)
