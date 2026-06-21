"""Live match events API — SSE stream + REST endpoints.

Provides:
  GET  /api/live/events/{fixture_id}        — all stored events for a fixture
  GET  /api/live/lineups/{fixture_id}        — starting XI + bench
  GET  /api/live/stream/{fixture_id}         — SSE stream (new events pushed)
  GET  /api/live/matches                     — all currently live fixtures
  POST /api/live/subscribe                   — save push notification subscription
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Fixture, MatchEvent, MatchLineup, PushSubscription
from app.db.session import get_db
from app.services.live_events import pop_events_since

log = logging.getLogger(__name__)
router = APIRouter()

_LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}


def _is_live(fx: Fixture) -> bool:
    extra = fx.extra if isinstance(fx.extra, dict) else {}
    return bool(extra.get("live")) or str(extra.get("status", "")).upper() in _LIVE_STATUSES


def _event_label(event_type: str) -> str:
    """User-facing label — no internal jargon."""
    return {
        "goal": "⚽ Goal",
        "yellow_card": "🟨 Yellow Card",
        "red_card": "🟥 Red Card",
        "substitution": "🔄 Substitution",
        "var": "📺 VAR Review",
        "penalty_missed": "❌ Penalty Missed",
        "lineup": "📋 Lineup",
        "card": "🃏 Card",
    }.get(event_type, event_type.replace("_", " ").title())


def _serialize_event(ev: MatchEvent) -> dict:
    return {
        "id": ev.id,
        "event_type": ev.event_type,
        "label": _event_label(ev.event_type),
        "minute": ev.minute,
        "team": ev.team,
        "player": ev.player,
        "assist": ev.assist,
        "detail": ev.detail,
        "score": f"{ev.home_score_at} - {ev.away_score_at}" if ev.home_score_at is not None else None,
        "timestamp": ev.created_at.isoformat() if ev.created_at else None,
    }


@router.get("/live/matches")
def live_matches(db: Session = Depends(get_db)):
    """All matches currently in progress."""
    today = date.today()
    fixtures = db.query(Fixture).filter(Fixture.match_date == today).all()
    live = [fx for fx in fixtures if _is_live(fx)]
    return [
        {
            "id": fx.id,
            "sport": fx.sport,
            "league": fx.league,
            "home_team": fx.home_team,
            "away_team": fx.away_team,
            "home_score": fx.home_score,
            "away_score": fx.away_score,
            "status": (fx.extra or {}).get("status"),
            "elapsed": (fx.extra or {}).get("elapsed"),
            "home_odds": fx.home_odds,
            "draw_odds": fx.draw_odds,
            "away_odds": fx.away_odds,
        }
        for fx in live
    ]


@router.get("/live/events/{fixture_id}")
def fixture_events(fixture_id: int, db: Session = Depends(get_db)):
    """All stored events for a fixture (goals, cards, subs)."""
    events = (
        db.query(MatchEvent)
        .filter(MatchEvent.fixture_id == fixture_id)
        .order_by(MatchEvent.minute.asc(), MatchEvent.created_at.asc())
        .all()
    )
    fx = db.query(Fixture).filter(Fixture.id == fixture_id).first()
    return {
        "fixture_id": fixture_id,
        "home_team": fx.home_team if fx else None,
        "away_team": fx.away_team if fx else None,
        "home_score": fx.home_score if fx else None,
        "away_score": fx.away_score if fx else None,
        "is_live": _is_live(fx) if fx else False,
        "events": [_serialize_event(ev) for ev in events],
    }


@router.get("/live/lineups/{fixture_id}")
def fixture_lineups(fixture_id: int, db: Session = Depends(get_db)):
    """Starting XI and bench for a fixture."""
    lineups = (
        db.query(MatchLineup)
        .filter(MatchLineup.fixture_id == fixture_id)
        .order_by(MatchLineup.is_starter.desc(), MatchLineup.number.asc())
        .all()
    )
    fx = db.query(Fixture).filter(Fixture.id == fixture_id).first()
    by_team: dict[str, dict] = {}
    for p in lineups:
        team = by_team.setdefault(p.team, {
            "team": p.team,
            "formation": p.formation,
            "starting_xi": [],
            "bench": [],
        })
        player_data = {
            "name": p.player,
            "position": p.position,
            "number": p.number,
        }
        if p.is_starter:
            team["starting_xi"].append(player_data)
        else:
            team["bench"].append(player_data)
    return {
        "fixture_id": fixture_id,
        "home_team": fx.home_team if fx else None,
        "away_team": fx.away_team if fx else None,
        "lineups": list(by_team.values()),
        "available": len(lineups) > 0,
    }


@router.get("/live/stream/{fixture_id}")
async def live_stream(fixture_id: int, since: int = 0, request: Request = None):
    """Server-Sent Events stream for live match events.

    The browser connects once and receives new goals/cards/subs as they are
    stored. Uses long-polling fallback (checks every 5s) so it works without
    WebSockets. No external push service needed — zero extra cost.

    Usage:
        const evtSource = new EventSource('/api/live/stream/12345');
        evtSource.onmessage = (e) => console.log(JSON.parse(e.data));
    """

    async def event_generator():
        last_id = since
        # Send initial connection acknowledgement
        yield f"data: {json.dumps({'type': 'connected', 'fixture_id': fixture_id})}\n\n"

        for _ in range(360):  # max 30 minutes (360 × 5s)
            if request and await request.is_disconnected():
                break
            events = pop_events_since(fixture_id, last_id)
            for ev in events:
                last_id = max(last_id, ev.get("id", 0))
                ev["label"] = _event_label(ev.get("event_type", ""))
                yield f"data: {json.dumps(ev)}\n\n"
            # Heartbeat to keep connection alive
            yield f": heartbeat\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/live/subscribe")
async def subscribe_push(request: Request, db: Session = Depends(get_db)):
    """Save a Web Push subscription for goal/card notifications.

    Frontend sends the PushSubscription object from the browser Push API.
    We store it and use it to send push notifications when events occur.
    """
    payload = await request.json()
    endpoint = str(payload.get("endpoint", "")).strip()
    if not endpoint:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="endpoint required")

    keys = payload.get("keys", {}) or {}
    fixture_ids = payload.get("fixture_ids", [])
    username = str(payload.get("username", "")).strip()[:80] or None

    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == endpoint
    ).first()

    if existing:
        existing.keys_p256dh = keys.get("p256dh")
        existing.keys_auth = keys.get("auth")
        existing.fixture_ids = fixture_ids
        if username:
            existing.username = username
    else:
        db.add(PushSubscription(
            endpoint=endpoint,
            keys_p256dh=keys.get("p256dh"),
            keys_auth=keys.get("auth"),
            username=username,
            fixture_ids=fixture_ids,
        ))
    db.commit()
    return {"status": "subscribed", "fixture_ids": fixture_ids}
