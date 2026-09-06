"""Public score-site and broad web fixture ingestion.

Uses bounded public-page reads plus the existing SportyBet fixture reader as an
additional contributor. No source replaces another; all successful rows enter
the same normalized fixture pool for immediate display and later AI analysis.
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
        day = int(match.group("d")); month = int(match.group("m")); year_raw = match.group("y")
        if year_raw:
            year = int(year_raw); year += 2000 if year < 100 else 0
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
        not low or len(low) > 90 or any(x in low for x in (
            "summary odds results fixtures standings", "show more matches",
            "latest scores", "live scores", "favorites", "my teams",
        ))
    )


def _extract_dom_pairs(html: str, today: date, wanted: set[date]) -> list[tuple[date, str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    participants = soup.find_all(
        class_=lambda value: bool(value) and any(
            "participant" in str(c).lower() for c in (value if isinstance(value, list) else [value])
        )
    )
    found: list[tuple[date, str, str]] = []
    for element in participants:
        classes = " ".join(element.get("class", [])).lower()
        if "home" not in classes and "away" not in classes:
            continue
        name = _normalise_space(element.get_text(" ", strip=True))
        if _is_navigation(name):
            continue
        parent = element; event_date = None
        for _ in range(6):
            parent = parent.parent
            if not parent: break
            event_date = _parse_date_token(_normalise_space(parent.get_text(" ", strip=True)), today)
            if event_date: break
        if event_date not in wanted:
            continue
        container = element.parent; sibling = None
        if container:
            parts = container.find_all(
                class_=lambda value: bool(value) and any(
                    "participant" in str(c).lower() for c in (value if isinstance(value, list) else [value])
                )
            )
            for candidate in parts:
                cclasses = " ".join(candidate.get("class", [])).lower()
                if (("away" in classes and "home" in cclasses) or ("home" in classes and "away" in cclasses)):
                    sibling = _normalise_space(candidate.get_text(" ", strip=True)); break
        if sibling and not _is_navigation(sibling):
            home, away = (name, sibling) if "home" in classes else (sibling, name)
            found.append((event_date, home[:80], away[:80]))
    return found


def _extract_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]): tag.decompose()
    text = soup.get_text("\n", strip=True)
    return [_normalise_space(line) for line in text.splitlines() if _normalise_space(line)]


def _extract_text_pairs(lines: list[str], today: date, wanted: set[date]) -> list[tuple[date, str, str]]:
    found: list[tuple[date, str, str]] = []; current_date: date | None = None; recent_window = 0
    for idx, line in enumerate(lines):
        parsed = _parse_date_token(line, today)
        if parsed: current_date = parsed; recent_window = 45
        if recent_window: recent_window -= 1
        if current_date not in wanted: continue
        sample = " ".join(lines[idx:idx + 4])
        for match in _PAIR_RE.finditer(sample):
            home = _normalise_space(match.group("home")); away = _normalise_space(match.group("away"))
            if _is_navigation(home) or _is_navigation(away): continue
            if home.lower() in {"football", "basketball", "tennis", "hockey", "baseball", "handball", "volleyball", "cricket"}: continue
            found.append((current_date, home[:80], away[:80]))
    return found


def _dedupe_pairs(pairs: list[tuple[date, str, str]]) -> list[tuple[date, str, str]]:
    unique = {}
    for item in pairs: unique[(item[0], item[1].lower(), item[2].lower())] = item
    return list(unique.values())


def _fetch_reader(url: str, today: date, wanted: set[date]) -> tuple[list[tuple[date, str, str]], str | None]:
    try:
        response = requests.get(f"https://r.jina.ai/{url}", headers=READER_HEADERS, timeout=25)
        response.raise_for_status()
        return _dedupe_pairs(_extract_text_pairs(_extract_lines(response.text), today, wanted)), None
    except requests.HTTPError as exc:
        return [], f"reader_http_{getattr(exc.response, 'status_code', None) or 'error'}"
    except Exception as exc:
        return [], f"reader_{str(exc)[:160]}"


def _fetch_source(source: str, sport: str, wanted_dates: list[date]) -> tuple[list[tuple[date, str, str]], str | None]:
    url = urljoin(SOURCES[source], SPORT_PATHS[sport]); today = date.today(); wanted = set(wanted_dates)
    direct_error = None
    try:
        response = requests.get(url, headers=HEADERS, timeout=18); response.raise_for_status()
        pairs = _dedupe_pairs(_extract_dom_pairs(response.text, today, wanted) + _extract_text_pairs(_extract_lines(response.text), today, wanted))
        if pairs: return pairs, None
        reader_pairs, reader_error = _fetch_reader(url, today, wanted)
        if reader_pairs: return reader_pairs, "reader_ok"
        return [], reader_error or "empty"
    except requests.HTTPError as exc:
        direct_error = f"http_{getattr(exc.response, 'status_code', None) or 'error'}"
    except Exception as exc:
        direct_error = str(exc)[:180]
    reader_pairs, reader_error = _fetch_reader(url, today, wanted)
    if reader_pairs: return reader_pairs, f"direct_{direct_error}_reader_ok"
    return [], direct_error or reader_error or "error"


def _persist_pairs(db: Session, pairs: list[tuple[date, str, str]], source: str, sport: str) -> int:
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
            upsert_fixture(db, fx); persisted += 1
        except Exception:
            log.exception("Web fixture persistence failed: %s %s", source, sport)
    return persisted


def ingest_web_score_sources(db: Session, target_dates: list[str], max_sports: int = 9) -> dict:
    """Ingest Flashscore, LiveScore and SportyBet fixtures into one pool."""
    wanted_dates = []
    for value in target_dates:
        try: wanted_dates.append(date.fromisoformat(value))
        except ValueError: continue
    if not wanted_dates: return {"rows": 0, "sources": {}}

    reports = {}; total = 0
    sports = list(SPORT_PATHS)[:max(max_sports, 1)]
    for source in SOURCES:
        for sport in sports:
            pairs, error = _fetch_source(source, sport, wanted_dates)
            persisted = _persist_pairs(db, pairs, source, sport)
            reports[f"{source}_{sport}"] = {"rows": persisted, "status": error or "ok", "url": urljoin(SOURCES[source], SPORT_PATHS[sport])}
            total += persisted

    # SportyBet is the project's existing broad upcoming-fixture reader. Seven
    # sports are sampled once per refresh to avoid excessive provider traffic.
    try:
        from app.scraper.sportybet import fetch_all_sports
        sporty_sports = ["soccer", "basketball", "tennis", "american_football", "hockey", "baseball", "cricket"]
        sporty_rows = fetch_all_sports(sports=sporty_sports, limit_per_sport=75)
        sporty_persisted = 0
        for item in sporty_rows:
            try:
                kickoff = pd.to_datetime(item.get("match_date"), errors="coerce", utc=True)
                sport = str(item.get("sport") or "soccer").strip().lower()
                home = str(item.get("home_team") or "").strip(); away = str(item.get("away_team") or "").strip()
                if pd.isna(kickoff) or not home or not away: continue
                fx = Fixture(
                    sport=sport,
                    league=str(item.get("league") or "Unknown")[:80],
                    season=str(kickoff.year), match_date=kickoff.date(),
                    home_team=resolve_team_name(db, home, sport, "sportybet"),
                    away_team=resolve_team_name(db, away, sport, "sportybet"),
                    home_odds=item.get("home_odds"), draw_odds=item.get("draw_odds"), away_odds=item.get("away_odds"),
                    source="sportybet",
                    extra={"sportybet_match_id": item.get("sportybet_match_id"), "provider": "sportybet"},
                )
                upsert_fixture(db, fx); sporty_persisted += 1
            except Exception:
                log.exception("SportyBet fixture persistence failed")
        reports["sportybet"] = {"rows": sporty_persisted, "status": "ok"}
        total += sporty_persisted
    except Exception as exc:
        reports["sportybet"] = {"rows": 0, "status": f"error_{str(exc)[:160]}"}
        log.exception("SportyBet fixture source failed")

    db.commit()
    return {"rows": total, "sources": reports}
