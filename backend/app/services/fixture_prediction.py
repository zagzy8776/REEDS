"""Generate the published AI read for one exact fixture."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import Fixture, Prediction
from app.ml.generic import GenericSportEngine
from app.ml.ensemble import LoyalEdgeEngine
from app.services.model_registry import active_model_path
from app.services.predictions import (
    _backfill_fixture_odds,
    _capture_odds_snapshot,
    _existing_prediction_changed,
    _next_prediction_version,
    _prediction_signature,
    explain_prediction_item,
    select_public_picks,
    dataframe_from_db,
)
from app.services.prediction_quality import annotate_quality

log = logging.getLogger(__name__)


def generate_fixture_predictions(db: Session, fixture_id: int) -> int:
    """Generate predictions for one fixture without rebuilding the whole board."""
    fx = (
        db.query(Fixture)
        .filter(Fixture.id == fixture_id, Fixture.source != "coverage_seed")
        .first()
    )
    if not fx:
        return 0

    # Current-day fixtures can sit between seasons. Keep a bounded two-year
    # history window so the engine does not silently fall back to league-average
    # defaults just because the last 180 days contain no completed matches.
    history = dataframe_from_db(db, max_age_days=730)
    if fx.sport == "soccer":
        engine = LoyalEdgeEngine(active_model_path(db, "soccer"))
        items = engine.predict_soccer(history, {
            "id": fx.id,
            "_db": db,
            "sport": fx.sport,
            "home_team": fx.home_team,
            "away_team": fx.away_team,
            "match_date": fx.match_date,
            "league": fx.league,
            "home_odds": fx.home_odds,
            "draw_odds": fx.draw_odds,
            "away_odds": fx.away_odds,
        })
    else:
        items = GenericSportEngine().predict(history, {
            "sport": fx.sport,
            "home_team": fx.home_team,
            "away_team": fx.away_team,
            "match_date": fx.match_date,
        })

    _backfill_fixture_odds(db, fx, items)
    published = select_public_picks(items, fixture=fx, db=db)
    generated = 0

    for idx, raw in enumerate(items):
        item = annotate_quality(explain_prediction_item(raw, fx))
        is_published = idx in published
        market = str(item.get("market") or "")
        if not market:
            continue
        signature = _prediction_signature(item)
        meta = dict(item.get("engine_meta") or {})
        meta["prediction_signature"] = signature
        existing = (
            db.query(Prediction)
            .filter(
                Prediction.fixture_id == fx.id,
                Prediction.market == market,
                Prediction.status == "active",
            )
            .order_by(Prediction.version.desc())
            .first()
        )
        if existing and not _existing_prediction_changed(existing, item):
            if existing.is_published != is_published:
                existing.is_published = is_published
                existing.published_at = datetime.utcnow() if is_published else None
            continue
        if existing:
            existing.status = "superseded"
            existing.superseded_at = datetime.utcnow()
        prediction = Prediction(
            fixture_id=fx.id,
            model_version_id=None,
            version=_next_prediction_version(db, fx.id, market),
            status="active",
            market=market,
            pick=str(item.get("pick") or ""),
            confidence=float(item.get("confidence", 0)),
            edge_score=float(item.get("edge_score", 0)),
            risk_level=str(item.get("risk_level") or "Medium"),
            reasoning=str(item.get("reasoning") or ""),
            engine_meta=meta,
            is_premium=is_published and float(item.get("confidence", 0)) >= 70,
            is_published=is_published,
            published_at=datetime.utcnow() if is_published else None,
        )
        db.add(prediction)
        db.flush()
        _capture_odds_snapshot(db, fx, prediction, "published" if is_published else "initial")
        generated += 1

    db.commit()
    return generated
