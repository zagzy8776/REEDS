"""Public score-site fixture ingestion.

Uses public listing pages only; no private/internal API endpoints and no browser
automation. Requests are deliberately bounded and failures are isolated so one
site can never stop the other fixture providers.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.services.data_quality import resolve_team_name
from app.scraper.loaders import upsert_fixture

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; REEDS fixture reader/1.0; +https://realssa-news.example)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

SOURCES = {
    "flashscore": "https://www.flashscore.com.ng/",
    "livescore": "https://www.livescore.in/",
}

SPORT_PATHS = {
    "soccer": "football/",
    "basketball": "basketball/",
    "tennis": "tennis/",
    "hockey": "hockey/",
    "baseball": "baseball/",
    "american_football": "american-football/",
    "handball": "handball/",
    "volleyball": "volleyball/",
    "cricket": "cricket/",
}

_DATE_PATTERNS = (
    re.compile(r"\b(?P<d>\d{2})[./-](?P<m>\d{2})(?:[./-](?P<y>\d{2,4}))?\b"),
    re.compile(r"\b(?P<m>\d{2})/(?P<d>\d{2})\b"),
)
_PAIR_RE = re.compile(r"(?P<home>[^,|–—]{2,80}?)\s*(?:-|–|—|vs\.?|v\.)\s*(?P<away>[^,|–—]{2,80})", re.I)


def _normalise_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n:-")


def _parse_date_token(token: str, today: date) -> date | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(token)
        if not match:
            continue
        try:
            day = int(match.group("d"))
            month = int(match.group("m"))
            year_raw = match.groupdict().get("y")
            if year_raw:
                year = int(year_raw)
                if year < 100:
                    year += 2000
            else:
                year = today.year
                # Handle year rollover around New Year.
                candidate = date(year, month, day)
                if candidate < today.replace(month=1, day=1) and (today - candidate).days > 300:
                    year += 1
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _extract_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return [_normalise_space(line) for line in text.splitlines() if _normalise_space(line)]


def _candidate_pairs(lines: list[str], today: date, wanted: set[date]) -> list[tuple[date, str, str]]:
    found: list[tuple[date, str, str]] = []
    current_date: date | None = None
    recent_date_window = 0

    for idx, line in enumerate(lines):
        parsed = _parse_date_token(line, today)
        if parsed:
            current_date = parsed
            recent_date_window = 40

        if recent_date_window:
            recent_date_window -= 1
        if current_date not in wanted:
            continue

        chunks = [line]
        if idx + 1 < len(lines):
            chunks.append(lines[idx + 1])
        if idx + 2 < len(lines):
            chunks.append(lines[idx + 2])
        sample = " ".join(chunks)

        for match in _PAIR_RE.finditer(sample):
            home = _normalise_space(match.group("home"))
            away = _normalise_space(match.group("away"))
            # Navigation/header noise is aggressively rejected.
            low = f"{home} {away}".lower()
            if len(home) < 2 or len(away) < 2:
                continue
            if any(x in low for x in ("summary odds results fixtures standings", "show more", "latest scores")):
                continue
            if home.lower() in {"football", "basketball", "tennis", "hockey", "baseball"}:
                continue
            found.append((current_date, home[:80], away[:80]))

    # Stable de-duplication before DB natural-key upsert.
    unique = {}
    for item in found:
        unique[(item[0], item[1].lower(), item[2].lower())] = item
    return list(unique.values())


def _fetch_source(source: str, sport: str, wanted_dates: list[date]) -> tuple[list[tuple[date, str, str]], str | None]:
    base = SOURCES[source]
    url = urljoin(base, SPORT_PATHS[sport])
    try:
        response = requests.get(url, headers=HEADERS, timeout=18)
        response.raise_for_status()
        lines = _extract_lines(response.text)
        wanted = set(wanted_dates)
        return _candidate_pairs(lines, date.today(), wanted), None
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        return [], f"http_{status or 'error'}"
    except Exception as exc:
        return [], str(exc)[:180]


def ingest_web_score_sources(db: Session, target_dates: list[str], max_sports: int = 9) -> dict:
    """Ingest upcoming fixtures from both public score sites across sports.

    The source pages are fetched at most once per sport/site per run, so a full
    run is bounded to ``2 * max_sports`` HTTP requests. Every source is attempted
    independently and one failure never blocks another source.
    """
    wanted_dates: list[date] = []
    for value in target_dates:
        try:
            wanted_dates.append(date.fromisoformat(value))
        except ValueError:
            continue
    if not wanted_dates:
        return {"rows": 0, "sources": {}}

    reports: dict = {}
    total = 0
    for source in SOURCES:
        for sport in list(SPORT_PATHS)[:max(max_sports, 1)]:
            pairs, error = _fetch_source(source, sport, wanted_dates)
            persisted = 0
            for match_date, home, away in pairs:
                try:
                    fx = Fixture(
                        sport="soccer" if sport == "soccer" else sport,
                        league=f"{source.title()} {sport.replace('_', ' ').title()}",
                        season=str(match_date.year),
                        match_date=match_date,
                        home_team=resolve_team_name(db, home, sport, source),
                        away_team=resolve_team_name(db, away, sport, source),
                        source=source,
                        extra={"web_source": source, "web_sport": sport},
                    )
                    upsert_fixture(db, fx)
                    persisted += 1
                except Exception:
                    log.exception("Web fixture persistence failed: %s %s", source, sport)
            key = f"{source}_{sport}"
            reports[key] = {"rows": persisted, "status": error or "ok", "url": urljoin(SOURCES[source], SPORT_PATHS[sport])}
            total += persisted

    db.commit()
    return {"rows": total, "sources": reports}
