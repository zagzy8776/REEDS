from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.scraper.api_clients import AllSportsApiClient, ApiBasketballClient, ApiFootballClient, ApiFootballComClient, FootballDataOrgClient, SportMonksFootballClient, TheOddsApiClient, TheSportsDbClient
from app.services.data_quality import resolve_team_name
from app.utils.team_names import normalize_team_name


FOOTBALL_DATA_MAP = {
    "Date": "match_date",
    "HomeTeam": "home_team",
    "AwayTeam": "away_team",
    "FTHG": "home_score",
    "FTAG": "away_score",
    "B365H": "home_odds",
    "B365D": "draw_odds",
    "B365A": "away_odds",
}


def read_csv_flexible(path: str, **kwargs) -> pd.DataFrame:
    """Read public sports CSVs that may use UTF-8 or legacy European encodings."""

    last_error: Exception | None = None
    for encoding in ("utf-8", "latin1", "cp1252"):
        try:
            return pd.read_csv(path, encoding=encoding, **kwargs)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return pd.read_csv(path, **kwargs)

BASKETBALL_DATA_MAP = {
    "Date": "match_date",
    "GAME_DATE": "match_date",
    "game_date": "match_date",
    "GAME_DATE_EST": "match_date",
    "date": "match_date",
    "HomeTeam": "home_team",
    "HOME_TEAM": "home_team",
    "HOME_TEAM_NAME": "home_team",
    "TEAM_NAME_home": "home_team",
    "home_team_name": "home_team",
    "home_team": "home_team",
    "VisitorTeam": "away_team",
    "AwayTeam": "away_team",
    "AWAY_TEAM": "away_team",
    "VISITOR_TEAM_NAME": "away_team",
    "TEAM_NAME_away": "away_team",
    "away_team_name": "away_team",
    "away_team": "away_team",
    "HomePTS": "home_score",
    "PTS_home": "home_score",
    "HOME_PTS": "home_score",
    "PTS_HOME": "home_score",
    "home_score": "home_score",
    "home_points": "home_score",
    "AwayPTS": "away_score",
    "PTS_away": "away_score",
    "AWAY_PTS": "away_score",
    "PTS_AWAY": "away_score",
    "away_score": "away_score",
    "away_points": "away_score",
}


def _merge_extra(existing, incoming):
    """Merge provider metadata instead of allowing the latest provider to erase it."""
    old = dict(existing.extra or {}) if isinstance(existing.extra, dict) else {}
    new = dict(incoming or {}) if isinstance(incoming, dict) else {}
    provider_sources = set()
    for payload in (old, new):
        value = payload.get("provider_sources") or []
        if isinstance(value, str):
            provider_sources.add(value)
        elif isinstance(value, list):
            provider_sources.update(str(v) for v in value if v)
        source = payload.get("web_source") or payload.get("source")
        if source:
            provider_sources.add(str(source))
    if incoming.source:
        provider_sources.add(str(incoming.source))
    if provider_sources:
        new["provider_sources"] = sorted(provider_sources)
    old.update(new)
    return old


def _country_qualified_league(league: str, extra: dict | None) -> str:
    """Disambiguate generic competition names when the provider supplies a country."""
    league_name = str(league or "Football").strip()
    country = str((extra or {}).get("country") or "").strip()
    if not country:
        return league_name[:80]
    generic = {"premier league", "super league", "national league", "championship"}
    if league_name.lower() in generic and country.lower() not in {"england", "usa", "united states", "uk"}:
        return f"{country} {league_name}"[:80]
    return league_name[:80]


def upsert_fixture(db: Session, fixture: Fixture) -> None:
    """Insert or update a fixture using the natural uniqueness key.

    SQLAlchemy merge only works by primary key. Downloaded rows do not know primary
    keys, so we find the natural-key row and enrich it without destroying metadata
    collected from other providers.
    """

    if fixture.extra is None:
        fixture.extra = {}
    fixture.league = _country_qualified_league(fixture.league, fixture.extra)

    existing = (
        db.query(Fixture)
        .filter(
            Fixture.sport == fixture.sport,
            Fixture.league == fixture.league,
            Fixture.match_date == fixture.match_date,
            Fixture.home_team == fixture.home_team,
            Fixture.away_team == fixture.away_team,
        )
        .first()
    )
    if existing:
        existing.season = fixture.season
        if fixture.home_score is not None:
            existing.home_score = fixture.home_score
        if fixture.away_score is not None:
            existing.away_score = fixture.away_score
        if fixture.home_odds is not None:
            existing.home_odds = fixture.home_odds
        if fixture.draw_odds is not None:
            existing.draw_odds = fixture.draw_odds
        if fixture.away_odds is not None:
            existing.away_odds = fixture.away_odds
        existing.source = fixture.source or existing.source
        existing.extra = _merge_extra(existing, fixture.extra)
    else:
        fixture.extra = _merge_extra(fixture, fixture.extra)
        db.add(fixture)


def _to_int_or_none(value) -> int | None:
    try:
        if value is None or pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float_or_none(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
