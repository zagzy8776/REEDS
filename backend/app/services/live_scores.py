"""Low-cost live score/state synchronization.

The live center must not make one upstream request per browser. This service
uses the configured AllSports live endpoint as a single shared feed, updates
tracked fixtures, stores the latest real statistics, and pushes score/state
changes into the existing SSE queue.

Provider data is treated as authoritative. We never invent player names,
xG, possession, momentum, or other statistics that were not supplied.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.scraper.http_client import HttpClient
from app.services.live_events import push_live_event

log = logging.getLogger(__name__)

_LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}


def _int_or_none(value):
    try:
        if value in (None, "", "-"):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalise_stats(raw) -> dict:
    """Convert provider statistics into a stable {metric: {home, away}} shape."""
    if not isinstance(raw, list):
        return {}
    stats: dict[str, dict[str, int | float | str | None]] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        metric = str(row.get("type") or row.get("name") or "").strip()
        if not metric:
            continue
        home = row.get("home")
        away = row.get("away")
        stats[metric] = {"home": home, "away": away}
    return stats


def _match_fixture(db: Session, item: dict) -> Fixture | None:
    event_key = str(item.get("event_key") or "").strip()
    if event_key:
        rows = db.query(Fixture).filter(Fixture.match_date == date.today()).all()
        for fx in rows:
            extra = fx.extra if isinstance(fx.extra, dict) else {}
            if str(extra.get("allsports_event_key") or "") == event_key:
                return fx

    home = str(item.get("event_home_team") or "").strip()
    away = str(item.get("event_away_team") or "").strip()
    if not home or not away:
        return None
    return (
        db.query(Fixture)
        .filter(
            Fixture.match_date == date.today(),
            Fixture.home_team == home,
            Fixture.away_team == away,
        )
        .first()
    )


def sync_allsports_live(db: Session, api_key: str | None, sport: str = "football") -> dict:
    """Synchronize the provider's live feed in one request.

    AllSports exposes a Livescore endpoint containing the current score,
    status/minute and (where supplied) match statistics. One response is
    shared across every REEDS browser instead of polling once per user.
    """
    if not api_key:
        return {"provider": "allsportsapi", "checked": 0, "updated": 0, "score_changes": 0, "stats_updates": 0, "skipped": "not configured"}

    client = HttpClient()
    base_url = "https://apiv2.allsportsapi.com" if sport == "football" else "https://apiv2.allsportsapi.com"
    try:
        payload = client.get(
            f"{base_url}/{sport}",
            params={"met": "Livescore", "APIkey": api_key, "withPlayerStats": "1"},
        ).json()
    except Exception as exc:
        log.warning("AllSports live feed failed: %s", exc)
        return {"provider": "allsportsapi", "checked": 0, "updated": 0, "score_changes": 0, "stats_updates": 0, "errors": [str(exc)[:300]]}

    events = payload.get("result", []) if isinstance(payload, dict) else []
    if not isinstance(events, list):
        events = []

    checked = updated = score_changes = stats_updates = 0
    for item in events:
        if not isinstance(item, dict):
            continue
        fx = _match_fixture(db, item)
        if not fx:
            continue
        checked += 1

        old_home = fx.home_score
        old_away = fx.away_score
        home_score = _int_or_none(item.get("event_current_home_score"))
        away_score = _int_or_none(item.get("event_current_away_score"))
        if home_score is None or away_score is None:
            result = str(item.get("event_final_result") or "")
            parts = result.replace(":", "-").split("-")
            if len(parts) == 2:
                home_score = home_score if home_score is not None else _int_or_none(parts[0].strip())
                away_score = away_score if away_score is not None else _int_or_none(parts[1].strip())

        status = str(item.get("event_status") or "").strip()
        is_live = str(item.get("event_live") or "0") == "1"
        elapsed = _int_or_none(status) if status.isdigit() else None
        stats = _normalise_stats(item.get("statistics"))

        extra = dict(fx.extra or {})
        extra.update({
            "allsports_event_key": item.get("event_key"),
            "status": status or extra.get("status"),
            "live": is_live or status.upper() in _LIVE_STATUSES,
            "elapsed": elapsed if elapsed is not None else extra.get("elapsed"),
            "live_last_synced_at": datetime.utcnow().isoformat(),
            "live_provider": "allsportsapi",
        })
        if stats:
            extra["live_stats"] = stats
            extra["live_stats_updated_at"] = datetime.utcnow().isoformat()
            stats_updates += 1

        changed = False
        if home_score is not None and home_score != old_home:
            fx.home_score = home_score
            changed = True
        if away_score is not None and away_score != old_away:
            fx.away_score = away_score
            changed = True
        if status:
            changed = changed or extra.get("status") != (fx.extra or {}).get("status")
        fx.extra = extra

        if changed or stats:
            updated += 1

        if (old_home != fx.home_score or old_away != fx.away_score):
            score_changes += 1
            push_live_event(fx.id, {
                "id": 0,
                "fixture_id": fx.id,
                "event_type": "score_update",
                "minute": elapsed,
                "team": None,
                "player": None,
                "assist": None,
                "detail": "Live score updated",
                "home_score": fx.home_score,
                "away_score": fx.away_score,
                "home_team": fx.home_team,
                "away_team": fx.away_team,
                "league": fx.league,
                "timestamp": datetime.utcnow().isoformat(),
            })

        if stats:
            push_live_event(fx.id, {
                "id": 0,
                "fixture_id": fx.id,
                "event_type": "stats_update",
                "minute": elapsed,
                "stats": stats,
                "home_score": fx.home_score,
                "away_score": fx.away_score,
                "timestamp": datetime.utcnow().isoformat(),
            })

    db.commit()
    return {
        "provider": "allsportsapi",
        "checked": checked,
        "updated": updated,
        "score_changes": score_changes,
        "stats_updates": stats_updates,
    }
