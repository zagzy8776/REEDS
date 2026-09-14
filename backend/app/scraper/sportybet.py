"""SportyBet public odds ingestion.

The old /api/ng/query/* endpoints were retired.  The current public web
application uses the factsCenter feed.  REEDS prefers that feed directly and
can optionally use the Vercel transport relay when SportyBet's CloudFront
blocks the AWS egress IP.

This module only reads publicly displayed pre-match data.  It never places
bets or submits account credentials.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.sportybet.com",
    "Referer": "https://www.sportybet.com/ng/lite",
    "x-app-name": "sportybet",
}

_BASE = "https://www.sportybet.com/api/ng"
_CURRENT_UPCOMING = f"{_BASE}/factsCenter/wapConfigurableUpcomingEvents"
_PROXY_URL = os.getenv("SPORTYBET_PROXY_URL", "https://reeds-phi.vercel.app/api/sportybet")

SPORT_IDS = {
    "soccer": "sr:sport:1",
    "basketball": "sr:sport:2",
    "tennis": "sr:sport:5",
    "american_football": "sr:sport:16",
    "hockey": "sr:sport:4",
    "baseball": "sr:sport:3",
    "cricket": "sr:sport:21",
}

_MARKET_1X2 = "1"
_MARKET_MONEYLINE = "219"


def _request_json(url: str, params: dict[str, Any], retries: int = 2) -> Any:
    """GET JSON with bounded retry/backoff and explicit HTTP handling."""
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=_HEADERS, params=params, timeout=15)
            if response.status_code == 429:
                if attempt >= retries:
                    log.warning("SportyBet rate limit exhausted")
                    return None
                time.sleep(2 ** attempt)
                continue
            if response.status_code in (403, 404):
                log.warning("SportyBet %s returned %d", url, response.status_code)
                return None
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                log.warning("SportyBet returned malformed JSON from %s", url)
                return None
        except requests.RequestException as exc:
            if attempt >= retries:
                log.warning("SportyBet request failed: %s", exc)
                return None
            time.sleep(1.5 * (2 ** attempt))
        except Exception as exc:
            log.warning("Unexpected SportyBet request failure: %s", exc)
            return None
    return None


def _params_for_sport(sport: str, page: int = 1, page_size: int = 100) -> dict[str, Any]:
    market_id = _MARKET_1X2 if sport == "soccer" else _MARKET_MONEYLINE
    return {
        "sportId": SPORT_IDS.get(sport, SPORT_IDS["soccer"]),
        "marketId": market_id,
        "productId": "3",
        "page": page,
        "pageSize": min(page_size, 100),
        "_t": int(time.time() * 1000),
    }


def _fetch_current(sport: str, limit: int) -> Any:
    """Fetch the current factsCenter feed directly from SportyBet."""
    page_size = min(max(limit, 1), 100)
    return _request_json(_CURRENT_UPCOMING, _params_for_sport(sport, 1, page_size))


def _fetch_via_relay(sport: str, limit: int) -> Any:
    """Fetch the same public feed through the optional Vercel relay.

    AWS EC2 can receive a CloudFront 403 even when the public SportyBet site
    is reachable from normal browser infrastructure.  The relay keeps the
    source URL fixed and forwards only the small, whitelisted query set.
    """
    if not _PROXY_URL:
        return None
    return _request_json(_PROXY_URL, _params_for_sport(sport, 1, min(limit, 100)))


def _name_from(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "teamName", "homeTeamName", "awayTeamName", "desc", "description"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _to_odds(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if number <= 0 or number != number:
            return None
        return number
    except (TypeError, ValueError):
        return None


def _match_time(event: dict[str, Any]) -> str | None:
    value = (
        event.get("estimateStartTime")
        or event.get("kickoffTime")
        or event.get("startTime")
        or event.get("matchTime")
        or event.get("scheduledStartTime")
    )
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = float(value) / 1000 if float(value) > 1e11 else float(value)
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return str(value)


def _iter_event_dicts(payload: Any):
    """Yield event-like dicts from several known SportyBet response shapes."""
    seen: set[int] = set()

    def walk(node: Any, inherited_league: str = ""):
        if isinstance(node, dict):
            marker = id(node)
            if marker in seen:
                return
            seen.add(marker)

            league = inherited_league
            for key in ("tournamentName", "tournament", "leagueName", "competitionName"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    league = value.strip()
                    break
                if isinstance(value, dict):
                    candidate = _name_from(value)
                    if candidate:
                        league = candidate
                        break

            home = node.get("homeTeamName") or node.get("homeTeam") or node.get("home")
            away = node.get("awayTeamName") or node.get("awayTeam") or node.get("away")
            if _name_from(home) and _name_from(away):
                item = dict(node)
                item.setdefault("_league", league)
                yield item

            for key in ("tournaments", "tournamentList", "events", "eventList", "data", "result", "response", "items", "list", "matches"):
                child = node.get(key)
                if child is not None:
                    yield from walk(child, league)
            return

        if isinstance(node, list):
            for item in node:
                yield from walk(item, inherited_league)

    yield from walk(payload)


def _extract_market_values(event: dict[str, Any], sport: str) -> tuple[float | None, float | None, float | None]:
    home_odds = draw_odds = away_odds = None

    direct = {
        "home": ("homeOdds", "homeOdd", "home_odds"),
        "draw": ("drawOdds", "drawOdd", "draw_odds"),
        "away": ("awayOdds", "awayOdd", "away_odds"),
    }
    for key in direct["home"]:
        home_odds = _to_odds(event.get(key))
        if home_odds is not None:
            break
    for key in direct["draw"]:
        draw_odds = _to_odds(event.get(key))
        if draw_odds is not None:
            break
    for key in direct["away"]:
        away_odds = _to_odds(event.get(key))
        if away_odds is not None:
            break

    containers = []
    for key in ("markets", "marketList", "odds", "marketData"):
        value = event.get(key)
        if isinstance(value, list):
            containers.extend(value)
        elif isinstance(value, dict):
            containers.append(value)

    for market in containers:
        if not isinstance(market, dict):
            continue
        market_id = str(market.get("id") or market.get("marketId") or "")
        market_name = str(market.get("name") or market.get("marketDesc") or market.get("description") or "").lower()
        if sport == "soccer":
            if market_id != _MARKET_1X2 and not any(token in market_name for token in ("1x2", "match result", "match winner")):
                continue
        else:
            if market_id != _MARKET_MONEYLINE and not any(token in market_name for token in ("moneyline", "winner")):
                continue

        outcomes = market.get("outcomes") or market.get("selections") or market.get("outcomeList") or []
        if isinstance(outcomes, dict):
            outcomes = list(outcomes.values())
        for outcome in outcomes if isinstance(outcomes, list) else []:
            if not isinstance(outcome, dict):
                continue
            outcome_id = str(outcome.get("id") or outcome.get("outcomeId") or outcome.get("selectionId") or "")
            label = str(outcome.get("desc") or outcome.get("name") or outcome.get("outcomeDesc") or "").lower().strip()
            odds = _to_odds(outcome.get("odds") or outcome.get("price") or outcome.get("value"))
            if sport == "soccer":
                if outcome_id == "1" or label in {"1", "home", "home win", "w1"}:
                    home_odds = odds
                elif outcome_id == "2" or label in {"2", "away", "away win", "w2"}:
                    away_odds = odds
                elif outcome_id in {"x", "0"} or label in {"x", "draw", "tie"}:
                    draw_odds = odds
            else:
                if outcome_id in {"1", "4"} or label in {"home", "home winner", "1"}:
                    home_odds = odds
                elif outcome_id in {"2", "5"} or label in {"away", "away winner", "2"}:
                    away_odds = odds
        if home_odds is not None or away_odds is not None:
            break

    return home_odds, draw_odds, away_odds


def _parse_event(event: dict[str, Any], league_name: str, sport: str) -> dict | None:
    home = _name_from(event.get("homeTeamName") or event.get("homeTeam") or event.get("home"))
    away = _name_from(event.get("awayTeamName") or event.get("awayTeam") or event.get("away"))
    if not home or not away or home.lower() == away.lower():
        return None

    home_odds, draw_odds, away_odds = _extract_market_values(event, sport)
    event_id = str(event.get("eventId") or event.get("eventID") or event.get("id") or "").strip()
    if not event_id and event.get("event"):
        nested = event.get("event")
        if isinstance(nested, dict):
            event_id = str(nested.get("id") or nested.get("eventId") or "").strip()

    return {
        "home_team": home[:80],
        "away_team": away[:80],
        "league": str(league_name or event.get("_league") or "Unknown")[:80],
        "sport": sport,
        "match_date": _match_time(event),
        "home_odds": home_odds,
        "draw_odds": draw_odds,
        "away_odds": away_odds,
        "sportybet_match_id": event_id,
        "source": "sportybet",
    }


def _dedupe(fixtures: list[dict]) -> list[dict]:
    unique: dict[tuple, dict] = {}
    for item in fixtures:
        key = item.get("sportybet_match_id") or (
            item.get("sport"),
            str(item.get("home_team", "")).lower(),
            str(item.get("away_team", "")).lower(),
            item.get("match_date"),
        )
        unique[key] = item
    return list(unique.values())


def _parse_payload(payload: Any, sport: str, limit: int) -> list[dict]:
    results: list[dict] = []
    for event in _iter_event_dicts(payload):
        parsed = _parse_event(event, str(event.get("_league") or "Unknown"), sport)
        if parsed:
            results.append(parsed)
            if len(results) >= limit:
                break
    return _dedupe(results)[:limit]


def fetch_upcoming_fixtures(sport: str = "soccer", limit: int = 100) -> list[dict]:
    """Fetch current public pre-match events and odds."""
    sport = sport if sport in SPORT_IDS else "soccer"
    limit = min(max(int(limit), 1), 100)

    payload = _fetch_current(sport, limit)
    source = "direct"
    if payload is None:
        payload = _fetch_via_relay(sport, limit)
        source = "relay"

    if payload is None:
        log.warning("SportyBet unavailable for %s; continuing without this source", sport)
        return []

    fixtures = _parse_payload(payload, sport, limit)
    log.info("SportyBet %s: parsed %d fixtures via %s", sport, len(fixtures), source)
    return fixtures


def fetch_all_sports(sports: list[str] | None = None, limit_per_sport: int = 50) -> list[dict]:
    """Fetch upcoming fixtures across supported SportyBet sports."""
    target = sports or ["soccer", "basketball", "tennis", "american_football", "hockey", "baseball", "cricket"]
    all_fixtures: list[dict] = []
    for sport in target:
        try:
            all_fixtures.extend(fetch_upcoming_fixtures(sport, limit=limit_per_sport))
        except Exception as exc:
            log.warning("SportyBet %s fetch failed: %s", sport, exc)
        time.sleep(0.5)
    return _dedupe(all_fixtures)


def ingest_upcoming_fixtures(db, sports: list[str] | None = None, limit_per_sport: int = 75) -> int:
    """Persist SportyBet upcoming fixtures into the shared fixture pool."""
    from app.db.models import Fixture
    from app.scraper.loaders import upsert_fixture
    from app.services.data_quality import resolve_team_name
    import pandas as pd

    count = 0
    for item in fetch_all_sports(sports=sports, limit_per_sport=limit_per_sport):
        try:
            kickoff = pd.to_datetime(item.get("match_date"), errors="coerce", utc=True)
            if pd.isna(kickoff):
                continue
            sport = str(item.get("sport") or "soccer").strip().lower()
            home = str(item.get("home_team") or "").strip()
            away = str(item.get("away_team") or "").strip()
            if not home or not away:
                continue
            fx = Fixture(
                sport=sport,
                league=str(item.get("league") or "Unknown")[:80],
                season=str(kickoff.year),
                match_date=kickoff.date(),
                home_team=resolve_team_name(db, home, sport, "sportybet"),
                away_team=resolve_team_name(db, away, sport, "sportybet"),
                home_odds=item.get("home_odds"),
                draw_odds=item.get("draw_odds"),
                away_odds=item.get("away_odds"),
                source="sportybet",
                extra={
                    "sportybet_match_id": item.get("sportybet_match_id"),
                    "provider": "sportybet",
                },
            )
            upsert_fixture(db, fx)
            count += 1
        except Exception:
            log.exception("SportyBet fixture persistence failed")
    db.commit()
    return count
