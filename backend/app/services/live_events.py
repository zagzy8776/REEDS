"""Live match event ingestion and notification dispatch.

Pulls goals, cards, substitutions, and lineups from API-Football when the
shared live provider is not configured. Stores new events in match_events /
match_lineups tables and queues SSE notifications to connected clients.
"""

import logging
from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.models import Fixture, MatchEvent, MatchLineup
from app.scraper.api_clients import ApiFootballClient

log = logging.getLogger(__name__)

_event_queue: dict[int, list[dict]] = {}
_stream_sequence = 1_000_000_000


def push_live_event(fixture_id: int, event: dict) -> None:
    """Push a live event into the in-memory SSE queue with a monotonic id.

    Persisted MatchEvent ids are retained, while transient score/stat/state
    updates receive a separate stream id so SSE clients cannot silently miss
    them when using the ``since`` cursor.
    """
    global _stream_sequence
    payload = dict(event)
    event_id = payload.get("id")
    if not event_id:
        _stream_sequence += 1
        event_id = _stream_sequence
        payload["id"] = event_id
    else:
        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            _stream_sequence += 1
            event_id = _stream_sequence
            payload["id"] = event_id
        else:
            _stream_sequence = max(_stream_sequence, event_id)

    # A persisted DB id can be lower than the transient stream cursor. That
    # would make a later transient event invisible to a client. Give every
    # queued event a strictly increasing stream sequence while preserving its
    # database id separately when one exists.
    if _event_queue.get(fixture_id):
        last_id = int(_event_queue[fixture_id][-1].get("stream_id", _stream_sequence) or 0)
        if event_id <= last_id:
            _stream_sequence = max(_stream_sequence, last_id) + 1
            payload["db_id"] = payload.get("id")
            payload["id"] = _stream_sequence
    payload["stream_id"] = int(payload["id"])
    _event_queue.setdefault(fixture_id, []).append(payload)
    _event_queue[fixture_id] = _event_queue[fixture_id][-100:]


def pop_events_since(fixture_id: int, since_id: int) -> list[dict]:
    """Return queued events that arrived after since_id."""
    events = _event_queue.get(fixture_id, [])
    return [e for e in events if int(e.get("stream_id", e.get("id", 0)) or 0) > since_id]


_EVENT_TYPE_MAP = {"Goal": "goal", "Card": None, "subst": "substitution", "Var": "var", "Missed Penalty": "penalty_missed"}
_CARD_DETAIL_MAP = {"Yellow Card": "yellow_card", "Red Card": "red_card", "Yellow Red Card": "red_card"}


def _normalize_event_type(raw_type: str, detail: str) -> str:
    if raw_type == "Card":
        return _CARD_DETAIL_MAP.get(detail, "card")
    return _EVENT_TYPE_MAP.get(raw_type, raw_type.lower().replace(" ", "_"))


def _upsert_event(db: Session, fixture_id: int, event_type: str, minute: int | None, team: str | None, player: str | None, assist: str | None, detail: str | None, home_score: int | None, away_score: int | None, extra: dict | None = None) -> MatchEvent | None:
    try:
        ev = MatchEvent(fixture_id=fixture_id, event_type=event_type, minute=minute, team=team, player=player or "Unknown", assist=assist, detail=detail, home_score_at=home_score, away_score_at=away_score, extra=extra, created_at=datetime.utcnow())
        db.add(ev)
        db.flush()
        return ev
    except IntegrityError:
        db.rollback()
        return None


def sync_live_events(db: Session, api_key: str | None) -> dict:
    """Pull live events for all in-progress soccer fixtures today."""
    if not api_key:
        return {"new_events": 0, "fixtures_checked": 0}

    client = ApiFootballClient(api_key)
    new_events = 0
    fixtures_checked = 0
    live_statuses = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}
    live_fixtures = db.query(Fixture).filter(Fixture.match_date == date.today(), Fixture.sport == "soccer").all()
    live_fixtures = [fx for fx in live_fixtures if isinstance(fx.extra, dict) and (fx.extra.get("live") or str(fx.extra.get("status", "")).upper() in live_statuses)]

    for fx in live_fixtures:
        api_fixture_id = (fx.extra or {}).get("api_fixture_id")
        if not api_fixture_id:
            continue
        fixtures_checked += 1
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
                ev = _upsert_event(db, fx.id, event_type, minute, team_data.get("name"), player_data.get("name"), assist_data.get("name"), detail, fx.home_score, fx.away_score, {"api_fixture_id": api_fixture_id, "comments": item.get("comments")})
                if ev:
                    new_events += 1
                    push_live_event(fx.id, {"id": ev.id, "fixture_id": fx.id, "event_type": event_type, "minute": minute, "team": team_data.get("name"), "player": player_data.get("name"), "assist": assist_data.get("name"), "detail": detail, "home_score": fx.home_score, "away_score": fx.away_score, "home_team": fx.home_team, "away_team": fx.away_team, "league": fx.league, "timestamp": datetime.utcnow().isoformat()})
        except Exception:
            log.exception("Event sync failed for fixture %d", fx.id)

        try:
            existing_lineup = db.query(MatchLineup).filter(MatchLineup.fixture_id == fx.id).first()
            if not existing_lineup:
                lineup_payload = client.fixture_lineups(int(api_fixture_id))
                for team_data in lineup_payload.get("response", []) or []:
                    team_name = (team_data.get("team") or {}).get("name", "")
                    formation = team_data.get("formation")
                    for player in (team_data.get("startXI", []) or []) + (team_data.get("substitutes", []) or []):
                        p = player.get("player", {})
                        is_starter = player in (team_data.get("startXI", []) or [])
                        try:
                            db.add(MatchLineup(fixture_id=fx.id, team=team_name, player=p.get("name", ""), position=p.get("pos"), number=p.get("number"), is_starter=is_starter, formation=formation))
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
