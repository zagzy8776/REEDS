"""Product intelligence API: performance, calibration, alerts, match timeline,
data freshness, and personalization follows."""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stats/performance")
def performance(db: Session = Depends(get_db)):
    from app.services.analytics import performance_summary
    from app.services.redis_cache import cache_get_or_set

    def _build():
        return performance_summary(db)

    try:
        return cache_get_or_set("stats:performance:v1", 180, _build)
    except Exception as exc:
        log.exception("Performance stats failed")
        raise HTTPException(status_code=503, detail="Performance stats unavailable") from exc


@router.get("/stats/data-status")
def data_status(db: Session = Depends(get_db)):
    from app.services.analytics import data_status
    try:
        return data_status(db)
    except Exception as exc:
        log.exception("Data status failed")
        raise HTTPException(status_code=503, detail="Data status unavailable") from exc


@router.get("/stats/model-feedback")
def model_feedback(db: Session = Depends(get_db)):
    from app.services.feedback import aggregate_feedback
    from app.services.redis_cache import cache_get_or_set

    def _build():
        return aggregate_feedback(db)

    try:
        return cache_get_or_set("stats:model-feedback:v1", 180, _build)
    except Exception as exc:
        log.exception("Model feedback statistics failed")
        raise HTTPException(status_code=503, detail="Model feedback unavailable") from exc


@router.get("/stats/alerts/latest")
def alerts_latest(limit: int = 20, db: Session = Depends(get_db)):
    from app.services.alerts import recent_alerts

    try:
        return {"alerts": recent_alerts(db, max(1, min(int(limit), 50))), "note": "Alerts are derived from actual stored events only."}
    except Exception as exc:
        log.exception("Alerts feed failed")
        raise HTTPException(status_code=503, detail="Alerts unavailable") from exc


@router.get("/stats/learning")
def learning(min_sample: int = 12, gap: float = 12.0, db: Session = Depends(get_db)):
    """Prediction Autopsy pattern engine: conditional overconfidence candidates.

    Buckets thousands of stored autopsies (league, market, side, confidence,
    defensive context) and proposes calibration adjustments. Proposals are
    review candidates only — never automatically applied to the live model.
    """
    from app.services.autopsy import aggregate_patterns
    from app.services.redis_cache import cache_get_or_set

    def _build():
        return aggregate_patterns(db, min_sample=max(5, int(min_sample)), gap_threshold=float(gap))

    try:
        return cache_get_or_set(f"stats:learning:v1:{int(min_sample)}:{float(gap):g}", 180, _build)
    except Exception as exc:
        log.exception("Learning pattern engine failed")
        raise HTTPException(status_code=503, detail="Learning patterns unavailable") from exc


@router.get("/stats/post-match/{prediction_id}")
def post_match(prediction_id: int, db: Session = Depends(get_db)):
    from app.services.feedback import post_match_analysis

    analysis = post_match_analysis(db, prediction_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="No post-match analysis yet for this prediction")
    return analysis


@router.get("/fixtures/{fixture_id}/intelligence")
def fixture_intelligence(fixture_id: int, db: Session = Depends(get_db)):
    from app.services.match_intelligence import match_intelligence

    result = match_intelligence(db, fixture_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return result


@router.get("/follows")
def follows(username: str = "", db: Session = Depends(get_db)):
    from app.services.follows import list_follows

    if not username.strip():
        return {"follows": []}
    return {"follows": list_follows(db, username)}


@router.post("/follows")
async def toggle_follow(request: Request):
    from app.db.session import SessionLocal
    from app.services.follows import toggle_follow as _toggle

    payload = await request.json()
    db = SessionLocal()
    try:
        return _toggle(db, str(payload.get("username", "")), str(payload.get("entity_type", "")), str(payload.get("entity_value", "")))
    except Exception as exc:
        log.exception("Follow toggle failed")
        raise HTTPException(status_code=400, detail="Follow toggle failed") from exc
    finally:
        db.close()