"""SportyBet scraper — fetches upcoming fixtures and their live odds.

Uses requests + BeautifulSoup for the JSON API that SportyBet's frontend calls
internally. This is far more stable than scraping rendered HTML and doesn't
require a headless browser, which is important on Render's free tier where
Playwright/Selenium can't run without extra setup.

SportyBet exposes a REST API at the same endpoints their mobile app uses.
We target those directly with standard HTTP headers to look like the app.

IMPORTANT — Terms of Service:
  Scraping for personal/research use is technically possible but may violate
  SportyBet's ToS. Use this for model training and value detection only.
  Do not automate bet placement. Do not republish their odds commercially.
"""

import logging
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.sportybet.com",
    "Referer": "https://www.sportybet.com/",
    "x-app-name": "sportybet",
}

# SportyBet internal API base — this is the same endpoint their mobile web app uses
_BASE = "https://www.sportybet.com/api/ng"

# Sport IDs used by SportyBet's API
SPORT_IDS = {
    "soccer": "sr:sport:1",
    "basketball": "sr:sport:2",
    "tennis": "sr:sport:5",
    "american_football": "sr:sport:16",
    "hockey": "sr:sport:4",
    "baseball": "sr:sport:3",
    "cricket": "sr:sport:21",
}

# Market IDs: 1X2 = 1, Asian Handicap = 18, Over/Under = 18
_MARKET_1X2 = "1"
_MARKET_MONEYLINE = "219"


def _get(url: str, params: dict | None = None, retries: int = 2) -> dict | list | None:
    """Safe GET with retries and rate-limit awareness."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=_HEADERS, params=params, timeout=15)
            if resp.status_code == 429:
                log.warning("SportyBet rate limit hit, sleeping 10s")
                time.sleep(10)
                continue
            if resp.status_code != 200:
                log.warning("SportyBet %s returned %d", url, resp.status_code)
                return None
            return resp.json()
        except Exception as exc:
            log.warning("SportyBet request failed (attempt %d): %s", attempt + 1, exc)
            time.sleep(2)
    return None


def fetch_upcoming_fixtures(sport: str = "soccer", limit: int = 100) -> list[dict]:
    """Fetch upcoming matches with odds from SportyBet.

    Returns a list of normalised fixture dicts:
      {
        "home_team": str,
        "away_team": str,
        "league": str,
        "sport": str,
        "match_date": str (ISO),
        "home_odds": float | None,
        "draw_odds": float | None,
        "away_odds": float | None,
        "sportybet_match_id": str,
        "source": "sportybet",
      }
    """
    sport_id = SPORT_IDS.get(sport, SPORT_IDS["soccer"])
    results: list[dict] = []

    # SportyBet tournament list endpoint
    tournaments_url = f"{_BASE}/query/tournamentMarkets"
    params = {
        "sportId": sport_id,
        "marketId": _MARKET_1X2 if sport == "soccer" else _MARKET_MONEYLINE,
        "groupId": "0",
        "_t": int(time.time() * 1000),
    }

    data = _get(tournaments_url, params)
    if not data:
        # Alternate public listing endpoint when tournament metadata is unavailable.
        return _fetch_via_odds_endpoint(sport, limit)

    tournaments = []
    if isinstance(data, dict):
        tournaments = data.get("data", {}).get("tournaments", []) or data.get("tournaments", []) or []
    elif isinstance(data, list):
        tournaments = data

    for tournament in tournaments[:20]:
        t_id = tournament.get("id") or tournament.get("tournamentId")
        league_name = tournament.get("name") or tournament.get("tournamentName", "Unknown")

        if not t_id:
            continue

        events_url = f"{_BASE}/query/tournamentMarkets"
        event_params = {
            "sportId": sport_id,
            "tournamentId": t_id,
            "marketId": _MARKET_1X2 if sport == "soccer" else _MARKET_MONEYLINE,
            "_t": int(time.time() * 1000),
        }
        event_data = _get(events_url, event_params)
        if not event_data:
            continue

        events = []
        if isinstance(event_data, dict):
            events = (event_data.get("data", {}) or {}).get("events", []) or event_data.get("events", []) or []

        for event in events:
            try:
                parsed = _parse_event(event, league_name, sport)
                if parsed:
                    results.append(parsed)
                    if len(results) >= limit:
                        return results
            except Exception:
                continue

        time.sleep(0.3)

    return results


def _fetch_via_odds_endpoint(sport: str, limit: int) -> list[dict]:
    """Use SportyBet's main event listing endpoint when tournaments are unavailable."""
    sport_id = SPORT_IDS.get(sport, SPORT_IDS["soccer"])
    url = f"{_BASE}/query/sportEvents"
    params = {
        "sportId": sport_id,
        "time": "today",
        "_t": int(time.time() * 1000),
    }
    data = _get(url, params)
    if not data:
        return []

    results = []
    events = []
    if isinstance(data, dict):
        events = data.get("data", {}).get("events", []) or data.get("events", []) or []
    for event in events:
        parsed = _parse_event(event, event.get("tournamentName", "Unknown"), sport)
        if parsed:
            results.append(parsed)
            if len(results) >= limit:
                break
    return results


