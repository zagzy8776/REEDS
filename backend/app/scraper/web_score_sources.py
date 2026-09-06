"""Public score-site fixture ingestion.

Uses public listing pages only; no private/internal API endpoints and no browser
automation. Requests are deliberately bounded and failures are isolated so one
site can never stop the other fixture providers. When the site's public HTML is
client-rendered, the free Jina Reader is used as a rendering-aware reader for the
same public URL.
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
    "User-Agent": "Mozilla/5.0 (compatible; REEDS fixture reader/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

READER_HEADERS = {
    "User-Agent": "REEDS fixture reader/1.0",
    "Accept": "text/plain, text/markdown;q=0.9, */*;q=0.5",
    "X-Timeout": "12",
    "X-Token-Budget": "6000",
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

_DATE_RE = re.compile(r"\b(?P<d>\d{2})[./-](?P<m>\d{2})(?:[./-](?P<y>\d{2,4}))?\b")
_PAIR_RE = re.compile(r"(?P<home>[^,|–—]{2,80}?)\s*(?:-|–|—|vs\.?|v\.)\s*(?P<away>[^,|–—]{2,80})", re.I)


def _normalise_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n:-")


def _parse_date_token(token: str, today: date) -> date | None:
    match = _DATE_RE.search(token or "")
    if not match:
        return None
    try:
        day = int(match.group("d"))
        month = int(match.group("m"))
        year_raw = match.group("y")
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        else:
            year = today.year
            candidate = date(year, month, day)
            if candidate < today and (today - candidate).days > 300:
                year += 1
        return date(year, month, day)
    except ValueError:
        return None


def _is_navigation(value: str) -> bool:
    low = _normalise_space(value).lower()
    return (
        not low
        or len(low) > 90
        or any(x in low for x in (
            "summary odds results fixtures standings",
            "show more matches",
            "latest scores",
            "live scores",
            "favorites",
            "my teams",
        ))
    )


def _extract_dom_pairs(html: str, today: date, wanted: set[date]) -> list[tuple[date, str, str]]:
    """Use public event/participant DOM classes when a page exposes them."""
    soup = BeautifulSoup(html, "html.parser")
    participants = soup.find_all(
        class_=lambda value: bool(value) and any("participant" in str(c).lower() for c in (value if isinstance(value, list) else [value]))
    )
    found: list[tuple[date, str, str]] = []
    for element in participants:
        classes = " ".join(element.get("class", [])).lower()
        if "home" not in classes and "away" not in classes:
            continue
        name = _normalise_space(element.get_text(" ", strip=True))
        if _is_navigation(name):
            continue
        parent = element
        event_date = None
        for _ in range(6):
            parent = parent.parent
            if not parent:
                break
            text = _normalise_space(parent.get_text(" ", strip=True))
            event_date = _parse_date_token(text, today)
            if event_date:
                break
        if event_date not in wanted:
            continue
        sibling = None
        container = element.parent
        if container:
            all_participants = container.find_all(
                class_=lambda value: bool(value) and any("participant" in str(c).lower() for c in (value if isinstance(value, list) else [value]))
            )
            for candidate in all_participants:
                cclasses = " ".join(candidate.get("class", [])).lower()
                if ("away" in classes and "home" in cclasses) or ("home" in classes and "away" in cclasses):
                    sibling = _normalise_space(candidate.get_text(" ", strip=True))
                    break
        if sibling and not _is_navigation(sibling):
            home, away = (name, sibling) if "home" in classes else (sibling, name)
            found.append((event_date, home[:80], away[:80]))
    return found


def _extract_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return [_normalise_space(line) for line in text.splitlines() if _normalise_space(line)]


def _extract_text_pairs(lines: list[str], today: date, wanted: set[date]) -> list[tuple[date, str, str]]:
    found: list[tuple[date, str, str]] = []
    current_date: date | None = None
    recent_window = 0
    for idx, line in enumerate(lines):
        parsed = _parse_date_token(line, today)
        if parsed:
            current_date = parsed
            recent_window = 45
        if recent_window:
            recent_window -= 1
        if current_date not in wanted:
            continue
        sample = " ".join(lines[idx:idx + 4])
        for match in _PAIR_RE.finditer(sample):
            home = _normalise_space(match.group("home"))
            away = _normalise_space(match.group("away"))
            if _is_navigation(home) or _is_navigation(away):
                continue
            if home.lower() in {"football", "basketball", "tennis", "hockey", "baseball", "handball", "volleyball", "cricket"}:
                continue
            found.append((current_date, home[:80], away[:80]))
    return found


def _dedupe_pairs(pairs: list[tuple[date, str, str]]) -> list[tuple[date, str, str]]:
    unique: dict[tuple[date, str, str], tuple[date, str, str]] = {}
    for item in pairs:
        unique[(item[0], item[1].lower(), item[2].lower())] = item
    return list(unique.values())


def _fetch_reader(url: str, today: date, wanted: set[date]) -> tuple[list[tuple[date, str, str]], str | None]:
    """Render the same public page through Jina Reader when direct HTML is sparse."""
    reader_url = f"https://r.jina.ai/{url}"
    try:
        response = requests.get(reader_url, headers=READER_HEADERS, timeout=25)
        response.raise_for_status()
        pairs = _extract_text_pairs(_extract_lines(response.text), today, wanted)
        return _dedupe_pairs(pairs), None
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        return [], f"reader_http_{status or 'error'}"
    except Exception as exc:
        return [], f"reader_{str(exc)[:160]}"


def _fetch_source(source: str, sport: str, wanted_dates: list[date]) -> tuple[list[tuple[date, str, str]], str | None]:
    url = urljoin(SOURCES[source], SPORT_PATHS[sport])
    today = date.today()
    wanted = set(wanted_dates)
    direct_error = None
    try:
        response = requests.get(url, headers=HEADERS, timeout=18)
        response.raise_for_status()
        pairs = _extract_dom_pairs(response.text, today, wanted)
        pairs.extend(_extract_text_pairs(_extract_lines(response.text), today, wanted))
        pairs = _dedupe_pairs(pairs)
        # Client-rendered score sites often return a valid HTML shell but no
        # fixture rows. In that case use the same public URL through Jina Reader,
        # whose default engine renders JavaScript before extracting the page.
        if pairs:
            return pairs, None
        reader_pairs, reader_error = _fetch_reader(url, today, wanted)
        if reader_pairs:
            return reader_pairs, "reader_ok"
        return [], reader_error or "empty"
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        direct_error = f"http_{status or 'error'}"
    except Exception as exc:
        direct_error = str(exc)[:180]

    reader_pairs, reader_error = _fetch_reader(url, today, wanted)
    if reader_pairs:
        return reader_pairs, f"direct_{direct_error}_reader_ok"
    return [], direct_error or reader_error or "error"


def ingest_web_score_sources(db: Session, target_dates: list[str], max_sports: int = 9) -> dict:
    """Ingest upcoming fixtures from both public score sites across sports.

    Each site is requested at most once per sport during a run. If the page is
    JS-rendered, one additional Jina Reader request is made only when direct
    extraction produced no fixture rows.
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
    sports = list(SPORT_PATHS)[:max(max_sports, 1)]
    for source in SOURCES:
        for sport in sports:
            pairs, error = _fetch_source(source, sport, wanted_dates)
            persisted = 0
            for match_date, home, away in pairs:
                try:
                    fx = Fixture(
                        sport=sport,
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
            reports[f"{source}_{sport}"] = {
                "rows": persisted,
                "status": error or "ok",
                "url": urljoin(SOURCES[source], SPORT_PATHS[sport]),
            }
            total += persisted
    db.commit()
    return {"rows": total, "sources": reports}
