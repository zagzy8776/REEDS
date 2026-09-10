"""REEDS ALERT architecture — a recent-events feed derived from real data.

Alerts are computed from actual stored events (settlements, red cards, new
publishes, prediction updates). Nothing is fabricated. The push-subscription
table already exists for future Web-Push dispatch; this feed is the readable
architectural surface now.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Fixture, MatchEvent, Prediction

log = logging.getLogger(__name__)


def recent_alerts(db: Session, limit: int = 30) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    # 1. Recently settled predictions (from the stored outcome metadata).
    cutoff = date.today() - timedelta(days=14)
    rows = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(Prediction.is_published == True, Fixture.match_date >= cutoff, Fixture.home_score.isnot(None), Fixture.away_score.isnot(None))
        .order_by(Fixture.match_date.desc(), Prediction.id.desc())
        .limit(60)
        .all()
    )
    for prediction, fixture in rows:
        meta = prediction.engine_meta if isinstance(prediction.engine_meta, dict) else {}
        outcome = meta.get("outcome") if isinstance(meta.get("outcome"), dict) else {}
        result = outcome.get("result")
        if result not in {"won", "lost"}:
            continue
        score = outcome.get("final_score") or f"{fixture.home_score}-{fixture.away_score}"
        event = {
            "type": "prediction_settled",
            "severity": "info" if result == "won" else "warning",
            "fixture_id": fixture.id,
            "prediction_id": prediction.id,
            "title": f"{'WIN' if result == 'won' else 'LOSS'} — {fixture.home_team} {score} {fixture.away_team}",
            "body": f"REEDS picked {prediction.pick} ({prediction.confidence:.0f}%). Final: {score}.",
            "confidence": prediction.confidence,
            "result": result,
            "ts": fixture.match_date.isoformat() if hasattr(fixture.match_date, "isoformat") else str(fixture.match_date),
        }
        events.append(event)

    # 2. Red cards (major live match-state changes).
    card_rows = (
        db.query(MatchEvent, Fixture)
        .join(Fixture, MatchEvent.fixture_id == Fixture.id)
        .filter(MatchEvent.event_type == "red_card")
        .order_by(MatchEvent.created_at.desc())
        .limit(10)
        .all()
    )
    for event, fixture in card_rows:
        events.append({
            "type": "red_card",
            "severity": "danger",
            "fixture_id": fixture.id,
            "title": f"RED CARD — {event.team or 'Unknown'}",
            "body": f"{event.player or 'A player'} was sent off ({event.minute or '?'}'). REEDS probabilities are updated during play where live context exists.",
            "ts": event.created_at.isoformat() if event.created_at else None,
            "minute": event.minute,
        })

    # 3. Newly published predictions.
    new_pub = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(Prediction.is_published == True, Prediction.status == "active", Prediction.published_at.isnot(None), Fixture.match_date >= date.today())
        .order_by(Prediction.published_at.desc(), Prediction.id.desc())
        .limit(20)
        .all()
    )
    for prediction, fixture in new_pub:
        events.append({
            "type": "prediction_created",
            "severity": "info",
            "fixture_id": fixture.id,
            "prediction_id": prediction.id,
            "title": f"NEW READ — {fixture.home_team} vs {fixture.away_team}",
            "body": f"REEDS published: {prediction.pick} at {prediction.confidence:.0f}% confidence.",
            "confidence": prediction.confidence,
            "ts": prediction.published_at.isoformat() if prediction.published_at else None,
        })

    # 4. Prediction updates (REEDS changed its mind) from superseded versions.
    superseded = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(Prediction.status == "superseded", Prediction.superseded_at.isnot(None))
        .order_by(Prediction.superseded_at.desc())
        .limit(15)
        .all()
    )
    for prediction, fixture in superseded:
        active = (
            db.query(Prediction)
            .filter(
                Prediction.fixture_id == fixture.id,
                Prediction.market == prediction.market,
                Prediction.status == "active",
            )
            .order_by(Prediction.version.desc())
            .first()
        )
        if not active:
            continue
        events.append({
            "type": "model_update",
            "severity": "info",
            "fixture_id": fixture.id,
            "title": f"REEDS UPDATE — {fixture.home_team} vs {fixture.away_team}",
            "body": f"Confidence changed from {prediction.confidence:.0f}% to {active.confidence:.0f}% after new information.",
            "confidence": active.confidence,
            "ts": prediction.superseded_at.isoformat() if prediction.superseded_at else None,
        })

    events.sort(key=lambda item: item.get("ts") or "", reverse=True)
    return events[:limit]