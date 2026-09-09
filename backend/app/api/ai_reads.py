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


def _fixture_payload(fixture: Fixture) -> dict:
    return {
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
    }


@router.get("/ai-reads/{fixture_id}")
def ai_reads(fixture_id: int, db: Session = Depends(get_db)):
    fixture = (
        db.query(Fixture)
        .filter(Fixture.id == fixture_id, Fixture.source != "coverage_seed")
        .first()
    )
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    def _rows(published_only: bool | None = None) -> list[tuple[Prediction, Fixture]]:
        query = (
            db.query(Prediction, Fixture)
            .join(Fixture, Prediction.fixture_id == Fixture.id)
            .filter(
                Prediction.fixture_id == fixture.id,
                Prediction.status == "active",
            )
            .order_by(Prediction.confidence.desc(), Prediction.market.asc())
        )
        if published_only is True:
            query = query.filter(Prediction.is_published == True)
        elif published_only is False:
            query = query.filter(Prediction.is_published == False)
        return query.all()

    def _response(rows: list[tuple[Prediction, Fixture]], status: str) -> dict:
        return {
            "status": status,
            "fixture": _fixture_payload(fixture),
            "predictions": [serialize_prediction(prediction, fx) for prediction, fx in rows],
            "generation_queued": False,
            "message": (
                "AI Reads are probabilistic analysis, not guaranteed outcomes."
            ),
            "responsible_note": "AI Reads are probabilistic analysis, not guaranteed outcomes.",
        }

    published = _rows(published_only=True)
    if published:
        return _response(published, "ready")

    draft = _rows(published_only=False)
    if draft:
        response = _response(draft, "draft")
        response["message"] = (
            "Draft analysis — generated for this exact match but not yet part "
            "of the public tracked record. These reads are provisional until "
            "empirical evidence unlocks publication."
        )
        return response

    if fixture.match_date >= date.today():
        # Generate this one fixture directly rather than depending on the
        # global 50-fixture prediction cap. A single Match Hub/AI Reads click
        # should always have an exact on-demand path.
        try:
            from app.services.fixture_prediction import generate_fixture_predictions
            generated = generate_fixture_predictions(db, fixture.id)
            log.info("Exact AI Reads generation: fixture=%s generated=%s", fixture.id, generated)
        except Exception:
            db.rollback()
            log.exception("Exact AI Reads generation failed for fixture %s", fixture.id)

        published = _rows(published_only=True)
        if published:
            return _response(published, "ready")
        draft = _rows(published_only=False)
        if draft:
            response = _response(draft, "draft")
            response["message"] = (
                "Draft analysis — generated for this exact match but not yet part "
                "of the public tracked record. These reads are provisional until "
                "empirical evidence unlocks publication."
            )
            return response

    return {
        "status": "preparing" if fixture.match_date >= date.today() else "unavailable",
        "fixture": _fixture_payload(fixture),
        "predictions": [],
        "generation_queued": False,
        "message": "AI analysis is not published for this match yet.",
    }
