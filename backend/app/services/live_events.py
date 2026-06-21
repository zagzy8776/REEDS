"""Live match event ingestion and notification dispatch.

Pulls goals, cards, substitutions, and lineups from API-Football every
60 seconds during live fixtures. Stores new events in match_events /
match_lineups tables and queues SSE notifications to connected clients.
"""

import logging
from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.models import Fixture, MatchEvent, MatchLineup
from app.scraper.api_clients import ApiFootballClient

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process SSE event queue — lightweight, no Redis needed on free tier
# ---------------------------------------------------------------------------
# Maps fixture_id -> list of (event_dict) not yet consumed by SSE clients.
# SSE clients pop from this when they reconnect or poll.
_event_queue: dict[int, list[dict]] = {}


def push_live_event(fixture_id: int, event: dict) -> None:
    """Push a new event into the in-memory SSE queue."""
    _event_queue.setdefault(fixture_id, []).append(event)
    # Cap queue at 50 events per fixture to avoid memory leak
    _event_queue[fixture_id] = _event_queue[fixture_id][-50:]


def pop_events_since(fixture_id: int, since_id: int) -> list[dict]:
    """Return events for a fixture that arrived after since_id."""
    events = _event_queue.get(fixture_id, [])
    return [e for e in events if e.get("id", 0) > since_id]


# ---------------------------------------------------------------------------
# API-Football event ingestion
# ---------------------------------------------------------------------------

_EVENT_TYPE_MAP = {
    "Goal": "goal",
    "Card": None,          # split on detail below
    "subst": "substitution",
    "Var": "var",
    "Missed Penalty": "penalty_missed",
}

_CARD_DETAIL_MAP = {
    "Yellow Card": "yellow_card",
    "Red Card": "red_card",
    "Yellow Red Card": "red_card",  # second yellow = red
}


def _normalize_event_type(raw_type: str, detail: str) -> str:
    if raw_type == "Card":
        return _CARD_DETAIL_MAP.get(detail, "card")
    return _EVENT_TYPE_MAP.get(raw_type, raw_type.lower().replace(" ", "_"))


def _upsert_event(db: Session, fixture_id: int, event_type: str,
                  minute: int | None, team: str | None, player: str | None,
                  assist: str | None, detail: str | None,
                  home_score: int | None, away_score: int | None,
                  extra: dict | None = None) -> MatchEvent | None:
    """Insert event, skip on duplicate key. Returns new row or None."""
    try:
        ev = MatchEvent(
            fixture_id=fixture_id,
            event_type=event_type,
            minute=minute,
            team=team,
            player=player or "Unknown",
            assist=assist,
            detail=detail,
            home_score_at=home_score,
            away_score_at=away_score,
            extra=extra,
            created_at=datetime.utcnow(),
        )
        db.add(ev)
        db.flush()
        return ev
    except IntegrityError:
        db.rollback()
        return None


def sync_live_events(db: Session, api_key: str | None) -> dict:
    """Pull live events for all in-progress fixtures today.

    Called every 60 seconds by the scheduler. Fetches:
    - Match events (goals, cards, subs) via /fixtures/events
    - Lineups via /fixtures/lineups (once per fixture, skipped if already loaded)

    Returns counts of new events stored.
    """
    if not api_key:
        return {"new_events": 0, "fixtures_checked": 0}

    today = date.today().isoformat()
    client = ApiFootballClient(api_key)
    new_events = 0
    fixtures_checked = 0

    # Find in-progress fixtures (status live or HT)
    live_statuses = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}
    live_fixtures = (
        db.query(Fixture)
        .filter(
            Fixture.match_date == date.today(),
            Fixture.sport == "soccer",
        )
        .all()
    )
    live_fixtures = [
        fx for fx in live_fixtures
        if isinstance(fx.extra, dict) and (
            fx.extra.get("live") or
            str(fx.extra.get("status", "")).upper() in live_statuses
        )
    ]

    for fx in live_fixtures:
        api_fixture_id = (fx.extra or {}).get("api_fixture_id")
        if not api_fixture_id:
            continue
        fixtures_checked += 1

        # --- Events ---
        try:
            payload = client.fixture_events(int(api_fixture_id))
            for item in payload.get("response", []) or []:
                time_data = item.get("time", {})
                minute = time_data.get("elapsed")
                team_data = item.get("team", {})
                player_data = item.get("player", {})
                assist_data = item.get("assist", {})
                raw_type = str(item.get("type", ""))
                detail = str(item.get("detail", ""))
                event_type = _normalize_event_type(raw_type, detail)

                # Current score at time of event
                home_score = fx.home_score
                away_score = fx.away_score

                ev = _upsert_event(
                    db,
                    fixture_id=fx.id,
                    event_type=event_type,
                    minute=minute,
                    team=team_data.get("name"),
                    player=player_data.get("name"),
                    assist=assist_data.get("name"),
                    detail=detail,
                    home_score=home_score,
                    away_score=away_score,
                    extra={"api_fixture_id": api_fixture_id, "comments": item.get("comments")},
                )
                if ev:
                    new_events += 1
                    # Push to SSE queue
                    push_live_event(fx.id, {
                        "id": ev.id,
                        "fixture_id": fx.id,
                        "event_type": event_type,
                        "minute": minute,
                        "team": team_data.get("name"),
                        "player": player_data.get("name"),
                        "assist": assist_data.get("name"),
                        "detail": detail,
                        "home_score": home_score,
                        "away_score": away_score,
                        "home_team": fx.home_team,
                        "away_team": fx.away_team,
                        "league": fx.league,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
        except Exception:
            log.exception("Event sync failed for fixture %d", fx.id)

        # --- Lineups (fetch once) ---
        try:
            existing_lineup = db.query(MatchLineup).filter(
                MatchLineup.fixture_id == fx.id
            ).first()
            if not existing_lineup:
                lineup_payload = client.fixture_lineups(int(api_fixture_id))
                for team_data in lineup_payload.get("response", []) or []:
                    team_name = (team_data.get("team") or {}).get("name", "")
                    formation = team_data.get("formation")
                    for player in team_data.get("startXI", []) or []:
                        p = player.get("player", {})
                        try:
                            db.add(MatchLineup(
                                fixture_id=fx.id,
                                team=team_name,
                                player=p.get("name", ""),
                                position=p.get("pos"),
                                number=p.get("number"),
                                is_starter=True,
                                formation=formation,
                            ))
                            db.flush()
                        except IntegrityError:
                            db.rollback()
                    for player in team_data.get("substitutes", []) or []:
                        p = player.get("player", {})
                        try:
                            db.add(MatchLineup(
                                fixture_id=fx.id,
                                team=team_name,
                                player=p.get("name", ""),
                                position=p.get("pos"),
                                number=p.get("number"),
                                is_starter=False,
                                formation=formation,
                            ))
                            db.flush()
                        except IntegrityError:
                            db.rollback()
        except Exception:
            log.exception("Lineup sync failed for fixture %d", fx.id)

    try:
        db.commit()
    except Exception:
        db.rollback()

    return {"new_events": new_events, "fixtures_checked": fixtures_checked}
