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

MIN_EDGE_PERCENT = 2.5
HIGH_CONFIDENCE = 72.0
MIN_HIGH_CONFIDENCE_SAMPLE = 200


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
    """Detect cold-start / neutral-prior output that must not reach customers.

    Triggers when:
      * engine explicitly flagged cold_start / thin history
      * both teams have zero usable history rows
      * classic neutral priors appear together (form 1.2/1.2, Elo gap ~0,
        projected goals ~2.45 — the exact defaults from features.py)
    """
    meta = item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else {}

    # Explicit flags from the feature / ensemble layer
    if meta.get("cold_start") is True:
        return True
    if str(meta.get("data_depth") or "").lower() in {"none", "thin", "cold_start", "default"}:
        return True
    hist_home = meta.get("history_count_home")
    hist_away = meta.get("history_count_away")
    try:
        if hist_home is not None and hist_away is not None:
            if int(hist_home) <= 0 and int(hist_away) <= 0:
                return True
    except (TypeError, ValueError):
        pass

    summary = str(meta.get("summary") or "").lower()
    if "no completed" in summary and "history" in summary:
        return True
    if "insufficient match-specific" in summary or "neutral prior" in summary:
        return True

    factors = meta.get("factors") if isinstance(meta.get("factors"), list) else []
    values = {
        str(f.get("label")): f.get("value")
        for f in factors
        if isinstance(f, dict) and f.get("label") is not None
    }

    def _f(key, default=None):
        raw = values.get(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    home_form = _f("Home form points")
    away_form = _f("Away form points")
    elo_gap = _f("Elo gap")
    projected = _f("Projected goals")

    # Classic neutral prior conjunction used by features_for_fixture
    prior_hits = 0
    if home_form is not None and abs(home_form - 1.2) < 0.001:
        prior_hits += 1
    if away_form is not None and abs(away_form - 1.2) < 0.001:
        prior_hits += 1
    if elo_gap is not None and abs(elo_gap) < 0.1:
        prior_hits += 1
    if projected is not None and abs(projected - 2.45) < 0.02:
        prior_hits += 1
    if prior_hits >= 3:
        return True

    # Reasoning text often embeds the same priors even when factors are partial
    reasoning = str(item.get("reasoning") or "")
    if (
        "form points, Elo 1125-1125" in reasoning
        or "form (1.2-1.2) and Elo (1125-1125)" in reasoning
        or "Total goal estimate 2.45" in reasoning and "Home avg 1.30" in reasoning
    ):
        return True

    # Scaled default Elo (1500 * 0.75 = 1125) appearing on both sides
    if "Elo 1125-1125" in reasoning or "Elo (1125-1125)" in reasoning:
        return True

    return False


def evaluate_publication(item: dict) -> tuple[bool, list[str]]:
    """Return whether a generated item is strong enough for public publication."""
    reasons: list[str] = []
    confidence = _finite(item.get("confidence"))
    edge_percent = _edge_percent(item.get("edge_score"))
    market = str(item.get("market") or "")
    risk = str(item.get("risk_level") or "Medium")

    threshold = MARKET_MIN_CONFIDENCE.get(market, 68.0)
    if confidence < threshold:
        reasons.append(f"confidence below {threshold:.0f}% market threshold")
    if market == "Correct Score":
        reasons.append("correct-score market is disabled for public picks")
    if risk.lower() == "high":
        reasons.append("high-risk classification")
    if edge_percent < MIN_EDGE_PERCENT:
        reasons.append(f"model edge is below {MIN_EDGE_PERCENT:.1f}%")
    if _default_driven(item):
        reasons.append("team-specific historical evidence is insufficient; neutral/default priors were used")

    if confidence >= HIGH_CONFIDENCE and _sample_size(item) < MIN_HIGH_CONFIDENCE_SAMPLE:
        reasons.append("high confidence requires deeper supporting data")

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
    default_driven = _default_driven(item)
    data_depth = meta.get("data_depth")
    if default_driven:
        data_depth = data_depth or "cold_start"
    elif data_depth is None:
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
