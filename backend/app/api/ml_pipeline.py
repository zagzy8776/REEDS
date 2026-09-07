"""Private ML pipeline endpoints used by the Hugging Face worker."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Fixture
from app.db.session import get_db

log = logging.getLogger(__name__)
router = APIRouter()


def _require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.admin_api_key or not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin credential")


def _history_dates(days_back: int) -> list[str]:
    days_back = max(1, min(int(days_back), 14))
    # Include yesterday through the requested historical window. Today is
    # deliberately excluded so this endpoint enriches completed API history,
    # not the live fixture board.
    return [
        (date.today() - timedelta(days=offset)).isoformat()
        for offset in range(1, days_back + 1)
    ][::-1]


@router.post("/admin/ml/sync-provider-history")
def sync_provider_history(
    days_back: int = 3,
    _: None = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Pull recent completed provider data into Neon for ML training.

    Render owns provider credentials and network access. HF asks Render to run
    this sync, then trains from the resulting Neon dataset. Providers are
    isolated so one failed quota/key does not prevent the others from loading.
    """
    settings = get_settings()
    dates = _history_dates(days_back)
    report: dict[str, object] = {
        "dates": dates,
        "providers": {},
        "errors": [],
    }

    from app.scraper.loaders import (
        ingest_allsportsapi_events,
        ingest_api_basketball_games,
        ingest_api_football_fixtures,
        ingest_apifootball_com_events,
        ingest_football_data_org_matches,
        ingest_sportmonks_football_fixtures,
        ingest_thesportsdb_events,
    )
    from app.scraper.coverage_sources import ingest_bzzoiro_football, ingest_openfoot_football

    football_key = settings.api_football_key or settings.api_sports_key
    basketball_key = settings.api_basketball_key or settings.api_sports_key
    providers = []

    if football_key:
        providers.append(("api_football", ingest_api_football_fixtures, (db, football_key, dates), {}))
    if settings.sportmonks_api_key:
        providers.append(("sportmonks", ingest_sportmonks_football_fixtures, (db, settings.sportmonks_api_key, dates), {}))
    if settings.football_data_api_key:
        providers.append(("football_data_org", ingest_football_data_org_matches, (db, settings.football_data_api_key, dates), {}))
    if settings.api_football_com_key:
        providers.append(("apifootball_com", ingest_apifootball_com_events, (db, settings.api_football_com_key, dates), {}))
    if settings.bzzoiro_api_key:
        providers.append(("bzzoiro", ingest_bzzoiro_football, (db, settings.bzzoiro_api_key, dates), {}))
    providers.append(("openfoot", ingest_openfoot_football, (db, settings.openfoot_api_key or None, dates), {}))
    if basketball_key:
        providers.append(("basketball", ingest_api_basketball_games, (db, basketball_key, dates), {}))
    if settings.allsportsapi_key:
        providers.append(("allsportsapi", ingest_allsportsapi_events, (db, settings.allsportsapi_key, dates, settings.allsportsapi_sport_list), {}))
    if settings.thesportsdb_enabled:
        providers.append((
            "thesportsdb",
            ingest_thesportsdb_events,
            (db, settings.thesportsdb_api_key, dates, settings.thesportsdb_sport_list, min(settings.thesportsdb_max_calls, 30)),
            {},
        ))

    for name, fn, args, kwargs in providers:
        try:
            value = fn(*args, **kwargs)
            report["providers"][name] = int(value or 0)
        except Exception as exc:
            db.rollback()
            report["providers"][name] = 0
            report["errors"].append({"provider": name, "error": str(exc)[:300]})
            log.exception("Historical ML provider sync failed: %s", name)

    # Return auditable source/sport counts after all isolated providers run.
    cutoff = date.today() - timedelta(days=max(1, min(int(days_back), 14)))
    completed_base = db.query(Fixture).filter(
        Fixture.match_date >= cutoff,
        Fixture.match_date < date.today(),
        Fixture.home_score.isnot(None),
        Fixture.away_score.isnot(None),
        Fixture.source != "coverage_seed",
    )
    report["completed_rows"] = completed_base.count()
    report["by_source"] = {
        source: count
        for source, count in db.query(Fixture.source, func.count(Fixture.id))
        .filter(
            Fixture.match_date >= cutoff,
            Fixture.match_date < date.today(),
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
            Fixture.source != "coverage_seed",
        )
        .group_by(Fixture.source)
        .all()
    }
    report["by_sport"] = {
        sport: count
        for sport, count in db.query(Fixture.sport, func.count(Fixture.id))
        .filter(
            Fixture.match_date >= cutoff,
            Fixture.match_date < date.today(),
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
            Fixture.source != "coverage_seed",
        )
        .group_by(Fixture.sport)
        .all()
    }
    return report
