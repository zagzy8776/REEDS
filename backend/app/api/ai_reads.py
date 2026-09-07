"""Public per-fixture AI Reads API."""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Fixture, Prediction
from app.db.session import get_db
from app.api.public import serialize_prediction

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/ai-reads/{fixture_id}")
def ai_reads(fixture_id: int, db: Session = Depends(get_db)):
    fixture = (
        db.query(Fixture)
        .filter(Fixture.id == fixture_id, Fixture.source != "coverage_seed")
        .first()
    )
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    rows = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(
            Prediction.fixture_id == fixture.id,
            Prediction.is_published == True,
            Prediction.status == "active",
        )
        .order_by(Prediction.confidence.desc(), Prediction.market.asc())
        .all()
    )

    if not rows:
        # AI Reads must never make the user's request execute a long ML build.
        # Queue the normal Render-side generator and let the client poll.
        if fixture.match_date >= date.today():
            try:
                from app.services.prediction_runner import start_prediction_generation
                queued = start_prediction_generation(reason=f"ai-reads-missing-{fixture.id}")
            except Exception:
                queued = False
                log.exception("Could not queue prediction generation for fixture %s", fixture.id)
        else:
            queued = False
        return {
            "status": "preparing",
            "fixture": {
                "id": fixture.id,
                "sport": fixture.sport,
                "league": fixture.league,
                "match_date": fixture.match_date,
                "home_team": fixture.home_team,
                "away_team": fixture.away_team,
                "home_score": fixture.home_score,
                "away_score": fixture.away_score,
                "home_odds": fixture.home_odds,
                "draw_odds": fixture.draw_odds,
                "away_odds": fixture.away_odds,
                "has_odds": any(v is not None for v in (fixture.home_odds, fixture.draw_odds, fixture.away_odds)),
            },
            "predictions": [],
            "generation_queued": bool(queued),
            "message": "AI analysis is being prepared for this match.",
        }

    return {
        "status": "ready",
        "fixture": {
            "id": fixture.id,
            "sport": fixture.sport,
            "league": fixture.league,
            "season": fixture.season,
            "match_date": fixture.match_date,
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "home_score": fixture.home_score,
            "away_score": fixture.away_score,
            "home_odds": fixture.home_odds,
            "draw_odds": fixture.draw_odds,
            "away_odds": fixture.away_odds,
            "has_odds": any(v is not None for v in (fixture.home_odds, fixture.draw_odds, fixture.away_odds)),
            "source": fixture.source,
            "provider_sources": (fixture.extra or {}).get("provider_sources", []) if isinstance(fixture.extra, dict) else [],
        },
        "predictions": [serialize_prediction(prediction, fx) for prediction, fx in rows],
        "responsible_note": "AI Reads are probabilistic analysis, not guaranteed outcomes.",
    }
