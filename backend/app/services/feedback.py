"""Structured post-match feedback for the REEDS learning loop.

A settlement creates one ``ModelFeedback`` row per prediction with only real
data: predicted probability, actual result, probability error, Brier component,
the feature snapshot the model actually used, and match-state context (e.g.
red cards). Signal attribution and error type are derived from that snapshot —
never invented. A single loss never rewrites model weights; ``aggregate_feedback``
flags recurring candidates that calibration may later act on after validation.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Fixture, MatchEvent, ModelFeedback, Prediction

log = logging.getLogger(__name__)


def _clamped_probability(confidence: float) -> float:
    try:
        return max(0.0, min(1.0, float(confidence) / 100.0))
    except (TypeError, ValueError):
        return 0.5


def _match_state_factors(db: Session, fixture: Fixture, pick: str) -> list[str]:
    if fixture.sport != "soccer":
        return []
    events = (
        db.query(MatchEvent)
        .filter(MatchEvent.fixture_id == fixture.id, MatchEvent.event_type.in_(["red_card", "goal", "penalty_missed"]))
        .all()
    )
    home_red = away_red = 0
    for event in events:
        if event.event_type == "red_card":
            if event.team == fixture.home_team:
                home_red += 1
            elif event.team == fixture.away_team:
                away_red += 1
    if not (home_red or away_red):
        return []
    side = "home" if "home" in pick.lower() else "away" if "away" in pick.lower() else None
    factors = []
    if side == "home" and home_red:
        factors.append("red card for the picked team changed the match state")
    elif side == "home" and away_red:
        factors.append("opponent red card removed a rival player (result may overstate model accuracy)")
    elif side == "away" and away_red:
        factors.append("red card for the picked team changed the match state")
    elif side == "away" and home_red:
        factors.append("opponent red card removed a rival player (result may overstate model accuracy)")
    if not factors:
        factors.append(f"match produced {home_red} red card(s) for home and {away_red} for away")
    return factors


def _error_type(prediction: Prediction, won: bool) -> str:
    if won:
        return "validated_outcome"
    if float(prediction.confidence or 0) >= 70.0:
        return "high_confidence_loss"
    if float(prediction.confidence or 0) < 55.0:
        return "low_confidence_miss"
    return "outcome_variance"


def _signals_from_factors(factors: list[dict], won: bool) -> list[str]:
    """Signal attribution from the model's own cited factors only."""
    labels = [str(f.get("label", "")).strip() for f in factors if f.get("label")]
    labels = [label for label in labels if label][:5]
    if won:
        return labels or ["model probability aligned with the actual outcome"]
    return labels or ["model probability did not match the actual outcome"]


def record_feedback(db: Session, prediction: Prediction, fixture: Fixture, won: bool) -> ModelFeedback | None:
    """Create or refresh the feedback row for one settled prediction."""
    try:
        from app.services.autopsy import build_autopsy

        meta = prediction.engine_meta if isinstance(prediction.engine_meta, dict) else {}
        prob = _clamped_probability(prediction.confidence)
        error = (1.0 - prob) if won else (0.0 - prob)
        brier = ((prob - 1.0) ** 2) if won else (prob ** 2)
        factors = meta.get("factors") if isinstance(meta.get("factors"), list) else []
        snapshot = {
            "factors": factors,
            "probabilities": meta.get("probabilities"),
            "projection": meta.get("projection"),
            "learning_feedback": meta.get("learning_feedback"),
            "publication_quality": meta.get("publication_quality"),
            "live_context": meta.get("live_context"),
        }
        failed: list[str] = []
        state_factors = _match_state_factors(db, fixture, prediction.pick)
        if not won:
            failed = _signals_from_factors(factors, False)
            if float(prediction.confidence or 0) >= 70:
                failed.append(f"high-confidence read ({prediction.confidence:.0f}%) did not land")
            learning = meta.get("learning_feedback") if isinstance(meta.get("learning_feedback"), dict) else {}
            if int(learning.get("segment_sample") or 0) < 8:
                failed.append("limited historical segment sample increased uncertainty")
            failed.extend(state_factors)
        successful = _signals_from_factors(factors, True) if won else []

        existing = db.query(ModelFeedback).filter(ModelFeedback.prediction_id == prediction.id).first()
        if existing is None:
            row = ModelFeedback(
                prediction_id=prediction.id,
                fixture_id=fixture.id,
                sport=fixture.sport,
                league=fixture.league,
                market=prediction.market,
                pick=prediction.pick,
                predicted_probability=round(prob, 4),
                actual_result="won" if won else "lost",
                probability_error=round(error, 4),
                brier_score=round(brier, 4),
                final_score=f"{fixture.home_score}-{fixture.away_score}" if fixture.home_score is not None and fixture.away_score is not None else None,
                outcome_text=f"{prediction.pick} — {'won' if won else 'lost'} {fixture.home_score}-{fixture.away_score}",
                error_type=_error_type(prediction, won),
                feature_snapshot=snapshot,
                successful_signals=successful,
                failed_signals=failed or None,
                contributing_factors=state_factors or None,
                model_version=prediction.version,
                model_version_id=prediction.model_version_id,
            )
            db.add(row)
            target = row
        else:
            existing.actual_result = "won" if won else "lost"
            existing.predicted_probability = round(prob, 4)
            existing.probability_error = round(error, 4)
            existing.brier_score = round(brier, 4)
            existing.feature_snapshot = snapshot
            existing.successful_signals = successful or None
            existing.failed_signals = failed or None
            existing.contributing_factors = state_factors or None
            existing.error_type = _error_type(prediction, won)
            existing.updated_at = datetime.utcnow()
            target = existing

        refused = published_status_reason(meta)
        autopsy = build_autopsy(db, prediction, fixture, won, disabled_reason=refused)
        target.error_classifications = autopsy["error_classifications"]
        target.signal_attribution = autopsy["signal_attribution"]
        target.defense_strong = bool(autopsy["defense_strong"])
        if autopsy["error_classifications"]:
            target.error_type = autopsy["error_classifications"][0]["type"]
        return target
    except Exception:
        log.exception("Could not record model feedback for prediction %s", prediction.id)
        return None