def _parse_event(event: dict, league_name: str, sport: str) -> dict | None:
    """Extract fixture + odds from a SportyBet event object."""
    try:
        home = event.get("homeTeamName") or event.get("home", {}).get("name", "")
        away = event.get("awayTeamName") or event.get("away", {}).get("name", "")
        if not home or not away:
            return None

        start_time = event.get("estimateStartTime") or event.get("startTime") or event.get("matchTime")
        match_dt = None
        if start_time:
            try:
                if isinstance(start_time, (int, float)) and start_time > 1e10:
                    match_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc).isoformat()
                else:
                    match_dt = str(start_time)
            except Exception:
                match_dt = str(start_time)

        home_odds = draw_odds = away_odds = None
        markets = event.get("markets", []) or event.get("odds", []) or []

        for market in markets:
            market_name = str(market.get("name", "")).lower()
            market_id = str(market.get("id", ""))

            is_1x2 = market_id == "1" or "1x2" in market_name or "match result" in market_name or "match winner" in market_name
            is_ml = market_id == "219" or "moneyline" in market_name or "winner" in market_name

            if not (is_1x2 or is_ml):
                continue

            outcomes = market.get("outcomes", []) or market.get("selections", []) or []
            for outcome in outcomes:
                name = str(outcome.get("desc", "") or outcome.get("name", "")).lower()
                odds_val = None
                raw_odds = outcome.get("odds") or outcome.get("price")
                if raw_odds:
                    try:
                        f = float(raw_odds)
                        odds_val = f / 100 if f > 100 else f
                    except (TypeError, ValueError):
                        pass

                if name in ("1", "home", "home win", "w1") or name == home.lower():
                    home_odds = odds_val
                elif name in ("x", "draw", "tie"):
                    draw_odds = odds_val
                elif name in ("2", "away", "away win", "w2") or name == away.lower():
                    away_odds = odds_val

            if home_odds or away_odds:
                break

        return {
            "home_team": str(home).strip(),
            "away_team": str(away).strip(),
            "league": str(league_name)[:80],
            "sport": sport,
            "match_date": match_dt,
            "home_odds": home_odds,
            "draw_odds": draw_odds,
            "away_odds": away_odds,
            "sportybet_match_id": str(event.get("eventId") or event.get("id") or ""),
            "source": "sportybet",
        }
    except Exception as exc:
        log.debug("Failed to parse SportyBet event: %s", exc)
        return None


def fetch_all_sports(sports: list[str] | None = None, limit_per_sport: int = 50) -> list[dict]:
    """Fetch upcoming fixtures across all supported SportyBet sports."""
    target = sports or [
        "soccer", "basketball", "tennis", "american_football",
        "hockey", "baseball", "cricket",
    ]
    all_fixtures = []
    for sport in target:
        try:
            fixtures = fetch_upcoming_fixtures(sport, limit=limit_per_sport)
            all_fixtures.extend(fixtures)
            log.info("SportyBet: fetched %d %s fixtures", len(fixtures), sport)
            time.sleep(1)
        except Exception as exc:
            log.warning("SportyBet %s fetch failed: %s", sport, exc)
    return all_fixtures
