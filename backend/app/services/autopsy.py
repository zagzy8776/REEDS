"""Prediction Autopsy — structured error classification and signal attribution.

Every settled prediction produces an autopsy derived strictly from data REEDS
already stored: the predicted probability, the real result, the model's exact
feature snapshot, stored odds, and match events. Nothing here is invented:

- ``error_classifications`` are deterministic rules over that stored data
  (overconfidence vs underconfidence, high-edge loss, market-support failure,
  defensive/offensive signal failure, lineup uncertainty, variance).
- ``signal_attribution`` is qualitative: which cited signals the model leaned
  on and whether the outcome followed them. We intentionally do NOT emit fake
  per-signal percentage contributions — that would be fabricated statistics.

``aggregate_patterns`` clusters thousands of autopsies into reviewable
condition/dimension buckets (league, market, side, confidence band, defensive
environment) and proposes calibration adjustments. Proposals are never applied
automatically: they are candidates that require validation first.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Fixture, ModelFeedback, Prediction

log = logging.getLogger(__name__)

# Factor label → signal category. Order matters: more specific first.
_CATEGORY_RULES: list[tuple[list[str], str]] = [
    (["defens", "conced", "clean sheet", "xga", "pressing", "opponent defense", "opponent defensive"], "defense"),
    (["injur", "lineup", "suspens", "rotation", "doubtful", "absent", "missing"], "lineup"),
    (["xg", "attack", "offense", "goal", "scor", "shot", "creat", "forward", "producent"], "offense"),
    (["form", "streak", "momentum", "recent", "form_differential"], "form"),
    (["odds", "value", "closing", "line", "bookmaker", "market"], "market"),
    (["home", "away", "venue", "crowd"], "venue"),
    (["h2h", "head to head", "quality", "rating"], "quality"),
]

_DEFENSE_STRONG_MARKERS = ["opponent defense", "opponent defensive", "defensive rating", "defens"]


def _categories_for(label: str) -> list[str]:
    text = str(label or "").lower()
    return [cat for markers, cat in _CATEGORY_RULES if any(m in text for m in markers)]


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def defense_strong_flag(factors: list[dict], disabled_reason: str | None = None) -> bool:
    """Was a strong-level defensive environment cited for this read?

    True only when a stored factor both (a) involves defense language and
    (b) carries a numeric severity/rating >= 70 or an explicitly strong value.
    Falls back to ``disabled_reason`` only with a real stored cause.
    """
    for factor in factors:
        label = str(factor.get("label", ""))
        if not any(m in label.lower() for m in _DEFENSE_STRONG_MARKERS):
            continue
        value = _numeric(factor.get("value"))
        if value is not None and value >= 70:
            return True
        note = str(factor.get("note", ""))
        value_text = str(factor.get("value", ""))
        if any(word in note.lower() for word in ("strong", "elite", "top ", "very good", "rock solid")):
            return True
        if any(word in value_text.lower() for word in ("strong", "elite", "top ", "very good", "rock solid")):
            return True
    if disabled_reason:
        return True
    return False


def market_disagreement(prediction: Prediction, fixture: Fixture) -> bool:
    """Market strongly prices against the picked side (real odds only)."""
    price = None
    pick_l = (str(prediction.pick or "")).lower()
    side = "home" if "home" in pick_l else "away" if "away" in pick_l else "draw" if "draw" in pick_l else None
    if side == "home":
        price = fixture.home_odds
    elif side == "away":
        price = fixture.away_odds
    elif side == "draw":
        price = fixture.draw_odds
    if not side or not price or price <= 1.0:
        return False
    return (1.0 / price) < 0.30


def classify_error(
    prediction: Prediction,
    fixture: Fixture,
    won: bool,
    factors: list[dict],
    market_supported: bool | None,
    disabled_reason: str | None,
) -> list[dict[str, str]]:
    """Deterministic error rules over stored data. Empty means 'variance'."""
    confidence = float(prediction.confidence or 0)
    edge = float(prediction.edge_score or 0)
    categories: set[str] = set()
    for f in factors:
        categories.update(_categories_for(f.get("label", "")))
    classifications: list[dict[str, str]] = []

    if won:
        if confidence < 55.0:
            classifications.append({"type": "probability_underconfidence", "reason": f"Won despite only {confidence:.0f}% model probability — the model underrated this outcome."})
        return classifications

    if disabled_reason:
        classifications.append({"type": "low_evidence_loss", "reason": f"No validated evidence yet: {disabled_reason}"})
    if confidence >= 70.0:
        classifications.append({"type": "probability_overconfidence", "reason": f"71→{confidence:.0f}% predicted but the pick lost; outsized certainty was not justified."})
    if edge >= 8.0:
        classifications.append({"type": "high_edge_loss", "reason": f"Model edge ({edge:.1f}%) did not convert to the outcome."})
    if market_supported is True:
        classifications.append({"type": "market_signal_failure", "reason": "Market prices supported the model, yet the result went the other way."})
    elif market_supported is False:
        classifications.append({"type": "market_contradiction", "reason": "The market was pricing against this pick from the start; the result followed the market."})
    if "defense" in categories:
        classifications.append({"type": "defensive_signal_failure", "reason": "Cited defensive signals (opponent defense / conceded / xGA) did not hold."})
    if "offense" in categories:
        classifications.append({"type": "offensive_signal_failure", "reason": "Cited attacking signals (xG / shots / scoring output) over-estimated the picked side."})
    if "lineup" in categories:
        classifications.append({"type": "lineup_uncertainty", "reason": "Lineup/injury uncertainty was present in the read and contributed to the miss."})
    if not classifications:
        classifications.append({"type": "outcome_variance", "reason": "Mid-confidence, no dominant signal failure — consistent with ordinary variance."})
    return classifications


def attribute_signals(prediction: Prediction, fixture: Fixture, won: bool, factors: list[dict]) -> dict[str, Any]:
    """Qualitative signal attribution. Direction is thesis-level, never fake PP points."""
    attributed = []
    for f in factors:
        label = str(f.get("label", "")).strip()
        if not label:
            continue
        categories = _categories_for(label)
        attributed.append({
            "label": label,
            "value": f.get("value"),
            "category": categories[-1] if categories else "general",
        })
    if won:
        return {
            "supporting": attributed,
            "contradicted": [],
            "note": "The cited signals align with the outcome. Attribution is qualitative — REEDS does not claim measured per-signal contributions.",
        }
    return {
        "supporting": [],
        "contradicted": attributed,
        "note": "The cited signals were leaned on but the outcome did not follow. Attribution is qualitative — REEDS does not claim measured per-signal contributions.",
    }


def build_autopsy(
    db: Session,
    prediction: Prediction,
    fixture: Fixture,
    won: bool,
    disabled_reason: str | None = None,
) -> dict[str, Any]:
    """Full structured autopsy for one settled prediction."""
    meta = prediction.engine_meta if isinstance(prediction.engine_meta, dict) else {}
    factors = meta.get("factors") if isinstance(meta.get("factors"), list) else []
    probabilities = meta.get("probabilities") if isinstance(meta.get("probabilities"), dict) else {}

    market_supported: bool | None = None
    try:
        from app.api.public import _market_metrics
        metrics = _market_metrics(prediction, fixture)
        edge_pp = metrics.get("edge_pp") if metrics.get("available") else None
        market_supported = (edge_pp >= 0.0) if edge_pp is not None else None
    except Exception:
        log.exception("Autopsy market metrics failed for prediction %s", prediction.id)
    if market_supported is None:
        has_odds = any(v is not None for v in (fixture.home_odds, fixture.draw_odds, fixture.away_odds))
        if has_odds:
            market_supported = not market_disagreement(prediction, fixture)

    defense_strong = defense_strong_flag(factors, disabled_reason)
    classifications = classify_error(prediction, fixture, won, factors, market_supported, disabled_reason)
    attribution = attribute_signals(prediction, fixture, won, factors)
    return {
        "prediction_id": prediction.id,
        "fixture_id": fixture.id,
        "market": prediction.market,
        "pick": prediction.pick,
        "result": "won" if won else "lost",
        "confidence": round(float(prediction.confidence or 0), 1),
        "edge_score": round(float(prediction.edge_score or 0), 1),
        "market_supported": market_supported,
        "defense_strong": defense_strong,
        "error_classifications": classifications,
        "signal_attribution": attribution,
        "final_score": (
            f"{fixture.home_score}-{fixture.away_score}"
            if fixture.home_score is not None and fixture.away_score is not None else None
        ),
        "generated_at": datetime.utcnow().isoformat(),
        "limitation": "Autopsy is computed from stored data and qualitative signal attribution. It is a diagnostic, not a claim of measured per-signal contribution.",
    }


def _confidence_bucket(prob: float) -> str:
    percent = prob * 100.0
    for low, high in ((0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)):
        if low <= percent < high:
            return f"{low}-{high if high <= 100 else '100'}"
    return "unknown"


def _side(pick: str) -> str:
    pick_l = (pick or "").lower()
    if "home" in pick_l:
        return "home"
    if "away" in pick_l:
        return "away"
    if "draw" in pick_l:
        return "draw"
    return "other"


def aggregate_patterns(db: Session, min_sample: int = 12, gap_threshold: float = 12.0, max_rowsets: int = 2500) -> dict[str, Any]:
    """Cluster autopsies into reviewable pattern candidates.

    Buckets are computed over stored feedback rows and are honestly conditional:
    a candidate fires only when the sample exceeds ``min_sample`` AND mean
    probability meaningfully exceeds realized accuracy (``gap_threshold``).
    Suggested adjustments are proposals only — never automatically applied.
    """
    rows = db.query(ModelFeedback).order_by(ModelFeedback.created_at.desc()).limit(max_rowsets).all()
    groups: dict[tuple, dict[str, Any]] = {}

    def bucket(key: tuple) -> dict[str, Any]:
        return groups.setdefault(key, {"n": 0, "wins": 0, "conf_sum": 0.0, "brier_sum": 0.0, "overconf_loss": 0, "market_failure": 0})

    for row in rows:
        won = row.actual_result == "won"
        conf = row.predicted_probability or 0.5
        classifications = [c.get("type") for c in (row.error_classifications or [])]
        sport = row.sport
        league = row.league or "unknown"
        dim_keys = [
            ("overall", "overall"),
            ("sport", sport),
            ("league", league),
            ("market", sport, row.market),
            ("league_market", league, row.market),
            ("confidence", sport, _confidence_bucket(conf)),
            ("side", sport, league, _side(row.pick)),
            ("defense_context", sport, league, _side(row.pick), "defense" if row.defense_strong else "neutral"),
        ]
        for key in dim_keys:
            group = bucket(key)
            group["n"] += 1
            group["wins"] += int(won)
            group["conf_sum"] += conf * 100.0
            group["brier_sum"] += row.brier_score or 0.0
            if not won and "probability_overconfidence" in classifications:
                group["overconf_loss"] += 1
            if not won and "market_signal_failure" in classifications:
                group["market_failure"] += 1

    segments: list[dict[str, Any]] = []
    for raw_key, g in groups.items():
        n = g["n"]
        if n < min_sample:
            continue
        accuracy = g["wins"] / n * 100.0
        avg_conf = g["conf_sum"] / n
        dimension = raw_key[0]
        gap = avg_conf - accuracy
        candidate = gap > gap_threshold
        if not candidate:
            continue
        brier = g["brier_sum"] / n
        overconf_loss_rate = g["overconf_loss"] / n * 100.0
        market_failure_rate = g["market_failure"] / n * 100.0
        suggestion = round(-(gap) * 0.6, 1) if gap > 0 else None
        segments.append({
            "dimension": dimension,
            "key": list(raw_key[1:]),
            "sample": n,
            "wins": g["wins"],
            "accuracy": round(accuracy, 1),
            "avg_confidence": round(avg_conf, 1),
            "gap_pp": round(gap, 1),
            "brier": round(brier, 3),
            "overconfidence_loss_rate": round(overconf_loss_rate, 1),
            "market_signal_failure_rate": round(market_failure_rate, 1),
            "candidate_issue": f"Model reads {avg_conf:.0f}% confident but only {accuracy:.0f}% landed — potential overconfidence in this dimension.",
            "suggested_adjustment_pp": suggestion,
            "automatic": False,
            "validation_required": True,
            "promotion_rule": "A candidate becomes validated only after out-of-sample review; it is never auto-applied to the live model.",
        })

    segments.sort(key=lambda s: s["gap_pp"], reverse=True)
    return {
        "pattern_candidates": segments,
        "min_sample": min_sample,
        "gap_threshold_pp": gap_threshold,
        "note": "Candidates are conditional-review buckets computed from stored autopsies. Suggested adjustments are proposals and are never applied automatically.",
    }


def attach_autopsy_to_feedback(db: Session, prediction: Prediction, fixture: Fixture, won: bool) -> None:
    """Persist classification + attribution onto the feedback row for a prediction."""
    autopsy = build_autopsy(db, prediction, fixture, won)
    row = db.query(ModelFeedback).filter(ModelFeedback.prediction_id == prediction.id).first()
    if row is None:
        return
    row.error_classifications = autopsy["error_classifications"]
    row.signal_attribution = autopsy["signal_attribution"]
    row.defense_strong = bool(autopsy["defense_strong"])
    row.error_type = (autopsy["error_classifications"][0]["type"] if autopsy["error_classifications"] else None)
    row.updated_at = datetime.utcnow()