"""Quality controls for customer-facing predictions.

STRIPPED: cold-start / edge / confidence gates no longer block publication.
annotate_quality still attaches audit metadata only.
"""

from __future__ import annotations

import math


MARKET_MIN_CONFIDENCE = {
    "1X2": 0.0,
    "Moneyline": 0.0,
    "Goals": 0.0,
    "BTTS": 0.0,
    "Both Teams to Score": 0.0,
    "Double Chance": 0.0,
    "Over/Under 1.5": 0.0,
    "Over/Under 2.5": 0.0,
    "Over/Under 3.5": 0.0,
    "Spread": 0.0,
    "Point Spread": 0.0,
    "Run Line": 0.0,
    "Total Points": 0.0,
    "Total Runs": 0.0,
    "Total Games": 0.0,
    "Correct Score": 0.0,
}

MIN_EDGE_PERCENT = 0.0
HIGH_CONFIDENCE = 72.0
MIN_HIGH_CONFIDENCE_SAMPLE = 0


def _finite(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _edge_percent(value: object) -> float:
    edge = abs(_finite(value))
    return edge * 100.0 if 0.0 < edge <= 1.0 else edge


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


def _default_driven(item: dict) -> bool:
    """STRIPPED: never block default/cold-start output."""
    return False


def evaluate_publication(item: dict) -> tuple[bool, list[str]]:
    """STRIPPED: allow publication of any scored pick."""
    return True, []


def annotate_quality(item: dict) -> dict:
    """Attach an auditable quality decision without changing model output."""
    accepted, reasons = evaluate_publication(item)
    meta = item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else {}
    default_driven = _default_driven(item)
    data_depth = meta.get("data_depth")
    if data_depth is None:
        data_depth = "unknown"
    item["engine_meta"] = {
        **meta,
        "cold_start": bool(meta.get("cold_start") or default_driven),
        "data_depth": data_depth,
        "publication_quality": {
            "accepted": accepted,
            "reasons": reasons,
            "edge_percent": round(_edge_percent(item.get("edge_score")), 3),
            "default_driven": default_driven,
        },
    }
    return item
