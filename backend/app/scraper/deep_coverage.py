"""Demand-driven fixture coverage expansion.

The normal Render scheduler is intentionally lightweight. This module is the
coverage escalator: when the live board is genuinely thin, it fans out across
ranged/public providers and persists the result to the same Neon fixtures table.
No synthetic fixtures are used to satisfy the coverage target.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Fixture, Prediction
from app.scraper.loaders import (
    ingest_allsportsapi_events,
    ingest_apifootball_com_events,
    ingest_football_data_org_matches,
    ingest_thesportsdb_events,
    upsert_fixture,
)
from app.services.data_quality import resolve_team_name
from app.services.public_football_sources import (
    ingest_fixture_download_football,
    ingest_sporting_events_football,
)

log = logging.getLogger(__name__)

BZZOIRO_BASE = "https://sports.bzzoiro.com/api/v2"
OPENFOOT_BASE = "https://openfootapi.com/v1"
SPORTMONKS_BASE = "https://api.sportmonks.com/v3/football"

MIN_COVERAGE = 300
WINDOW_DAYS = 7


def _window(days: int = WINDOW_DAYS) -> tuple[str, str, list[str]]:
    today = date.today()
    end = today + timedelta(days=max(days - 1, 0))
    dates = [(today + timedelta(days=i)).isoformat() for i in range(max(days, 1))]
    return today.isoformat(), end.isoformat(), dates


def _int_or_none(value):
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _score(item: dict, side: str):
    score = item.get("score") or {}
    if isinstance(score, dict):
        nested = score.get(side)
        if isinstance(nested, dict):
            for key in ("goals", "points", "score", "total"):
                parsed = _int_or_none(nested.get(key))
                if parsed is not None:
                    return parsed
        elif nested is not None:
            parsed = _int_or_none(nested)
            if parsed is not None:
                return parsed
        for key in (f"{side}_score", f"{side}Score", f"{side}_goals"):
            parsed = _int_or_none(score.get(key))
            if parsed is not None:
                return parsed
    for key in (f"{side}_score", f"{side}Score", f"{side}_goals"):
        parsed = _int_or_none(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _future_count(db: Session, today: date | None = None) -> int:
    today = today or date.today()
    return int(
        db.query(func.count(Fixture.id))
        .filter(Fixture.match_date >= today, Fixture.source != "coverage_seed")
        .scalar()
        or 0
    )


def purge_showcase_rows(db: Session) -> int:
    """Remove old synthetic showcase fixtures and their predictions."""

    seed_ids = [row[0] for row in db.query(Fixture.id).filter(Fixture.source == "coverage_seed").all()]
    if not seed_ids:
        return 0
    db.query(Prediction).filter(Prediction.fixture_id.in_(seed_ids)).delete(synchronize_session=False)
    deleted = (
        db.query(Fixture)
        .filter(Fixture.id.in_(seed_ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted or 0)


def _ingest_sportmonks(db: Session, token: str, start_date: str, end_date: str) -> int:
    """Use one SportMonks ranged fixture request for the whole coverage window."""

    response = requests.get(
        f"{SPORTMONKS_BASE}/fixtures/between/{start_date}/{end_date}",
        params={"api_token": token, "include": "participants;scores;league"},
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    count = 0

    for item in rows:
        participants = item.get("participants", []) or []
        home = next((p for p in participants if (p.get("meta") or {}).get("location") == "home"), None)
        away = next((p for p in participants if (p.get("meta") or {}).get("location") == "away"), None)
        kickoff = pd.to_datetime(item.get("starting_at"), errors="coerce", utc=True)
        if pd.isna(kickoff) or not home or not away:
            continue

        scores = item.get("scores") or []
        def score_for(participant_id):
            for score in scores:
                if score.get("participant_id") == participant_id and str(score.get("description", "")).upper() in {"CURRENT", "FT", "FULLTIME"}:
                    value = _int_or_none((score.get("score") or {}).get("goals"))
                    if value is not None:
                        return value
            return None

        league = (item.get("league") or {}).get("name") or "Football"
        fx = Fixture(
            sport="soccer",
            league=str(league)[:80],
            season=str(item.get("season_id") or kickoff.year)[:20],
            match_date=kickoff.date(),
            home_team=resolve_team_name(db, str(home.get("name")), "soccer", "sportmonks"),
            away_team=resolve_team_name(db, str(away.get("name")), "soccer", "sportmonks"),
            home_score=score_for(home.get("id")),
            away_score=score_for(away.get("id")),
            source="sportmonks_range",
            extra={"sportmonks_fixture_id": item.get("id"), "state_id": item.get("state_id"), "coverage_mode": "date_range"},
        )
        upsert_fixture(db, fx)
        count += 1

    db.commit()
    return count


def _ingest_bzzoiro_paged(db: Session, token: str, start_date: str, end_date: str) -> int:
    """Page through Bzzoiro so the first 200 rows do not cap coverage."""

    if not token:
        return 0
    count = 0
    offset = 0
    session = requests.Session()
    while True:
        response = session.get(
            f"{BZZOIRO_BASE}/events/",
            params={"date_from": start_date, "date_to": end_date, "limit": 200, "offset": offset},
            headers={"Authorization": f"Token {token}", "Accept": "application/json"},
            timeout=35,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("results", []) if isinstance(payload, dict) else []
        if not rows:
            break
        for item in rows:
            if not isinstance(item, dict):
                continue
            kickoff = pd.to_datetime(item.get("kickoff") or item.get("start_time") or item.get("starting_at"), errors="coerce", utc=True)
            home = item.get("home_team") or item.get("homeTeam")
            away = item.get("away_team") or item.get("awayTeam")
            if isinstance(home, dict):
                home = home.get("name")
            if isinstance(away, dict):
                away = away.get("name")
            if pd.isna(kickoff) or not home or not away:
                continue
            match_day = kickoff.date().isoformat()
            if not start_date <= match_day <= end_date:
                continue
            league = item.get("league") or item.get("competition") or "Football"
            if isinstance(league, dict):
                league = league.get("name") or "Football"
            fx = Fixture(
                sport="soccer",
                league=str(league)[:80],
                season=str(item.get("season") or kickoff.year)[:20],
                match_date=kickoff.date(),
                home_team=resolve_team_name(db, str(home), "soccer", "bzzoiro"),
                away_team=resolve_team_name(db, str(away), "soccer", "bzzoiro"),
                home_score=_score(item, "home"),
                away_score=_score(item, "away"),
                source="bzzoiro_page",
                extra={"bzzoiro_event_id": item.get("id"), "status": item.get("status"), "coverage_mode": "paged_range"},
            )
            upsert_fixture(db, fx)
            count += 1
        if len(rows) < 200:
            break
        offset += len(rows)
    db.commit()
    return count


def _ingest_openfoot(db: Session, dates: list[str]) -> int:
    """Use OpenFoot's no-key daily endpoint as a public fallback."""

    count = 0
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    for target_date in dates:
        try:
            response = session.get(f"{OPENFOOT_BASE}/matches", params={"date": target_date}, timeout=25)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            continue
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        for item in rows:
            if not isinstance(item, dict):
                continue
            kickoff = pd.to_datetime(item.get("kickoffAt"), errors="coerce", utc=True)
            home = item.get("homeTeam") or {}
            away = item.get("awayTeam") or {}
            home_name = home.get("name") if isinstance(home, dict) else str(home)
            away_name = away.get("name") if isinstance(away, dict) else str(away)
            if pd.isna(kickoff) or not home_name or not away_name:
                continue
            competition = item.get("competition") or item.get("competitionName") or item.get("competitionId") or "Football"
            if isinstance(competition, dict):
                competition = competition.get("name") or competition.get("id") or "Football"
            fx = Fixture(
                sport="soccer",
                league=str(competition)[:80],
                season=str(item.get("season") or kickoff.year)[:20],
                match_date=kickoff.date(),
                home_team=resolve_team_name(db, home_name, "soccer", "openfoot_range"),
                away_team=resolve_team_name(db, away_name, "soccer", "openfoot_range"),
                home_score=_score(item, "home"),
                away_score=_score(item, "away"),
                source="openfoot_range",
                extra={"openfoot_match_id": item.get("id"), "status": item.get("status")},
            )
            upsert_fixture(db, fx)
            count += 1
        db.commit()
    return count


