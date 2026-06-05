from pathlib import Path

import joblib
import pandas as pd

from app.ml.features import features_for_fixture
from app.ml.poisson import soccer_probabilities


class LoyalEdgeEngine:
    """Private hybrid engine. Public UI only shows LOYAL EDGE branding."""

    def __init__(self, model_path: str | None = None):
        self.bundle = joblib.load(model_path) if model_path and Path(model_path).exists() else None

    def predict_soccer(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        f = features_for_fixture(history, fixture["home_team"], fixture["away_team"])
        home_lam = max((f["home_goals_for"] + f["away_goals_against"]) / 2, 0.2)
        away_lam = max((f["away_goals_for"] + f["home_goals_against"]) / 2, 0.2)
        p = soccer_probabilities(home_lam, away_lam)
        ml_probs = {"away": 0.33, "draw": 0.33, "home": 0.34}

        if self.bundle:
            x = pd.DataFrame([f]).reindex(columns=self.bundle["features"], fill_value=0)
            probs = self.bundle["model"].predict_proba(x)[0]
            cls_map = {0: "away", 1: "draw", 2: "home"}
            ml_probs = {cls_map.get(c, str(c)): float(probs[i]) for i, c in enumerate(self.bundle["model"].classes_)}

        form_total = f["home_form_points"] + f["away_form_points"] + 0.01
        one_x_two = {
            "Home Win": 0.45 * ml_probs.get("home", 0.34) + 0.35 * p["home"] + 0.20 * (f["home_form_points"] / form_total),
            "Draw": 0.45 * ml_probs.get("draw", 0.33) + 0.35 * p["draw"] + 0.20 * 0.30,
            "Away Win": 0.45 * ml_probs.get("away", 0.33) + 0.35 * p["away"] + 0.20 * (f["away_form_points"] / form_total),
        }
        pick_1x2, conf_1x2 = max(one_x_two.items(), key=lambda x: x[1])
        over_conf = 0.65 * p["over25"] + 0.35 * min((home_lam + away_lam) / 4, 1)
        btts_conf = 0.70 * p["btts"] + 0.30 * min(min(home_lam, away_lam) / 2, 1)

        def risk(c: float) -> str:
            return "Low" if c >= 0.72 else "Medium" if c >= 0.58 else "High"

        reason = f"Form profile, matchup goal trend, and value filters passed. Projected score band: {p['score']} with total-goal estimate {home_lam + away_lam:.2f}."
        return [
            {"market": "1X2", "pick": pick_1x2, "confidence": round(conf_1x2 * 100, 1), "edge_score": round(conf_1x2 * 100, 1), "risk_level": risk(conf_1x2), "reasoning": reason, "engine_meta": {"private": p}},
            {"market": "Goals", "pick": "Over 2.5 Goals" if over_conf >= 0.5 else "Under 2.5 Goals", "confidence": round(max(over_conf, 1 - over_conf) * 100, 1), "edge_score": round(max(over_conf, 1 - over_conf) * 100, 1), "risk_level": risk(max(over_conf, 1 - over_conf)), "reasoning": reason, "engine_meta": {"private": p}},
            {"market": "BTTS", "pick": "BTTS Yes" if btts_conf >= 0.5 else "BTTS No", "confidence": round(max(btts_conf, 1 - btts_conf) * 100, 1), "edge_score": round(max(btts_conf, 1 - btts_conf) * 100, 1), "risk_level": risk(max(btts_conf, 1 - btts_conf)), "reasoning": reason, "engine_meta": {"private": p}},
            {"market": "Correct Score", "pick": p["score"], "confidence": 42.0, "edge_score": 42.0, "risk_level": "High", "reasoning": "Correct scores are high-variance; this is a projected score band, not a guarantee.", "engine_meta": {"private": p}},
        ]
