"""Evidence-based live match intelligence.

This module deliberately does not invent probabilities. It converts provider
live score/statistics into a small, explainable state: score state, available
evidence, pressure leader and the concrete provider metrics that support it.
"""
from __future__ import annotations

from datetime import datetime


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("%", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _metric(stats: dict, *names: str):
    for name in names:
        value = stats.get(name)
        if isinstance(value, dict):
            home = _num(value.get("home"))
            away = _num(value.get("away"))
            if home is not None or away is not None:
                return home, away
    return None, None


def build_live_intelligence(home_score, away_score, stats: dict, elapsed=None) -> dict:
    """Build a transparent live state from facts already supplied by a provider."""
    stats = stats if isinstance(stats, dict) else {}
    drivers: list[str] = []
    scores = {"home": home_score, "away": away_score}

    if home_score is not None and away_score is not None:
        if home_score > away_score:
            score_state = "home_leading"
        elif away_score > home_score:
            score_state = "away_leading"
        else:
            score_state = "level"
    else:
        score_state = "unknown"

    possession_h, possession_a = _metric(stats, "Ball Possession", "Possession")
    shots_h, shots_a = _metric(stats, "Total Shots", "Shots")
    target_h, target_a = _metric(stats, "Shots on Goal", "Shots on Target")
    corners_h, corners_a = _metric(stats, "Corner Kicks", "Corners")

    # Pressure is intentionally a relative evidence label, not a probability.
    evidence = {"possession": False, "shots": False, "shots_on_target": False, "corners": False}
    home_score = away_score = 0.0
    away_score = 0.0

    if possession_h is not None and possession_a is not None and possession_h != possession_a:
        evidence["possession"] = True
        if possession_h > possession_a:
            home_score += 1
        else:
            away_score += 1

    if shots_h is not None and shots_a is not None and shots_h != shots_a:
        evidence["shots"] = True
        if shots_h > shots_a:
            home_score += 1
        else:
            away_score += 1

    if target_h is not None and target_a is not None and target_h != target_a:
        evidence["shots_on_target"] = True
        if target_h > target_a:
            home_score += 2
        else:
            away_score += 2

    if corners_h is not None and corners_a is not None and corners_h != corners_a:
        evidence["corners"] = True
        if corners_h > corners_a:
            home_score += 1
        else:
            away_score += 1

    total_evidence = sum(1 for present in evidence.values() if present)
    if total_evidence == 0:
        pressure = "insufficient_live_evidence"
    elif home_score > away_score:
        pressure = "home_pressure"
    elif away_score > home_score:
        pressure = "away_pressure"
    else:
        pressure = "balanced"

    if possession_h is not None and possession_a is not None:
        drivers.append(f"Possession {possession_h:g}%–{possession_a:g}%")
    if shots_h is not None and shots_a is not None:
        drivers.append(f"Shots {shots_h:g}–{shots_a:g}")
    if target_h is not None and target_a is not None:
        drivers.append(f"Shots on target {target_h:g}–{target_a:g}")
    if corners_h is not None and corners_a is not None:
        drivers.append(f"Corners {corners_h:g}–{corners_a:g}")

    return {
        "version": 1,
        "generated_at": datetime.utcnow().isoformat(),
        "elapsed": elapsed,
        "score_state": score_state,
        "score": scores,
        "pressure": pressure,
        "evidence_count": total_evidence,
        "drivers": drivers,
        "evidence": evidence,
        "method": "provider_stats_relative_comparison",
    }