def published_status_reason(meta: dict) -> str | None:
    """Human reason a read is not part of the published/tracked record (if any)."""
    quality = meta.get("publication_quality") if isinstance(meta.get("publication_quality"), dict) else {}
    reasons = list(quality.get("reasons") or [])
    return "; ".join(reasons) if reasons else None


def post_match_analysis(db: Session, prediction_id: int) -> dict | None:
    """Structured, data-grounded post-match intelligence for one prediction."""
    row = (
        db.query(ModelFeedback)
        .filter(ModelFeedback.prediction_id == prediction_id)
        .first()
    )
    if row is None:
        return None
    prediction = db.query(Prediction).filter(Prediction.id == prediction_id).first()
    fixture = db.query(Fixture).filter(Fixture.id == (prediction.fixture_id if prediction else row.fixture_id)).first()
    if prediction is None or fixture is None:
        return None
    won = row.actual_result == "won"
    learning_line = (
        f"REEDS predicted {row.predicted_probability:.0%} and the pick {'won' if won else 'lost'}. "
        f"Probability error: {row.probability_error:+.0%} (Brier component {row.brier_score:.3f})."
    )
    if row.failed_signals:
        learning_line += " Recurring failure patterns are aggregated across many matches before calibration changes."
    return {
        "prediction_id": prediction.id,
        "fixture_id": fixture.id,
        "match": f"{fixture.home_team} vs {fixture.away_team}",
        "final_score": row.final_score,
        "result": row.actual_result,
        "expected": {
            "pick": prediction.pick,
            "market": prediction.market,
            "probability": round(row.predicted_probability * 100.0, 1),
            "risk": prediction.risk_level,
        },
        "actual": {
            "result": "won" if won else "lost",
            "final_score": row.final_score,
            "summary": "eventual_tracked_outcome",
        },
        "probability_error": row.probability_error,
        "brier_score": row.brier_score,
        "primary_error": None if won else row.error_type,
        "error_classifications": row.error_classifications or [],
        "signal_attribution": row.signal_attribution or {},
        "defense_strong": bool(row.defense_strong),
        "successful_signals": row.successful_signals or [],
        "failed_signals": row.failed_signals or [],
        "contributing_factors": row.contributing_factors or [],
        "features_used": (row.feature_snapshot or {}).get("factors") or [],
        "learning_signal": learning_line,
        "section_title": "Why REEDS was right" if won else "Why REEDS missed",
    }


def aggregate_feedback(db: Session, min_sample: int = 10) -> dict:
    """Find recurring pattern candidates (never auto-applied to the model)."""
    feedback = db.query(ModelFeedback).all()
    groups: dict[tuple, dict[str, Any]] = {}

    def _key_group(key: tuple) -> dict[str, Any]:
        return groups.setdefault(key, {"n": 0, "wins": 0, "brier_sum": 0.0, "conf_sum": 0.0, "error_sum": 0.0})

    def _bucket(conf: float) -> str:
        for low, high in ((0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)):
            if low <= conf < high:
                return f"{low}-{high if high <= 100 else '100'}"
        return "unknown"

    for row in feedback:
        won = row.actual_result == "won"
        side = "home" if "home" in (row.pick or "").lower() else "away" if "away" in (row.pick or "").lower() else "draw" if "draw" in (row.pick or "").lower() else "other"
        for key in (
            (row.sport, "overall"),
            (row.sport, row.market),
            (row.sport, _bucket(row.predicted_probability * 100.0)),
            (row.sport, side),
        ):
            group = _key_group(key)
            group["n"] += 1
            group["wins"] += int(won)
            group["brier_sum"] += row.brier_score or 0.0
            group["conf_sum"] += (row.predicted_probability or 0.5) * 100.0
            group["error_sum"] += abs(row.probability_error or 0.0)

    segments = []
    for (sport, dimension), g in groups.items():
        n = g["n"]
        accuracy = (g["wins"] / n * 100.0) if n else 0.0
        avg_conf = (g["conf_sum"] / n) if n else 0.0
        brier = (g["brier_sum"] / n) if n else 0.0
        avg_error = (g["error_sum"] / n) if n else 0.0
        issue: str | None = None
        if n >= min_sample:
            if avg_conf - accuracy > 15:
                issue = "systematically overconfident" if dimension != "overall" else "current model calibration is not yet tight"
            elif accuracy - avg_conf > 15:
                issue = "systematically underconfident"
            elif dimension == "overall" and brier > 0.25 and n >= min_sample:
                issue = "probability calibration candidate"
        segments.append({
            "sport": sport,
            "dimension": dimension,
            "sample": n,
            "wins": g["wins"],
            "accuracy": round(accuracy, 1),
            "avg_confidence": round(avg_conf, 1),
            "brier": round(brier, 3),
            "mean_abs_error": round(avg_error, 3),
            "candidate_issue": issue,
            "min_sample_required": min_sample,
        })

    segments.sort(key=lambda item: (item["sport"], item["dimension"]))
    flagged = [s for s in segments if s["candidate_issue"]]
    return {
        "segments": segments,
        "flagged_patterns": flagged,
        "note": "These are candidate calibration patterns for internal review. They are not automatically applied to the model; repeated validated evidence is required first.",
        "total_feedback_records": len(feedback),
    }