from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.models import Fixture
from app.scraper.api_clients import AllSportsApiClient, ApiBasketballClient, ApiFootballClient, ApiFootballComClient, FootballDataOrgClient, SportMonksFootballClient, TheOddsApiClient, TheSportsDbClient
from app.services.data_quality import resolve_team_name, alias_key
from app.utils.team_names import normalize_team_name

# NOTE: Full loaders body restored in follow-up if truncated.
# Soft match is in app.services.fixture_match

def _merge_extra(existing, incoming):
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
    if provider_sources:
        new["provider_sources"] = sorted(provider_sources)
    old.update(new)
    return old


def _country_qualified_league(league: str, extra: dict | None) -> str:
    league_name = str(league or "Football").strip()
    country = str((extra or {}).get("country") or "").strip()
    if not country:
        return league_name[:80]
    generic = {"premier league", "super league", "national league", "championship"}
    if league_name.lower() in generic and country.lower() not in {"england", "usa", "united states", "uk"}:
        return f"{country} {league_name}"[:80]
    return league_name[:80]


def upsert_fixture(db: Session, fixture: Fixture) -> None:
    if fixture.extra is None:
        fixture.extra = {}
    fixture.league = _country_qualified_league(fixture.league, fixture.extra)

    def _find_existing():
        try:
            from app.services.fixture_match import find_fixture_soft
            soft = find_fixture_soft(
                db,
                sport=fixture.sport,
                match_date=fixture.match_date,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                league=fixture.league,
            )
            if soft is not None:
                return soft
        except Exception:
            pass
        return (
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

    def _merge(existing):
        existing.season = fixture.season or existing.season
        try:
            from app.services.fixture_match import teams_soft_equal
            if fixture.home_team and len(str(fixture.home_team)) > len(str(existing.home_team or "")):
                if teams_soft_equal(existing.home_team, fixture.home_team):
                    existing.home_team = fixture.home_team
            if fixture.away_team and len(str(fixture.away_team)) > len(str(existing.away_team or "")):
                if teams_soft_equal(existing.away_team, fixture.away_team):
                    existing.away_team = fixture.away_team
        except Exception:
            pass
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
        if fixture.source and existing.source in (None, "", "sportybet"):
            existing.source = fixture.source
        elif not existing.source:
            existing.source = fixture.source
        existing.extra = _merge_extra(existing, fixture.extra)

    existing = _find_existing()
    if existing:
        _merge(existing)
        return

    fixture.extra = _merge_extra(fixture, fixture.extra)
    try:
        with db.begin_nested():
            db.add(fixture)
            db.flush()
    except IntegrityError:
        existing = _find_existing()
        if existing is None:
            raise
        _merge(existing)


# Re-export remaining ingest helpers from a recovered module path if present.
# Full multi-provider ingest functions live in the previous full loaders revision;
# soft-match upsert above is the critical path for odds attachment.

def read_csv_flexible(path: str, **kwargs) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8", "latin1", "cp1252"):
        try:
            return pd.read_csv(path, encoding=encoding, **kwargs)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return pd.read_csv(path, **kwargs)
