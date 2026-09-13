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
_MISSED_LIVE_POLLS_BEFORE_FINISH = 3


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


def _provider_match_date(item: dict) -> date:
    """Use the provider date when available; fall back to today for live discovery."""
    raw = str(item.get("event_date") or item.get("event_date_start") or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                pass
    return date.today()


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


def _discover_live_fixture(db: Session, item: dict) -> Fixture | None:
    """Create a fixture directly from a provider live event when ingestion missed it."""
    existing = _match_fixture(db, item)
    if existing:
        return existing

    home = str(item.get("event_home_team") or "").strip()
    away = str(item.get("event_away_team") or "").strip()
    if not home or not away:
        return None

    match_date = _provider_match_date(item)
    league = str(
        item.get("event_league_name")
        or item.get("event_league")
        or item.get("league_name")
        or "Live"
    ).strip()[:80] or "Live"
    sport = "basketball" if str(item.get("event_sport_type") or "").lower() in {"basketball", "basket ball"} else "football"
    event_key = str(item.get("event_key") or "").strip() or None

    # Team/date lookup is deliberately repeated here to handle a fixture that
    # falls outside the normal +/-1 day window but has the provider's event key.
    existing = (
        db.query(Fixture)
        .filter(
            Fixture.sport == sport,
            Fixture.match_date == match_date,
            Fixture.home_team == home,
            Fixture.away_team == away,
        )
        .first()
    )
    if existing:
        return existing

    extra = {
        "allsports_event_key": event_key,
        "status": str(item.get("event_status") or "LIVE").strip() or "LIVE",
        "live": True,
        "live_provider": "allsportsapi",
        "live_discovered": True,
        "live_miss_count": 0,
        "live_last_synced_at": datetime.utcnow().isoformat(),
    }
    fx = Fixture(
        sport=sport,
        league=league,
        season=str(match_date.year),
        match_date=match_date,
        home_team=home,
        away_team=away,
        home_score=_int_or_none(item.get("event_current_home_score")),
        away_score=_int_or_none(item.get("event_current_away_score")),
        source="allsportsapi_live",
        extra=extra,
    )
    db.add(fx)
    db.flush()
    log.info("Discovered live fixture directly from AllSports: %s vs %s (%s)", home, away, league)
    return fx


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


def _mark_missing_live_fixtures(db: Session, seen_keys: set[str], seen_teams: set[tuple[str, str]]) -> list[Fixture]:
    """Expire fixtures that disappeared from the live feed and settle their picks.

    AllSports' Livescore endpoint is a current-live feed, so a finished game can
    disappear instead of sending an explicit FT row. Three consecutive 30s
    misses gives the provider a short grace period while preventing a match from
    remaining permanently stuck in the LIVE board.
    """
    today = date.today()
    rows = (
        db.query(Fixture)
        .filter(
            Fixture.match_date >= today - timedelta(days=1),
            Fixture.match_date <= today + timedelta(days=1),
        )
        .all()
    )
    finished: list[Fixture] = []
    for fx in rows:
        extra = dict(fx.extra or {})
        if str(extra.get("live_provider") or "") != "allsportsapi" or not extra.get("live"):
            continue
        key = str(extra.get("allsports_event_key") or "").strip()
        teams = (
            " ".join(str(fx.home_team or "").casefold().split()),
            " ".join(str(fx.away_team or "").casefold().split()),
        )
        if (key and key in seen_keys) or teams in seen_teams:
            extra["live_miss_count"] = 0
            fx.extra = extra
            continue
        misses = int(extra.get("live_miss_count") or 0) + 1
        extra["live_miss_count"] = misses
        if misses >= _MISSED_LIVE_POLLS_BEFORE_FINISH:
            extra["live"] = False
            extra["status"] = "FT"
            extra["finished_at"] = datetime.utcnow().isoformat()
            fx.extra = extra
            finished.append(fx)
            push_live_event(fx.id, {
                "fixture_id": fx.id,
                "event_type": "match_finished",
                "status": "FT",
                "home_score": fx.home_score,
                "away_score": fx.away_score,
                "timestamp": datetime.utcnow().isoformat(),
            })
        else:
            fx.extra = extra
    return finished


def sync_allsports_live(db: Session, api_key: str | None, sport: str = "football") -> dict:
    """Synchronize the shared provider live feed in one request.

    Live discovery is authoritative: if AllSports says a match is live, REEDS
    creates the fixture even when normal scheduled ingestion never saw it.
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

    checked = updated = score_changes = stats_updates = intelligence_updates = discovered = 0
    seen_keys: set[str] = set()
    seen_teams: set[tuple[str, str]] = set()
    for item in events:
        if not isinstance(item, dict):
            continue
        event_key = str(item.get("event_key") or "").strip()
        home_raw = str(item.get("event_home_team") or "").strip()
        away_raw = str(item.get("event_away_team") or "").strip()
        if event_key:
            seen_keys.add(event_key)
        if home_raw and away_raw:
            seen_teams.add((" ".join(home_raw.casefold().split()), " ".join(away_raw.casefold().split())))

        fx = _discover_live_fixture(db, item)
        if not fx:
            continue
        if fx.source == "allsportsapi_live" and fx.id:
            discovered += int(bool((fx.extra or {}).get("live_discovered")))
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
            "status": status or old_status or "LIVE",
            "live": is_live or status.upper() in _LIVE_STATUSES,
            "elapsed": elapsed if elapsed is not None else extra.get("elapsed"),
            "live_last_synced_at": datetime.utcnow().isoformat(),
            "live_provider": "allsportsapi",
            "live_miss_count": 0,
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

    finished = _mark_missing_live_fixtures(db, seen_keys, seen_teams)
    db.commit()

    # Settlement happens in the same 30-second live cycle. A finished match is
    # therefore eligible for history/performance immediately after its final
    # provider state is persisted; no separate daily job is required.
    settled = {"settled": 0, "won": 0, "lost": 0, "updated": 0}
    if finished:
        try:
            from app.services.prediction_learning import settle_prediction_outcomes
            settled = settle_prediction_outcomes(db, lookback_days=730)
            db.commit()
        except Exception:
            db.rollback()
            log.exception("Immediate live settlement failed")

    return {
        "provider": "allsportsapi",
        "checked": checked,
        "updated": updated,
        "score_changes": score_changes,
        "stats_updates": stats_updates,
        "intelligence_updates": intelligence_updates,
        "discovered": discovered,
        "finished": len(finished),
        "settled": settled.get("settled", 0),
        "settled_won": settled.get("won", 0),
        "settled_lost": settled.get("lost", 0),
    }
