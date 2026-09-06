"""Demand-driven fixture coverage expansion.

This is a safety-net around the normal scheduler. It only does provider work when
future coverage is genuinely low, so it does not duplicate healthy scheduled
refreshes on every wake.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Fixture
from app.scraper.loaders import upsert_fixture
from app.services.data_quality import resolve_team_name

log = logging.getLogger(__name__)

BZZOIRO_BASE = "https://sports.bzzoiro.com/api/v2"
OPENFOOT_BASE = "https://openfootapi.com/v1"
SPORTMONKS_BASE = "https://api.sportmonks.com/v3/football"

MIN_COVERAGE = 120
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
        .filter(Fixture.match_date >= today)
        .scalar()
        or 0
    )


def _ingest_sportmonks(db: Session, token: str, start_date: str, end_date: str) -> int:
    """Use SportMonks' date-range endpoint: one request for the whole window."""
    response = requests.get(
        f"{SPORTMONKS_BASE}/fixtures/between/{start_date}/{end_date}",
        params={
            "api_token": token,
            "include": "participants;scores;league",
        },
        timeout=40,
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
        league = (item.get("league") or {}).get("name") or "Football"
        fx = Fixture(
            sport="soccer",
            league=str(league)[:80],
            season=str(item.get("season_id") or kickoff.year)[:20],
            match_date=kickoff.date(),
            home_team=resolve_team_name(db, str(home.get("name")), "soccer", "sportmonks"),
            away_team=resolve_team_name(db, str(away.get("name")), "soccer", "sportmonks"),
            home_score=None,
            away_score=None,
            source="sportmonks_range",
            extra={
                "sportmonks_fixture_id": item.get("id"),
                "state_id": item.get("state_id"),
                "coverage_mode": "date_range",
            },
        )
        for side, participant in (("home", home), ("away", away)):
            scores = item.get("scores") or []
            value = None
            for score in scores:
                if score.get("participant_id") == participant.get("id") and str(score.get("description", "")).upper() in {"CURRENT", "FT", "FULLTIME"}:
                    value = _int_or_none((score.get("score") or {}).get("goals"))
                    if value is not None:
                        break
            if side == "home":
                fx.home_score = value
            else:
                fx.away_score = value
        upsert_fixture(db, fx)
        count += 1

    db.commit()
    return count


def _ingest_bzzoiro(db: Session, token: str, start_date: str, end_date: str) -> int:
    """Page through Bzzoiro instead of silently stopping at its 200-row page."""
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
            kickoff = pd.to_datetime(
                item.get("kickoff") or item.get("start_time") or item.get("starting_at"),
                errors="coerce",
                utc=True,
            )
            home = item.get("home_team") or item.get("homeTeam")
            away = item.get("away_team") or item.get("awayTeam")
            if isinstance(home, dict):
                home = home.get("name")
            if isinstance(away, dict):
                away = away.get("name")
            if pd.isna(kickoff) or not home or not away:
                continue
            match_day = kickoff.date()
            league = item.get("league") or item.get("competition") or "Football"
            if isinstance(league, dict):
                league = league.get("name") or "Football"
            fx = Fixture(
                sport="soccer",
                league=str(league)[:80],
                season=str(item.get("season") or kickoff.year)[:20],
                match_date=match_day,
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
    count = 0
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    for target_date in dates:
        try:
            response = session.get(f"{OPENFOOT_BASE}/matches", params={"date": target_date}, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            continue
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        for item in rows:
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
    """Expand only when future coverage is below the desired floor."""
    settings = get_settings()
    before = _future_count(db)
    report = {"before": before, "after": before, "target": min_coverage, "ran": False, "sources": {}}
    if before >= min_coverage:
        return report

    report["ran"] = True
    start_date, end_date, dates = _window()

    if settings.sportmonks_api_key:
        try:
            report["sources"]["sportmonks_range"] = _ingest_sportmonks(db, settings.sportmonks_api_key, start_date, end_date)
        except Exception as exc:
            report["sources"]["sportmonks_range_error"] = str(exc)[:200]

    if _future_count(db) < min_coverage and settings.bzzoiro_api_key:
        try:
            report["sources"]["bzzoiro_paged"] = _ingest_bzzoiro(db, settings.bzzoiro_api_key, start_date, end_date)
        except Exception as exc:
            report["sources"]["bzzoiro_paged_error"] = str(exc)[:200]

    if _future_count(db) < min_coverage:
        try:
            from app.services.public_football_sources import ingest_fixture_download_football

            report["sources"]["fixture_download"] = ingest_fixture_download_football(db, dates, max_competitions=32)
        except Exception as exc:
            report["sources"]["fixture_download_error"] = str(exc)[:200]

    if _future_count(db) < min_coverage:
        try:
            report["sources"]["openfoot"] = _ingest_openfoot(db, dates)
        except Exception as exc:
            report["sources"]["openfoot_error"] = str(exc)[:200]

    after = _future_count(db)
    report["after"] = after
    log.info("Deep fixture coverage: %s", report)
    return report
