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
        "has_odds": any(v is not None for v in (fixture.home_odds, fixture.draw_odds, fixture.away_odds)),
        "status": extra.get("status"),
        "elapsed": extra.get("elapsed"),
        "is_live": bool(extra.get("live")) or str(extra.get("status", "")).upper() in _LIVE_STATUSES,
        "source": fixture.source,
        "provider_sources": extra.get("provider_sources", []) if isinstance(extra.get("provider_sources"), list) else [],
    }


def _supported_draft(prediction: Prediction) -> bool:
    """Only expose an early read when its stored quality gate accepted it.

    A generated but default-driven row is useful for internal diagnostics, not
    for a customer-facing intelligence page. The public tracked record remains
    governed by ``is_published`` separately.
    """
    meta = prediction.engine_meta if isinstance(prediction.engine_meta, dict) else {}
    quality = meta.get("publication_quality") if isinstance(meta.get("publication_quality"), dict) else {}
    return bool(quality.get("accepted"))


def _response(db: Session, fixture: Fixture, rows: list[tuple[Prediction, Fixture]], status: str) -> dict:
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

    def _rows(published_only: bool | None = None) -> list[tuple[Prediction, Fixture]]:
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

    if fixture.match_date >= date.today():
        try:
            from app.services.fixture_prediction import generate_fixture_predictions
            generated = generate_fixture_predictions(db, fixture.id)
            log.info("Exact AI Reads refresh: fixture=%s generated=%s", fixture.id, generated)
        except Exception:
            db.rollback()
            log.exception("Exact AI Reads refresh failed for fixture %s", fixture.id)

    published = _rows(published_only=True)
    if published:
        return _response(db, fixture, published, "ready")

    draft = _rows(published_only=False)
    if draft:
        response = _response(db, fixture, draft, "draft")
        response["message"] = "Early read — generated for this exact match, but it is not yet part of the public tracked record."
        return response

    return {
        "status": "preparing" if fixture.match_date >= date.today() else "unavailable",
        "fixture": _fixture_payload(fixture),
        "predictions": [],
        "generation_queued": False,
        "message": "REEDS is withholding this read because the available evidence is not match-specific enough yet.",
        "responsible_note": "No public read is shown until REEDS has sufficient match-specific evidence.",
    }
