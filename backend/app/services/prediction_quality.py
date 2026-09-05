"""Quality controls for customer-facing predictions.

These checks do not try to predict outcomes themselves. They make publication
more disciplined by requiring usable probabilities, meaningful model edge,
and sufficient supporting data before a pick is shown as a public selection.
"""

from __future__ import annotations

import math


MARKET_MIN_CONFIDENCE = {
    "1X2": 55.0,
    "Moneyline": 55.0,
    "Goals": 55.0,
    "BTTS": 55.0,
    "Both Teams to Score": 55.0,
    "Double Chance": 58.0,
    "Over/Under 1.5": 58.0,
    "Over/Under 2.5": 55.0,
    "Over/Under 3.5": 58.0,
    "Spread": 60.0,
    "Point Spread": 60.0,
    "Run Line": 58.0,
    "Total Points": 55.0,
    "Total Runs": 55.0,
    "Total Games": 55.0,
    "Correct Score": 101.0,
}

# A weak edge is not enough to justify presenting a selection as a strong read.
MIN_EDGE = 0.025
# Confidence above this level needs substantially better evidence.
HIGH_CONFIDENCE = 72.0
MIN_HIGH_CONFIDENCE_SAMPLE = 200


def _finite(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _sample_size(item: dict) -> int:
    meta = item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else {}
    for key in ("sample_size", "training_rows", "history_count", "data_points"):
        value = meta.get(key)
        if value is not None:
            try:
                return max(0, int(float(value)))
            except (TypeError, ValueError):
                pass
    return 0


def evaluate_publication(item: dict) -> tuple[bool, list[str]]:
    """Return whether a generated item is strong enough for public publication."""
    reasons: list[str] = []
    confidence = _finite(item.get("confidence"))
    edge = abs(_finite(item.get("edge_score")))
    market = str(item.get("market") or "")
    risk = str(item.get("risk_level") or "Medium")

    threshold = MARKET_MIN_CONFIDENCE.get(market, 68.0)
    if confidence < threshold:
        reasons.append(f"confidence below {threshold:.0f}% market threshold")
    if market == "Correct Score":
        reasons.append("correct-score market is disabled for public picks")
    if risk.lower() == "high":
        reasons.append("high-risk classification")
    if edge and edge < MIN_EDGE:
        reasons.append("model edge is too small")

    if confidence >= HIGH_CONFIDENCE and _sample_size(item) < MIN_HIGH_CONFIDENCE_SAMPLE:
        reasons.append("high confidence requires deeper supporting data")

    # Reject impossible probability payloads if the engine exposes them.
    meta = item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else {}
    probabilities = meta.get("probabilities")
    if isinstance(probabilities, dict) and probabilities:
        values = [_finite(v, -1.0) for v in probabilities.values()]
        if any(v < 0 or v > 1 for v in values):
            reasons.append("invalid model probability payload")

    return not reasons, reasons


def annotate_quality(item: dict) -> dict:
    """Attach an auditable quality decision without changing model output."""
    accepted, reasons = evaluate_publication(item)
    meta = item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else {}
    item["engine_meta"] = {
        **meta,
        "publication_quality": {
            "accepted": accepted,
            "reasons": reasons,
        },
    }
    return item
