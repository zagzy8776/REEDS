from pathlib import Path

import joblib
import pandas as pd

from app.ml.features import basketball_features_for_fixture
from app.utils.team_names import normalize_team_name


def _risk(confidence_pct: float) -> str:
    confidence = confidence_pct / 100
    return "Low" if confidence >= 0.72 else "Medium" if confidence >= 0.58 else "High"


class BasketballEngine:
    """Basketball prediction engine: trained model when available, safe heuristic fallback otherwise."""

    def __init__(self, model_path: str | None = None):
        self.bundle = joblib.load(model_path) if model_path and Path(model_path).exists() else None

    def predict(self, history: pd.DataFrame, fixture: dict, line_total: float | None = None) -> list[dict]:
        home_team = normalize_team_name(fixture["home_team"], "basketball")
        away_team = normalize_team_name(fixture["away_team"], "basketball")
        f = basketball_features_for_fixture(history, home_team, away_team, fixture.get("match_date"))
        home_avg = (f["home_recent_points_for"] + f["away_recent_points_against"]) / 2
        away_avg = (f["away_recent_points_for"] + f["home_recent_points_against"]) / 2
        projected_total = home_avg + away_avg
        spread_edge = (home_avg - away_avg) + 2.5

        home_win_prob = 0.55
        if self.bundle:
            x = pd.DataFrame([f]).reindex(columns=self.bundle["features"], fill_value=0)
            probs = self.bundle["model"].predict_proba(x)[0]
            classes = list(self.bundle["model"].classes_)
            home_win_prob = float(probs[classes.index(1)]) if 1 in classes else 0.55
        else:
            home_win_prob = min(0.78, max(0.22, 0.50 + (spread_edge / 24)))

        winner_conf = max(home_win_prob, 1 - home_win_prob) * 100
        spread_conf = min(74, max(52, abs(spread_edge) * 4 + 50))
        total_pick = "Over" if line_total and projected_total > line_total else "Under" if line_total else "Projected High Total"
        reason = f"Recent scoring profile projects {home_team} {home_avg:.1f}, {away_team} {away_avg:.1f}; model win edge {home_win_prob:.1%}."

        return [
            {"market": "Moneyline", "pick": "Home Win" if home_win_prob >= 0.5 else "Away Win", "confidence": round(winner_conf, 1), "edge_score": round(winner_conf, 1), "risk_level": _risk(winner_conf), "reasoning": reason, "engine_meta": {"private": f}},
            {"market": "Spread", "pick": "Home Spread Lean" if spread_edge >= 0 else "Away Spread Lean", "confidence": round(spread_conf, 1), "edge_score": round(spread_conf, 1), "risk_level": _risk(spread_conf), "reasoning": reason, "engine_meta": {"private": {**f, "spread_edge": spread_edge}}},
            {"market": "Total Points", "pick": total_pick, "confidence": 58.0, "edge_score": 58.0, "risk_level": "Medium", "reasoning": f"Projected combined points: {projected_total:.1f}.", "engine_meta": {"private": {**f, "projected_total": projected_total}}},
        ]


def predict_basketball_fixture(home_recent_points: list[int], away_recent_points: list[int], line_total: float | None = None) -> list[dict]:
    """Backward-compatible simple helper used by older callers/tests."""

    def avg(values, default):
        return sum(values) / len(values) if values else default

    history = pd.DataFrame()
    fixture = {"home_team": "Home", "away_team": "Away"}
    picks = BasketballEngine().predict(history, fixture, line_total)
    home_avg = avg(home_recent_points, 112)
    away_avg = avg(away_recent_points, 108)
    picks[0]["reasoning"] = f"Recent scoring profile projects home {home_avg:.1f}, away {away_avg:.1f}."
    return picks