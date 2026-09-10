"""Low-cost live score/state synchronization."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import Fixture, MatchEvent
from app.scraper.http_client import HttpClient
from app.services.live_events import push_live_event
from app.services.live_intelligence import build_live_intelligence

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
    """Convert provider statistics into {metric: {home, away}}."""
    if not isinstance(raw, list):
        return {}
    stats: dict[str, dict[str, int | float | str | None]] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        metric = str(row.get("type") or row.get("name") or "").strip()
        if metric:
            stats[metric] = {"home": row.get("home"), "away": row.get("away")}
    return stats


def _live_fixture_candidates(db: Session) -> list[Fixture]:
    """Load a small date window so provider/local timezone differences cannot hide live games."""
    today = date.today()
    return (
        db.query(Fixture)
        .filter(Fixture.match_date >= today - timedelta(days=1), Fixture.match_date <= today + timedelta(days=1))
        .all()
    )


def _match_fixture(db: Session, item: dict) -> Fixture | None:
    """Match provider events to a tracked fixture using stable key, then teams."""
    event_key = str(item.get("event_key") or "").strip()
    candidates = _live_fixture_candidates(db)
    if event_key:
        for fx in candidates:
            extra = fx.extra if isinstance(fx.extra, dict) else {}
            if str(extra.get("allsports_event_key") or extra.get("event_key") or "") == event_key:
                return fx

    home = str(item.get("event_home_team") or "").strip()
    away = str(item.get("event_away_team") or "").strip()
    if not home or not away:
        return None
    home_norm = " ".join(home.casefold().split())
    away_norm = " ".join(away.casefold().split())
    for fx in candidates:
        if home_norm == " ".join(str(fx.home_team or "").casefold().split()) and away_norm == " ".join(str(fx.away_team or "").casefold().split()):
            return fx
    return None


def _record_score_event(db: Session, fx: Fixture, minute: int | None) -> None:
    """Persist a score transition and publish it to the live stream."""
    existing = db.query(MatchEvent).filter(
        MatchEvent.fixture_id == fx.id,
        MatchEvent.event_type == "score_update",
        MatchEvent.minute == minute,
        MatchEvent.team == "REEDS",
        MatchEvent.player == "Score",
        MatchEvent.detail == f"{fx.home_score} - {fx.away_score}",
    ).first()
    if not existing:
        existing = MatchEvent(
            fixture_id=fx.id,
            event_type="score_update",
            minute=minute,
            team="REEDS",
            player="Score",
            detail=f"{fx.home_score} - {fx.away_score}",
            home_score_at=fx.home_score,
            away_score_at=fx.away_score,
            extra={"source": "live_score_feed"},
            created_at=datetime.utcnow(),
        )
        db.add(existing)
        db.flush()

    push_live_event(fx.id, {
        "id": existing.id,
        "fixture_id": fx.id,
        "event_type": "score_update",
        "minute": minute,
        "detail": "Live score updated",
        "home_score": fx.home_score,
        "away_score": fx.away_score,
        "home_team": fx.home_team,
        "away_team": fx.away_team,
        "league": fx.league,
        "timestamp": datetime.utcnow().isoformat(),
    })


def _intelligence_signature(value: dict | None) -> dict:
    """Ignore generation time so the same live read is not rebroadcast every poll."""
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if k != "generated_at"}


def sync_allsports_live(db: Session, api_key: str | None, sport: str = "football") -> dict:
    """Synchronize the shared provider live feed in one request.

    The scheduler calls this frequently only when AllSports is configured.
    Every browser receives the resulting state through REEDS SSE; browsers
    never call the provider directly.
    """
    if not api_key:
        return {"provider": "allsportsapi", "checked": 0, "updated": 0, "score_changes": 0, "stats_updates": 0, "skipped": "not configured"}

    client = HttpClient()
    try:
        payload = client.get(
            f"https://apiv2.allsportsapi.com/{sport}",
            params={"met": "Livescore", "APIkey": api_key, "withPlayerStats": "1"},
        ).json()
    except Exception as exc:
        log.warning("AllSports live feed failed: %s", exc)
        return {"provider": "allsportsapi", "checked": 0, "updated": 0, "score_changes": 0, "stats_updates": 0, "errors": [str(exc)[:300]]}

    events = payload.get("result", []) if isinstance(payload, dict) else []
    if not isinstance(events, list):
        events = []

    checked = updated = score_changes = stats_updates = intelligence_updates = 0
    for item in events:
        if not isinstance(item, dict):
            continue
        fx = _match_fixture(db, item)
        if not fx:
            continue
        checked += 1
        old_home, old_away = fx.home_score, fx.away_score
        old_status = str((fx.extra or {}).get("status") or "")
        old_intelligence = (fx.extra or {}).get("live_intelligence") if isinstance(fx.extra, dict) else None

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

        if home_score is not None:
            fx.home_score = home_score
        if away_score is not None:
            fx.away_score = away_score

        intelligence = build_live_intelligence(fx.home_score, fx.away_score, stats, elapsed)
        intelligence_changed = _intelligence_signature(old_intelligence) != _intelligence_signature(intelligence)

        extra = dict(fx.extra or {})
        extra.update({
            "allsports_event_key": item.get("event_key"),
            "status": status or old_status,
            "live": is_live or status.upper() in _LIVE_STATUSES,
            "elapsed": elapsed if elapsed is not None else extra.get("elapsed"),
            "live_last_synced_at": datetime.utcnow().isoformat(),
            "live_provider": "allsportsapi",
            "live_intelligence": intelligence,
        })
        if stats:
            extra["live_stats"] = stats
            extra["live_stats_updated_at"] = datetime.utcnow().isoformat()
            stats_updates += 1
        fx.extra = extra

        score_changed = old_home != fx.home_score or old_away != fx.away_score
        if score_changed:
            score_changes += 1
            _record_score_event(db, fx, elapsed)

        if stats:
            push_live_event(fx.id, {
                "fixture_id": fx.id,
                "event_type": "stats_update",
                "minute": elapsed,
                "stats": stats,
                "home_score": fx.home_score,
                "away_score": fx.away_score,
                "timestamp": datetime.utcnow().isoformat(),
            })

        if intelligence_changed and (score_changed or stats or not old_intelligence):
            intelligence_updates += 1
            pressure = str(intelligence.get("pressure") or "").replace("_", " ")
            evidence_count = int(intelligence.get("evidence_count") or 0)
            drivers = intelligence.get("drivers") or []
            push_live_event(fx.id, {
                "fixture_id": fx.id,
                "event_type": "live_intelligence",
                "minute": elapsed,
                "home_score": fx.home_score,
                "away_score": fx.away_score,
                "player": f"{pressure} • {evidence_count} evidence point{'s' if evidence_count != 1 else ''}",
                "detail": " • ".join(drivers) if drivers else "No provider statistics are available yet",
                "intelligence": intelligence,
                "timestamp": datetime.utcnow().isoformat(),
            })

        if score_changed or old_status != status or stats or intelligence_changed:
            updated += 1

    db.commit()
    return {
        "provider": "allsportsapi",
        "checked": checked,
        "updated": updated,
        "score_changes": score_changes,
        "stats_updates": stats_updates,
        "intelligence_updates": intelligence_updates,
    }
