"""Public fixture coverage sources.

These sources are independent contributors to the fixture pool. They are
bounded, public-page readers and never replace the API providers.
"""

from datetime import date
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.scraper.loaders import upsert_fixture
from app.services.data_quality import resolve_team_name


FIXTURE_DOWNLOAD_INDEX = "https://fixturedownload.com/sport/football"
FIXTURE_DOWNLOAD_BASE = "https://fixturedownload.com"
SPORTING_EVENTS_URL = "https://sporting-events.org/data/football.json"


def _text(value) -> str:
    return str(value or "").strip()


def _int_or_none(value):
    try:
        if value in (None, "", "null"):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


_LEAGUE_HINTS = {
    "premier-league": "Premier League",
    "epl": "Premier League",
    "la-liga": "La Liga",
    "serie-a": "Serie A",
    "bundesliga": "Bundesliga",
    "ligue-1": "Ligue 1",
    "eredivisie": "Eredivisie",
    "primeira-liga": "Primeira Liga",
    "champions-league": "UEFA Champions League",
    "europa-league": "UEFA Europa League",
    "conference-league": "UEFA Conference League",
    "championship": "EFL Championship",
    "scottish-premiership": "Scottish Premiership",
    "mls": "MLS",
    "brazil-serie-a": "Brazil Serie A",
    "argentina-primera": "Argentina Primera",
    "j1-league": "J1 League",
    "a-league": "A-League",
}


def _slug_to_league(url: str) -> str:
    path = urlparse(url).path.rstrip("/").split("/")
    slug = next((part for part in reversed(path) if part and part not in {"json", "view"}), "football")
    slug = slug.rsplit("-20", 1)[0].lower()
    for hint, league in sorted(_LEAGUE_HINTS.items(), key=lambda item: -len(item[0])):
        if hint in slug:
            return league
    return slug.replace("-", " ").title() or "Football"


def _known_league_from_text(value: str) -> str:
    low = value.lower()
    for hint, league in sorted(_LEAGUE_HINTS.items(), key=lambda item: -len(item[0])):
        if hint.replace("-", " ") in low or hint in low:
            return league
    return "Football"


def _safe_date(value):
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed.date()


def ingest_fixture_download_football(db: Session, target_dates: list[str], max_competitions: int = 8) -> int:
    """Pull a bounded set of current FixtureDownload JSON feeds."""
    if not target_dates:
        return 0
    wanted_dates = set(target_dates)
    session = requests.Session()
    session.headers.update({"User-Agent": "REEDS-football-coverage/1.0"})

    try:
        index = session.get(FIXTURE_DOWNLOAD_INDEX, timeout=20)
        index.raise_for_status()
        soup = BeautifulSoup(index.text, "html.parser")
    except Exception:
        return 0

    feed_urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = _text(anchor.get("href"))
        if "/view/json/" not in href:
            continue
        absolute = urljoin(FIXTURE_DOWNLOAD_BASE, href)
        feed_url = absolute.replace("/view/json/", "/feed/json/")
        if feed_url not in seen:
            seen.add(feed_url)
            feed_urls.append(feed_url)
        if len(feed_urls) >= max(max_competitions, 1):
            break

    count = 0
    for feed_url in feed_urls:
        try:
            response = session.get(feed_url, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                continue
            league = _slug_to_league(feed_url)
            for item in payload:
                if not isinstance(item, dict):
                    continue
                home = _text(item.get("HomeTeam"))
                away = _text(item.get("AwayTeam"))
                match_date = _safe_date(item.get("DateUtc"))
                day = match_date.isoformat() if match_date else ""
                if not home or not away or day not in wanted_dates:
                    continue
                fx = Fixture(
                    sport="soccer",
                    league=league,
                    season=str(item.get("Season") or match_date.year),
                    match_date=match_date,
                    home_team=resolve_team_name(db, home, "soccer", "fixture_download"),
                    away_team=resolve_team_name(db, away, "soccer", "fixture_download"),
                    home_score=_int_or_none(item.get("HomeTeamScore")),
                    away_score=_int_or_none(item.get("AwayTeamScore")),
                    source="fixture_download",
                    extra={
                        "fixture_download_match_number": item.get("MatchNumber"),
                        "fixture_download_round": item.get("RoundNumber"),
                        "fixture_download_feed": feed_url,
                    },
                )
                upsert_fixture(db, fx)
                count += 1
        except Exception:
            continue

    db.commit()
    return count


def ingest_sporting_events_football(db: Session, target_dates: list[str]) -> int:
    """Pull the no-key Sporting Events football JSON dataset plus web score feeds."""
    if not target_dates:
        return 0
    wanted_dates = set(target_dates)
    count = 0
    try:
        response = requests.get(
            SPORTING_EVENTS_URL,
            timeout=20,
            headers={"User-Agent": "REEDS-football-coverage/1.0", "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        payload = {}

    rows = payload.get("events", []) if isinstance(payload, dict) else []
    for item in rows:
        if not isinstance(item, dict):
            continue
        home = _text(item.get("home_team"))
        away = _text(item.get("away_team"))
        if not home or not away:
            continue
        match_date = _safe_date(item.get("date_utc"))
        if not match_date or match_date.isoformat() not in wanted_dates:
            continue
        fixture_text = _text(item.get("fixture"))
        league = _known_league_from_text(fixture_text)
        if league == "Football":
            league = _text(payload.get("competition")) or "Football"
        fx = Fixture(
            sport="soccer",
            league=league,
            season=str(match_date.year),
            match_date=match_date,
            home_team=resolve_team_name(db, home, "soccer", "sporting_events"),
            away_team=resolve_team_name(db, away, "soccer", "sporting_events"),
            source="sporting_events",
            extra={
                "sporting_events_url": item.get("url"),
                "status": item.get("status"),
                "country": item.get("country"),
                "city": item.get("city"),
            },
        )
        upsert_fixture(db, fx)
        count += 1

    # The public score sites are additional contributors. Each source is bounded
    # to one public listing request per sport and failures are isolated.
    try:
        from app.scraper.web_score_sources import ingest_web_score_sources
        web_report = ingest_web_score_sources(db, target_dates)
        count += int(web_report.get("rows", 0) or 0)
    except Exception as exc:
        # Never let a score-site block the existing public/API fan-out.
        import logging
        logging.getLogger(__name__).warning("Web score sources unavailable: %s", exc)

    db.commit()
    return count
