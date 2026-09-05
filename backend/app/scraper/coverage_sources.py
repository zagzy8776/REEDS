"""Additional football coverage sources with bounded request volume.

These providers are optional. No request is made unless the corresponding env
key is configured. They are used for fixture/score coverage only; REEDS keeps
its own prediction logic and does not import provider predictions as truth.
"""

from datetime import date

import pandas as pd
import requests
from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.services.data_quality import resolve_team_name
from app.scraper.loaders import upsert_fixture


BZZOIRO_BASE = "https://sports.bzzoiro.com/api/v2"
OPENFOOT_BASE = "https://openfootapi.com/v1"


def _int_or_none(value):
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _extract_score(item: dict, side: str):
    score = item.get("score") or {}
    if isinstance(score, dict):
        nested = score.get(side)
        if isinstance(nested, dict):
            for key in ("goals", "points", "score", "total"):
                value = _int_or_none(nested.get(key))
                if value is not None:
                    return value
        elif nested is not None:
            value = _int_or_none(nested)
            if value is not None:
                return value
        for key in (f"{side}_score", f"{side}Score", f"{side}_goals"):
            value = _int_or_none(score.get(key))
            if value is not None:
                return value
    for key in (f"{side}_score", f"{side}Score", f"{side}_goals"):
        value = _int_or_none(item.get(key))
        if value is not None:
            return value
    return None


def ingest_bzzoiro_football(db: Session, api_key: str, target_dates: list[str]) -> int:
    """Ingest a single ranged request covering the requested window.

    The provider documents date_from/date_to on the football events endpoint.
    We deliberately make one call per refresh window to avoid quota-heavy polling.
    """

    if not api_key or not target_dates:
        return 0
    from_date, to_date = target_dates[0], target_dates[-1]
    response = requests.get(
        f"{BZZOIRO_BASE}/events/",
        params={"date_from": from_date, "date_to": to_date, "limit": 200},
        headers={"Authorization": f"Token {api_key}", "Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("results", []) if isinstance(payload, dict) else []
    count = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
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
        match_day = kickoff.date().isoformat()
        if match_day < from_date or match_day > to_date:
            continue
        league = item.get("league") or item.get("competition") or "Football"
        if isinstance(league, dict):
            league = league.get("name") or "Football"
        status = item.get("status")
        fx = Fixture(
            sport="soccer",
            league=str(league),
            season=str(item.get("season") or kickoff.year),
            match_date=date.fromisoformat(match_day),
            home_team=resolve_team_name(db, str(home), "soccer", "bzzoiro"),
            away_team=resolve_team_name(db, str(away), "soccer", "bzzoiro"),
            home_score=_extract_score(item, "home"),
            away_score=_extract_score(item, "away"),
            source="bzzoiro",
            extra={"bzzoiro_event_id": item.get("id"), "status": status},
        )
        upsert_fixture(db, fx)
        count += 1
    db.commit()
    return count


def ingest_openfoot_football(db: Session, api_key: str, target_dates: list[str]) -> int:
    """Ingest OpenFoot fixture/result coverage, one request per calendar date."""

    if not api_key or not target_dates:
        return 0
    count = 0
    session = requests.Session()
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    for target_date in target_dates:
        response = session.get(
            f"{OPENFOOT_BASE}/matches",
            params={"date": target_date},
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
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
                league=str(competition),
                season=str(item.get("season") or kickoff.year),
                match_date=kickoff.date(),
                home_team=resolve_team_name(db, str(home_name), "soccer", "openfoot"),
                away_team=resolve_team_name(db, str(away_name), "soccer", "openfoot"),
                home_score=_extract_score(item, "home"),
                away_score=_extract_score(item, "away"),
                source="openfoot",
                extra={"openfoot_match_id": item.get("id"), "status": item.get("status")},
            )
            upsert_fixture(db, fx)
            count += 1
        db.commit()
    return count
