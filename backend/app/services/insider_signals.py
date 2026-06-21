"""
Insider Signals Service
=======================
Collects and stores high-value pre-match intelligence that boosts ML accuracy:

  - Referee tendencies (cards/fouls per match) from public football-data sources
  - Sharp line movements (OddsSnapshot closing vs opening diff)
  - Weather context (OpenMeteo free API — no key needed)
  - Injury flags (parsed from fixture extra fields set by API-Football)
  - Public betting % imbalance (from The Odds API public endpoint)

All signals are stored in insider_signals and joined into features at
prediction time via get_fixture_signals().
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import requests
import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture, InsiderSignal, OddsSnapshot

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Upsert helper
# ---------------------------------------------------------------------------

def _upsert_signal(
    db: Session,
    fixture_id: int,
    sport: str,
    signal_type: str,
    source: str,
    value: float | None = None,
    direction: str | None = None,
    description: str | None = None,
    extra: dict | None = None,
) -> None:
    existing = (
        db.query(InsiderSignal)
        .filter_by(fixture_id=fixture_id, signal_type=signal_type, source=source)
        .first()
    )
    if existing:
        existing.value       = value
        existing.direction   = direction
        existing.description = description
        existing.extra       = extra
        existing.captured_at = datetime.utcnow()
    else:
        db.add(InsiderSignal(
            fixture_id=fixture_id, sport=sport,
            signal_type=signal_type, source=source,
            value=value, direction=direction,
            description=description, extra=extra,
        ))


# ---------------------------------------------------------------------------
# 1. Sharp line movement — computed from OddsSnapshot table
# ---------------------------------------------------------------------------

def compute_sharp_line_moves(db: Session, fixture_id: int, sport: str) -> None:
    """Compare earliest vs latest odds snapshot; flag significant moves as signals."""
    snaps = (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.fixture_id == fixture_id, OddsSnapshot.market == "1X2")
        .order_by(OddsSnapshot.captured_at.asc())
        .all()
    )
    if len(snaps) < 2:
        return

    first, last = snaps[0], snaps[-1]

    def _implied(odds: float | None) -> float:
        return (1 / odds) if odds and odds > 1 else 0.0

    home_move = _implied(last.home_odds) - _implied(first.home_odds)
    away_move = _implied(last.away_odds) - _implied(first.away_odds)

    # Threshold: 3%+ implied probability shift is considered sharp
    if abs(home_move) >= 0.03:
        _upsert_signal(
            db, fixture_id, sport,
            signal_type="sharp_line_move",
            source="odds_snapshot",
            value=round(home_move, 4),
            direction="home" if home_move > 0 else "away",
            description=f"Home implied prob moved {home_move:+.1%} from open to close",
            extra={"open_home": first.home_odds, "close_home": last.home_odds},
        )
    if abs(away_move) >= 0.03:
        _upsert_signal(
            db, fixture_id, sport,
            signal_type="sharp_line_move",
            source="odds_snapshot",
            value=round(away_move, 4),
            direction="away" if away_move > 0 else "home",
            description=f"Away implied prob moved {away_move:+.1%} from open to close",
            extra={"open_away": first.away_odds, "close_away": last.away_odds},
        )


# ---------------------------------------------------------------------------
# 2. Weather context — OpenMeteo (free, no key)
# ---------------------------------------------------------------------------

# Approximate lat/lon for common stadium cities (add more as needed)
CITY_COORDS: dict[str, tuple[float, float]] = {
    # England
    "london":        (51.505, -0.091),
    "manchester":    (53.480, -2.243),
    "liverpool":     (53.408, -2.991),
    "birmingham":    (52.486, -1.890),
    "leeds":         (53.800, -1.549),
    "newcastle":     (54.975, -1.613),
    # Spain
    "madrid":        (40.416, -3.703),
    "barcelona":     (41.385, 2.173),
    "seville":       (37.389, -5.984),
    # Germany
    "berlin":        (52.520, 13.405),
    "munich":        (48.137, 11.576),
    "dortmund":      (51.514, 7.468),
    # Italy
    "milan":         (45.464, 9.190),
    "rome":          (41.902, 12.496),
    "naples":        (40.852, 14.268),
    "turin":         (45.071, 7.686),
    # France
    "paris":         (48.856, 2.352),
    "marseille":     (43.296, 5.381),
    "lyon":          (45.748, 4.847),
    # USA
    "new york":      (40.713, -74.006),
    "los angeles":   (34.052, -118.244),
    "chicago":       (41.878, -87.630),
    "dallas":        (32.776, -96.796),
    # Default fallback — London
    "__default__":   (51.505, -0.091),
}


def _city_from_league(league: str) -> str:
    league_lower = league.lower()
    for city in CITY_COORDS:
        if city != "__default__" and city in league_lower:
            return city
    return "__default__"


def fetch_weather_signal(
    db: Session,
    fixture: Fixture,
) -> None:
    """Pull forecast from OpenMeteo for the fixture date/location (free, no key)."""
    try:
        city = _city_from_league(fixture.league or "")
        lat, lon = CITY_COORDS[city]
        match_date = str(fixture.match_date)

        url = "https://api.open-meteo.com/v1/forecast"
        resp = requests.get(url, params={
            "latitude":          lat,
            "longitude":         lon,
            "daily":             "precipitation_sum,windspeed_10m_max,temperature_2m_max",
            "start_date":        match_date,
            "end_date":          match_date,
            "timezone":          "auto",
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        daily = data.get("daily", {})

        precip  = (daily.get("precipitation_sum") or [None])[0]
        wind    = (daily.get("windspeed_10m_max") or [None])[0]
        temp    = (daily.get("temperature_2m_max") or [None])[0]

        if precip is not None:
            _upsert_signal(
                db, fixture.id, fixture.sport,
                signal_type="weather",
                source="open_meteo",
                value=float(precip),
                direction="neutral",
                description=f"Precipitation {precip}mm, wind {wind}km/h, temp {temp}°C",
                extra={"wind_kmh": wind, "temp_c": temp, "precip_mm": precip, "city": city},
            )
    except Exception as exc:
        log.debug("Weather fetch failed for fixture %d: %s", fixture.id, exc)


# ---------------------------------------------------------------------------
# 3. Referee tendency — computed from historical match data in DB
# ---------------------------------------------------------------------------

def compute_referee_signal(
    db: Session,
    fixture: Fixture,
    referee_name: str | None = None,
) -> None:
    """Compute average cards/fouls for the referee across historical fixtures.

    Data source: fixture extra fields populated by API-Football (referee, cards).
    If we have ≥5 historical matches for this referee, write a signal.
    """
    if not referee_name:
        extra = fixture.extra if isinstance(fixture.extra, dict) else {}
        referee_name = extra.get("referee")
    if not referee_name:
        return

    # Scan fixture extra fields for same referee with card data
    past = (
        db.query(Fixture)
        .filter(
            Fixture.sport == fixture.sport,
            Fixture.match_date < fixture.match_date,
        )
        .limit(5000)
        .all()
    )

    total_cards = 0
    n = 0
    for f in past:
        ex = f.extra if isinstance(f.extra, dict) else {}
        if ex.get("referee") != referee_name:
            continue
        cards = ex.get("home_yellow", 0) + ex.get("away_yellow", 0) + \
                ex.get("home_red", 0) + ex.get("away_red", 0)
        total_cards += int(cards or 0)
        n += 1

    if n >= 5:
        avg_cards = round(total_cards / n, 2)
        _upsert_signal(
            db, fixture.id, fixture.sport,
            signal_type="referee",
            source="historical_fixtures",
            value=avg_cards,
            direction="neutral",
            description=f"Referee {referee_name}: avg {avg_cards:.1f} cards/match ({n} matches)",
            extra={"referee": referee_name, "sample_size": n},
        )


# ---------------------------------------------------------------------------
# 4. Injury flags — parsed from API-Football fixture extra data
# ---------------------------------------------------------------------------

def parse_injury_signals(db: Session, fixture: Fixture) -> None:
    """Extract injury/suspension flags stored in fixture extra by API-Football loader."""
    extra = fixture.extra if isinstance(fixture.extra, dict) else {}

    injuries = extra.get("injuries", []) or []
    for inj in injuries:
        if not isinstance(inj, dict):
            continue
        team_side = inj.get("team_side", "home")   # "home" or "away"
        player    = inj.get("player", "Unknown")
        reason    = inj.get("type", "injured")
        severity  = 1.0 if "out" in str(reason).lower() else 0.5

        signal_type = f"injury_{team_side}"
        _upsert_signal(
            db, fixture.id, fixture.sport,
            signal_type=signal_type,
            source="api_football_injury",
            value=severity,
            direction=team_side,
            description=f"{player} — {reason}",
            extra={"player": player, "reason": reason, "severity": severity},
        )


# ---------------------------------------------------------------------------
# 5. Public betting % imbalance — from The Odds API (when configured)
# ---------------------------------------------------------------------------

def fetch_public_betting_signal(
    db: Session,
    fixture: Fixture,
    api_key: str | None,
) -> None:
    """Pull public betting % from The Odds API betPercents endpoint (free tier).
    
    Only available for NFL/NBA on the free tier; skips gracefully otherwise.
    """
    if not api_key:
        return
    sport_key_map = {
        "american_football": "americanfootball_nfl",
        "basketball":        "basketball_nba",
    }
    sport_key = sport_key_map.get(fixture.sport)
    if not sport_key:
        return
    try:
        resp = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us",
                "markets":    "h2h",
                "oddsFormat": "decimal",
            },
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json() if isinstance(resp.json(), list) else []
        for event in events:
            h = str(event.get("home_team", ""))
            a = str(event.get("away_team", ""))
            if h.lower() not in fixture.home_team.lower() and a.lower() not in fixture.away_team.lower():
                continue
            # "bookmakers" → first with bets that has "bet%"
            for bm in event.get("bookmakers", []):
                for market in bm.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: o for o in market.get("outcomes", [])}
                    home_pct = outcomes.get(h, {}).get("betPercent")
                    away_pct = outcomes.get(a, {}).get("betPercent")
                    if home_pct or away_pct:
                        _upsert_signal(
                            db, fixture.id, fixture.sport,
                            signal_type="public_betting",
                            source="the_odds_api",
                            value=float(home_pct or 0),
                            direction="home" if (home_pct or 0) > 55 else "away",
                            description=f"Public bets: {h} {home_pct}% / {a} {away_pct}%",
                            extra={"home_pct": home_pct, "away_pct": away_pct},
                        )
                        return
    except Exception as exc:
        log.debug("Public betting fetch failed: %s", exc)


# ---------------------------------------------------------------------------
# 6. Batch refresh — called by scheduler and admin endpoint
# ---------------------------------------------------------------------------

def refresh_insider_signals(
    db: Session,
    odds_api_key: str | None = None,
    days_ahead: int = 3,
) -> dict:
    """Refresh all insider signals for upcoming fixtures.

    Runs: sharp line moves, weather, injuries, public betting.
    Referee signals run lazily per-fixture at prediction time.
    """
    today = date.today()
    upcoming = (
        db.query(Fixture)
        .filter(
            Fixture.match_date >= today,
            Fixture.match_date <= today + timedelta(days=days_ahead),
            Fixture.home_score == None,
        )
        .order_by(Fixture.match_date.asc())
        .limit(200)
        .all()
    )

    counts = {"sharp_line": 0, "weather": 0, "injuries": 0, "public_betting": 0}

    for fx in upcoming:
        try:
            compute_sharp_line_moves(db, fx.id, fx.sport)
            counts["sharp_line"] += 1
        except Exception:
            pass
        try:
            fetch_weather_signal(db, fx)
            counts["weather"] += 1
        except Exception:
            pass
        try:
            parse_injury_signals(db, fx)
            counts["injuries"] += 1
        except Exception:
            pass
        try:
            fetch_public_betting_signal(db, fx, odds_api_key)
            counts["public_betting"] += 1
        except Exception:
            pass

    try:
        db.commit()
    except Exception:
        db.rollback()

    return {"fixtures_processed": len(upcoming), "signals": counts}


# ---------------------------------------------------------------------------
# 7. Feature lookup — called by ML feature builders at prediction time
# ---------------------------------------------------------------------------

def get_fixture_signals(db: Session, fixture_id: int) -> dict:
    """Return a flat feature dict from all InsiderSignal rows for a fixture.

    Keys match the feature names expected by the ML feature pipeline:
      insider_sharp_home_move  — home-side implied prob line move (+ = sharpening)
      insider_sharp_away_move  — away-side line move
      insider_weather_precip   — precipitation mm
      insider_weather_wind     — wind km/h
      insider_home_injury      — severity of home key player injury (0–1)
      insider_away_injury      — severity of away key player injury (0–1)
      insider_referee_cards    — avg cards/match for assigned referee
      insider_public_home_pct  — % of public bets on home side
    """
    signals = (
        db.query(InsiderSignal)
        .filter(InsiderSignal.fixture_id == fixture_id)
        .all()
    )

    features: dict[str, float] = {
        "insider_sharp_home_move": 0.0,
        "insider_sharp_away_move": 0.0,
        "insider_weather_precip":  0.0,
        "insider_weather_wind":    0.0,
        "insider_home_injury":     0.0,
        "insider_away_injury":     0.0,
        "insider_referee_cards":   3.0,   # league average default
        "insider_public_home_pct": 50.0,
    }

    for sig in signals:
        v = sig.value or 0.0
        d = sig.direction or "neutral"

        if sig.signal_type == "sharp_line_move":
            if d == "home":
                features["insider_sharp_home_move"] = v
            else:
                features["insider_sharp_away_move"] = v

        elif sig.signal_type == "weather":
            ex = sig.extra or {}
            features["insider_weather_precip"] = float(ex.get("precip_mm") or v or 0)
            features["insider_weather_wind"]   = float(ex.get("wind_kmh") or 0)

        elif sig.signal_type == "injury_home":
            features["insider_home_injury"] = max(features["insider_home_injury"], v)

        elif sig.signal_type == "injury_away":
            features["insider_away_injury"] = max(features["insider_away_injury"], v)

        elif sig.signal_type == "referee":
            features["insider_referee_cards"] = v

        elif sig.signal_type == "public_betting":
            features["insider_public_home_pct"] = float((sig.extra or {}).get("home_pct") or 50)

    return features
