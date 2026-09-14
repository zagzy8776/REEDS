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
        "has_odds": (
            (extra.get("odds_source") != "model_implied")
            and any(v is not None for v in (fixture.home_odds, fixture.draw_odds, fixture.away_odds))
        ),
        "status": extra.get("status"),
        "elapsed": extra.get("elapsed"),
        "is_live": bool(extra.get("live")) or str(extra.get("status", "")).upper() in _LIVE_STATUSES,
        "source": fixture.source,
        "provider_sources": extra.get("provider_sources", []) if isinstance(extra.get("provider_sources"), list) else [],
    }


def _supported_draft(prediction: Prediction) -> bool:
    """Allow any valid model output as a match-specific read.

    Odds, historical-depth, publication-market evidence, and confidence bands
    are enrichment/publication concerns. They must not erase a prediction that
    the trained model has already generated for this exact fixture.
    """
    meta = prediction.engine_meta if isinstance(prediction.engine_meta, dict) else {}
    quality = meta.get("publication_quality") if isinstance(meta.get("publication_quality"), dict) else {}
    reasons = quality.get("reasons") if isinstance(quality.get("reasons"), list) else []
    lowered = " ".join(str(r).lower() for r in reasons)
    if "invalid model probability payload" in lowered:
        return False
    return True


def _response(db: Session, fixture: Fixture, rows: list, status: str) -> dict:
    from app.services.feedback import post_match_analysis
    from app.services.match_intelligence import market_overview, prediction_revisions, prediction_timeline
    from app.api.public import records_map

    records = records_map(db, {(fixture.sport, p.market) for p, _ in rows})
    predictions = []
    for prediction, fx in rows:
        item = serialize_prediction(prediction, fx, records.get(f"{fx.sport}::{prediction.market}"))
        if item.get("result") != "pending":
            post = post_match_analysis(db, prediction.id)
            if post:
                item["post_match"] = post
        predictions.append(item)
    intelligence = {
        "revisions": prediction_revisions(db, fixture.id),
        "market": market_overview(db, fixture),
        "timeline": prediction_timeline(db, fixture),
    }
    return {
        "status": status,
        "fixture": _fixture_payload(fixture),
        "predictions": predictions,
        "intelligence": intelligence,
        "generation_queued": False,
        "message": "AI Reads are probabilistic analysis, not guaranteed outcomes.",
        "responsible_note": "AI Reads are probabilistic analysis, not guaranteed outcomes.",
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

    def _rows(published_only: bool | None = None):
        query = (
            db.query(Prediction, Fixture)
            .join(Fixture, Prediction.fixture_id == Fixture.id)
            .filter(Prediction.fixture_id == fixture.id, Prediction.status == "active")
            .order_by(Prediction.confidence.desc(), Prediction.market.asc())
        )
        if published_only is True:
            query = query.filter(Prediction.is_published == True)
        elif published_only is False:
            query = query.filter(Prediction.is_published == False)
        rows = query.all()
        if published_only is False:
            rows = [row for row in rows if _supported_draft(row[0])]
        return rows

    # Generate once when no active predictions exist. Odds/history are not
    # prerequisites; fixture_prediction now gates only real model prerequisites.
    existing_active = (
        db.query(Prediction.id)
        .filter(Prediction.fixture_id == fixture.id, Prediction.status == "active")
        .limit(1)
        .first()
    )
    if fixture.match_date >= date.today() and existing_active is None:
        try:
            from app.services.fixture_prediction import generate_fixture_predictions
            generated = generate_fixture_predictions(db, fixture.id)
            log.info("Exact AI Reads first-time generate: fixture=%s generated=%s", fixture.id, generated)
        except Exception:
            db.rollback()
            log.exception("Exact AI Reads generate failed for fixture %s", fixture.id)

    published = _rows(published_only=True)
    if published:
        return _response(db, fixture, published, "ready")

    draft = _rows(published_only=False)
    if draft:
        response = _response(db, fixture, draft, "ready")
        response["message"] = "Model read generated for this exact match. Odds and historical depth are optional supporting evidence."
        return response

    any_internal = (
        db.query(Prediction)
        .filter(Prediction.fixture_id == fixture.id, Prediction.status == "active")
        .count()
    )
    if any_internal:
        return {
            "status": "ready",
            "fixture": _fixture_payload(fixture),
            "predictions": [],
            "intelligence": {"revisions": [], "market": {}, "timeline": []},
            "generation_queued": False,
            "message": "REEDS generated a model read, but it is temporarily unavailable while the current prediction state is refreshed.",
            "responsible_note": "Predictions are probabilistic, not guaranteed outcomes.",
        }

    from app.services.fixture_quality import prediction_readiness
    readiness = prediction_readiness(db, fixture)
    checks = readiness.get("checks") or {}
    if readiness.get("ready"):
        status = "preparing" if fixture.match_date >= date.today() else "unavailable"
        message = "REEDS is ready to run the model for this fixture, but no prediction was stored."
    else:
        status = "insufficient_data"
        message = "REEDS cannot run the model for this fixture because a required model prerequisite is missing."

    return {
        "status": status,
        "fixture": _fixture_payload(fixture),
        "predictions": [],
        "intelligence": {"revisions": [], "market": {}, "timeline": []},
        "generation_queued": False,
        "readiness": readiness,
        "message": message,
        "responsible_note": "Predictions are probabilistic, not guaranteed outcomes.",
        "evidence_checklist": {
            "fixture_found": True,
            "league_identified": bool(checks.get("league_identified")),
            "odds_present": bool(checks.get("odds_present")),
            "history_present": bool(checks.get("history_present")),
            "teams_valid": bool(checks.get("teams_valid")),
            "model_available": bool(checks.get("model_available")),
            "gaps": readiness.get("reason") or [],
            "odds_optional": True,
            "history_optional": True,
        },
    }
