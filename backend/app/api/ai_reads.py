"""Public per-fixture model reads API."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.public import serialize_prediction
from app.db.models import Fixture, Prediction
from app.db.session import get_db

log = logging.getLogger(__name__)
router = APIRouter()
_LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}


def _fixture_payload(fixture: Fixture) -> dict:
    extra = fixture.extra if isinstance(fixture.extra, dict) else {}
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
        "has_odds": extra.get("odds_source") != "model_implied" and any(
            v is not None for v in (fixture.home_odds, fixture.draw_odds, fixture.away_odds)
        ),
        "status": extra.get("status"),
        "elapsed": extra.get("elapsed"),
        "is_live": bool(extra.get("live")) or str(extra.get("status", "")).upper() in _LIVE_STATUSES,
        "source": fixture.source,
        "provider_sources": extra.get("provider_sources", []) if isinstance(extra.get("provider_sources"), list) else [],
    }


def _rows(db: Session, fixture_id: int):
    return (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(Prediction.fixture_id == fixture_id, Prediction.status == "active")
        .order_by(Prediction.confidence.desc(), Prediction.market.asc())
        .all()
    )


def _response(db: Session, fixture: Fixture, rows: list, status: str) -> dict:
    from app.api.public import records_map
    from app.services.feedback import post_match_analysis
    from app.services.match_intelligence import market_overview, prediction_revisions, prediction_timeline

    records = records_map(db, {(fixture.sport, p.market) for p, _ in rows})
    predictions = []
    for prediction, fx in rows:
        item = serialize_prediction(prediction, fx, records.get(f"{fx.sport}::{prediction.market}"))
        if item.get("result") != "pending":
            post = post_match_analysis(db, prediction.id)
            if post:
                item["post_match"] = post
        predictions.append(item)
    return {
        "status": status,
        "fixture": _fixture_payload(fixture),
        "predictions": predictions,
        "intelligence": {
            "revisions": prediction_revisions(db, fixture.id),
            "market": market_overview(db, fixture),
            "timeline": prediction_timeline(db, fixture),
        },
        "generation_queued": False,
        "message": "LOYAL EDGE model predictions for this exact fixture.",
        "responsible_note": "Predictions are probabilistic, not guaranteed outcomes.",
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

    rows = _rows(db, fixture.id)
    if not rows and fixture.match_date is not None:
        try:
            from app.services.fixture_prediction import generate_fixture_predictions
            generated = generate_fixture_predictions(db, fixture.id)
            log.info("Model generation: fixture=%s generated=%s", fixture.id, generated)
            db.commit()
            rows = _rows(db, fixture.id)
        except Exception:
            db.rollback()
            log.exception("Model generation failed for fixture %s", fixture.id)

    if rows:
        return _response(db, fixture, rows, "ready")

    return {
        "status": "model_unavailable",
        "fixture": _fixture_payload(fixture),
        "predictions": [],
        "intelligence": {"revisions": [], "market": {}, "timeline": []},
        "generation_queued": False,
        "message": "The trained model did not return a prediction for this fixture.",
        "responsible_note": "Predictions are probabilistic, not guaranteed outcomes.",
    }