def run_deep_coverage(db: Session, min_coverage: int = MIN_COVERAGE) -> dict:
    """Expand real coverage across providers until the board reaches its floor."""

    settings = get_settings()
    purged = purge_showcase_rows(db)
    before = _future_count(db)
    report = {"before": before, "after": before, "target": min_coverage, "ran": False, "showcase_purged": purged, "sources": {}}
    if before >= min_coverage:
        return report

    report["ran"] = True
    start_date, end_date, dates = _window()

    def run(name: str, fn, *args):
        try:
            report["sources"][name] = fn(*args)
        except Exception as exc:
            report["sources"][f"{name}_error"] = str(exc)[:220]

    if settings.sportmonks_api_key and _future_count(db) < min_coverage:
        run("sportmonks_range", _ingest_sportmonks, db, settings.sportmonks_api_key, start_date, end_date)
    if settings.bzzoiro_api_key and _future_count(db) < min_coverage:
        run("bzzoiro_paged", _ingest_bzzoiro_paged, db, settings.bzzoiro_api_key, start_date, end_date)
    if settings.football_data_api_key and _future_count(db) < min_coverage:
        run("football_data_org", ingest_football_data_org_matches, db, settings.football_data_api_key, dates)
    if settings.api_football_com_key and _future_count(db) < min_coverage:
        run("apifootball_com", ingest_apifootball_com_events, db, settings.api_football_com_key, dates)
    if settings.allsportsapi_key and _future_count(db) < min_coverage:
        run("allsportsapi", ingest_allsportsapi_events, db, settings.allsportsapi_key, dates, settings.allsportsapi_sport_list)
    if settings.thesportsdb_enabled and _future_count(db) < min_coverage:
        run("thesportsdb", ingest_thesportsdb_events, db, settings.thesportsdb_api_key, dates[:2], settings.thesportsdb_sport_list, min(settings.thesportsdb_max_calls, 20))
    if _future_count(db) < min_coverage:
        run("fixture_download", ingest_fixture_download_football, db, dates, 32)
    if _future_count(db) < min_coverage:
        run("sporting_events", ingest_sporting_events_football, db, dates)
    if _future_count(db) < min_coverage:
        run("openfoot", _ingest_openfoot, db, dates)

    after = _future_count(db)
    report["after"] = after
    log.info("Deep fixture coverage: %s", report)
    return report
