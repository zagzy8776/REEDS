"""Match Intelligence: prediction timeline, revisions, and market overview.

Built strictly from stored rows (prediction versions, odds snapshots, live
match events, stored outcome metadata). The frontend renders these as a
continuously-updating intelligence view; nothing here is invented.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Fixture, MatchEvent, OddsSnapshot, Prediction

log = logging.getLogger(__name__)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def prediction_revisions(db: Session, fixture_id: int) -> list[dict[str, Any]]:
    """Every stored version of a prediction for this fixture.

    Superseded rows are kept — 'REEDS changed its mind' is never hidden.
    """
    rows = (
        db.query(Prediction)
        .filter(Prediction.fixture_id == fixture_id)
        .order_by(Prediction.market.asc(), Prediction.version.asc(), Prediction.id.asc())
        .all()
    )
    groups: dict[str, list[dict]] = {}
    for prediction in rows:
        groups.setdefault(prediction.market, []).append({
            "version": prediction.version,
            "pick": prediction.pick,
            "confidence": round(float(prediction.confidence), 1),
            "risk_level": prediction.risk_level,
            "status": prediction.status,
            "published_at": _iso(prediction.published_at),
            "superseded_at": _iso(prediction.superseded_at),
            "created_at": _iso(prediction.created_at),
        })
    return [
        {"market": market, "versions": versions, "latest": len(versions) > 1}
        for market, versions in sorted(groups.items())
    ]


def market_overview(db: Session, fixture: Fixture) -> dict[str, Any]:
    """Opening / current / closing odds with pick-side market probability + edge."""
    snapshots = (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.fixture_id == fixture.id)
        .order_by(OddsSnapshot.captured_at.asc())
        .all()
    )

    def _phase_rows(phase: str):
        matches = [s for s in snapshots if (s.phase or "") == phase]
        return matches

    opening = _phase_rows("opening")
    closing = _phase_rows("closing")
    current = snapshots[-1] if snapshots else None

    def _serialize(snapshot: OddsSnapshot | None) -> dict | None:
        if snapshot is None:
            return None
        return {
            "captured_at": _iso(snapshot.captured_at),
            "home_odds": snapshot.home_odds,
            "draw_odds": snapshot.draw_odds,
            "away_odds": snapshot.away_odds,
            "over_odds": snapshot.over_odds,
            "under_odds": snapshot.under_odds,
            "bookmaker": snapshot.bookmaker,
            "source": snapshot.source,
        }

    # Pick-side market probability + model edge from the newest active read.
    active = (
        db.query(Prediction)
        .filter(Prediction.fixture_id == fixture.id, Prediction.status == "active")
        .order_by(Prediction.version.desc(), Prediction.id.desc())
        .first()
    )
    market_metrics: dict = {"available": False}
    if active is not None:
        from app.api.public import _market_metrics
        market_metrics = _market_metrics(active, fixture)

    return {
        "fixture_id": fixture.id,
        "opening": _serialize(opening[0]) if opening else None,
        "current": _serialize(current),
        "closing": _serialize(closing[-1]) if closing else None,
        "snapshot_count": len(snapshots),
        "pick_side": market_metrics,
        "fixture_line": {
            "home_odds": fixture.home_odds,
            "draw_odds": fixture.draw_odds,
            "away_odds": fixture.away_odds,
        },
        "note": "Line movement is information from the market, not proof that a pick will win.",
    }


def prediction_timeline(db: Session, fixture: Fixture) -> list[dict[str, Any]]:
    """Chronological intelligence events for the match."""
    entries: list[dict[str, Any]] = []

    predictions = (
        db.query(Prediction)
        .filter(Prediction.fixture_id == fixture.id)
        .order_by(Prediction.version.asc(), Prediction.id.asc())
        .all()
    )
    for index, prediction in enumerate(predictions):
        first_version = prediction.version == 1 or index == 0
        entries.append({
            "type": "model_analysis",
            "title": "Initial analysis" if first_version else f"REEDS UPDATE — model re-read",
            "detail": f"{prediction.market}: {prediction.pick} at {prediction.confidence:.0f}% confidence",
            "ts": _iso(prediction.created_at),
            "data": {"confidence": prediction.confidence, "version": prediction.version},
        })

    # Publication events.
    for prediction in predictions:
        if prediction.published_at:
            entries.append({
                "type": "publication",
                "title": "Prediction published",
                "detail": f"{prediction.market}: {prediction.pick} ({prediction.confidence:.0f}%)",
                "ts": _iso(prediction.published_at),
                "data": {"prediction_id": prediction.id},
            })

    # Odds movement snapshots (current vs previous).
    snapshots = (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.fixture_id == fixture.id)
        .order_by(OddsSnapshot.captured_at.asc())
        .all()
    )
    previous: dict[str, float | None] = {}
    for snap in snapshots:
        move: list[str] = []
        for key, label in (("home_odds", "Home"), ("draw_odds", "Draw"), ("away_odds", "Away"), ("over_odds", "Over"), ("under_odds", "Under")):
            value = getattr(snap, key)
            if value is None:
                continue
            if previous.get(key) is not None and previous[key] != value:
                move.append(f"{label}: {previous[key]:.2f} → {value:.2f}")
            previous[key] = value
        detail = move if move else "Odds refreshed (no price change)" if snap.home_odds else "Odds snapshot recorded"
        entries.append({
            "type": "odds_snapshot",
            "title": "Odds movement",
            "detail": "; ".join(detail) if isinstance(detail, list) else detail,
            "ts": _iso(snap.captured_at),
            "data": {"phase": snap.phase, "home_odds": snap.home_odds, "draw_odds": snap.draw_odds, "away_odds": snap.away_odds},
        })

    # Live events.
    event_rows = (
        db.query(MatchEvent)
        .filter(MatchEvent.fixture_id == fixture.id)
        .order_by(MatchEvent.created_at.asc())
        .all()
    )
    for event in event_rows:
        label = {
            "goal": "Goal",
            "yellow_card": "Yellow Card",
            "red_card": "Red Card",
            "substitution": "Substitution",
            "var": "VAR Review",
            "penalty_missed": "Penalty Missed",
            "lineup": "Lineup",
        }.get(event.event_type, event.event_type.replace("_", " ").title())
        detail = f"{event.minute}' {event.team or ''} {event.player or ''}".strip()
        if event.home_score_at is not None:
            detail += f" ({event.home_score_at}-{event.away_score_at})"
        entries.append({
            "type": "live_event",
            "title": label,
            "detail": detail,
            "ts": _iso(event.created_at),
            "data": {"event_type": event.event_type, "minute": event.minute},
        })

    # Match start.
    entries.append({"type": "kickoff", "title": "Kickoff", "detail": f"{fixture.home_team} vs {fixture.away_team}", "ts": _iso(fixture.match_date), "data": {}})

    # Settlement from stored outcome metadata.
    for prediction in predictions:
        meta = prediction.engine_meta if isinstance(prediction.engine_meta, dict) else {}
        outcome = meta.get("outcome") if isinstance(meta.get("outcome"), dict) else {}
        result = outcome.get("result")
        if result not in {"won", "lost"}:
            continue
        entries.append({
            "type": "final",
            "title": f"Final — {result.upper()}",
            "detail": f"{prediction.pick} settled as a {result.lower()} · final {outcome.get('final_score') or f'{fixture.home_score}-{fixture.away_score}'}",
            "ts": outcome.get("settled_at"),
            "data": {"result": result, "final_score": outcome.get("final_score")},
        })

    entries.sort(key=lambda item: item.get("ts") or "")
    return entries


def match_intelligence(db: Session, fixture_id: int) -> dict[str, Any] | None:
    fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
    if fixture is None:
        return None
    return {
        "fixture_id": fixture.id,
        "revisions": prediction_revisions(db, fixture.id),
        "market": market_overview(db, fixture),
        "timeline": prediction_timeline(db, fixture),
        "data_note": "Timeline reflects stored events only. Empty periods mean no event was recorded for that moment.",
    }