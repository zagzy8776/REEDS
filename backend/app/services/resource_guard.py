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
from functools import wraps

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
    if combined.count(" vs ") >= 2 or combined.count(" vs. ") >= 2:
        return True
    if combined.count(" - ") >= 4:
        return True
    return False


def _validate_fixture(fixture: Fixture) -> None:
    if _looks_malformed(fixture):
        raise ValueError("malformed fixture payload: team fields contain invalid combined-event data")


def _apply_provider_sport(fixture: Fixture, provider_sport: str | None) -> None:
    """Apply authoritative provider sport after conflict checks have been made."""
    if provider_sport and provider_sport != str(fixture.sport or "").strip().lower():
        log.warning(
            "Correcting provider sport mismatch: provider=%s stored=%s fixture=%s",
            provider_sport,
            fixture.sport,
            fixture.id,
        )
        fixture.sport = provider_sport


def _provider_target_exists(db, fixture: Fixture, provider_sport: str | None) -> bool:
    """Return whether changing sport would collide with an existing unique fixture."""
    if not provider_sport or provider_sport == str(fixture.sport or "").strip().lower():
        return False
    query = db.query(Fixture.id).filter(
        Fixture.sport == provider_sport,
        Fixture.league == fixture.league,
        Fixture.match_date == fixture.match_date,
        Fixture.home_team == fixture.home_team,
        Fixture.away_team == fixture.away_team,
    )
    if fixture.id is not None:
        query = query.filter(Fixture.id != fixture.id)
    return query.first() is not None


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

    @wraps(original)
    def guarded(db, fixture, *args, **kwargs):
        _validate_fixture(fixture)
        provider_sport = _provider_sport(fixture.extra)
        if _provider_target_exists(db, fixture, provider_sport):
            log.info(
                "Provider sport correction skipped because canonical fixture already exists: "
                "provider=%s stored=%s fixture=%s",
                provider_sport,
                fixture.sport,
                fixture.id,
            )
            _apply_provider_sport(fixture, provider_sport)
            return original(db, fixture, *args, **kwargs)
        _apply_provider_sport(fixture, provider_sport)
        return original(db, fixture, *args, **kwargs)

    guarded._reeds_resource_guard = True
    setattr(module, name, guarded)
    return guarded


def _repair_existing_provider_mismatches(db) -> int:
    """Repair recent rows polluted by the old league-based inference bug.

    A sport change is only applied when the destination unique key is free.
    If the canonical row already exists, keep both rows untouched rather than
    turning a data-cleanup pass into a transaction-breaking merge operation.
    """
    repaired = 0
    skipped_conflicts = 0
    rows = (
        db.query(Fixture)
        .filter(Fixture.source.in_(["allsportsapi", "thesportsdb"]))
        .order_by(Fixture.id.desc())
        .limit(3_000)
        .all()
    )
    for fixture in rows:
        provider_sport = _provider_sport(fixture.extra)
        if not provider_sport or provider_sport == str(fixture.sport or "").strip().lower():
            continue
        if _provider_target_exists(db, fixture, provider_sport):
            skipped_conflicts += 1
            continue
        fixture.sport = provider_sport
        repaired += 1
    if repaired:
        db.commit()
    else:
        db.rollback()
    log.info(
        "Provider sport repair completed: repaired=%s skipped_unique_conflicts=%s",
        repaired,
        skipped_conflicts,
    )
    return repaired


def _install_prediction_gc(predictions) -> None:
    original = getattr(predictions, "generate_today_predictions", None)
    if original is None or getattr(original, "_reeds_gc_guard", False):
        return

    @wraps(original)
    def guarded_generation(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            collected = gc.collect()
            log.info("Prediction job garbage collection completed: %s objects", collected)

    guarded_generation._reeds_gc_guard = True
    predictions.generate_today_predictions = guarded_generation


def install_resource_guards() -> None:
    """Install lightweight memory and provider-data safeguards once per process."""
    try:
        import app.services.predictions as predictions
        predictions.dataframe_from_db = bounded_prediction_history
        _install_prediction_gc(predictions)

        import app.scraper.loaders as loaders
        _wrap_upsert(loaders, "upsert_fixture", getattr(loaders, "upsert_fixture", None))

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
