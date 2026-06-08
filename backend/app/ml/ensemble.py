from pathlib import Path

import joblib
import pandas as pd

from app.ml.features import features_for_fixture
from app.ml.poisson import soccer_probabilities
from app.ml.calibration import apply_calibration
from app.services.customer_copy import high_variance_warning, soccer_reasoning
from app.utils.team_names import normalize_team_name


class LoyalEdgeEngine:
    """Private hybrid engine. Public UI only shows LOYAL EDGE branding."""

    def __init__(self, model_path: str | None = None):
        self.bundle = joblib.load(model_path) if model_path and Path(model_path).exists() else None

    def predict_soccer(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        home_team = normalize_team_name(fixture["home_team"], "soccer")
        away_team = normalize_team_name(fixture["away_team"], "soccer")
        f = features_for_fixture(
            history,
            home_team,
            away_team,
            fixture.get("match_date"),
            fixture.get("league"),
            fixture.get("home_odds"),
            fixture.get("draw_odds"),
            fixture.get("away_odds"),
        )
        home_lam = max((f["home_goals_for"] + f["away_goals_against"]) / 2, 0.2)
        away_lam = max((f["away_goals_for"] + f["home_goals_against"]) / 2, 0.2)
        p = soccer_probabilities(home_lam, away_lam)
        ml_probs = {"away": 0.33, "draw": 0.33, "home": 0.34}

        if self.bundle:
            x = pd.DataFrame([f]).reindex(columns=self.bundle["features"], fill_value=0)
            probs = self.bundle["model"].predict_proba(x)[0]
            cls_map = {0: "away", 1: "draw", 2: "home"}
            ml_probs = {cls_map.get(c, str(c)): float(probs[i]) for i, c in enumerate(self.bundle["model"].classes_)}
            ml_probs = apply_calibration(ml_probs, self.bundle.get("calibrator_path"))

        form_total = f["home_form_points"] + f["away_form_points"] + 0.01
        one_x_two = {
            "Home Win": 0.45 * ml_probs.get("home", 0.34) + 0.35 * p["home"] + 0.20 * (f["home_form_points"] / form_total),
            "Draw": 0.45 * ml_probs.get("draw", 0.33) + 0.35 * p["draw"] + 0.20 * 0.30,
            "Away Win": 0.45 * ml_probs.get("away", 0.33) + 0.35 * p["away"] + 0.20 * (f["away_form_points"] / form_total),
        }
        pick_1x2, conf_1x2 = max(one_x_two.items(), key=lambda x: x[1])
        over_conf = 0.65 * p["over25"] + 0.35 * min((home_lam + away_lam) / 4, 1)
        btts_conf = 0.70 * p["btts"] + 0.30 * min(min(home_lam, away_lam) / 2.5, 1)

        # Double Chance: Home or Draw, Away or Draw, Home or Away
        dc_home = p["home"] + p["draw"]
        dc_away = p["away"] + p["draw"]
        dc_no_draw = p["home"] + p["away"]
        double_chance_picks = {"Home or Draw": dc_home, "Away or Draw": dc_away, "Home or Away": dc_no_draw}
        dc_pick, dc_conf = max(double_chance_picks.items(), key=lambda x: x[1])

        # Over/Under 1.5
        from app.ml.poisson import poisson_pmf as _pmf
        over15 = sum(_pmf(h, home_lam) * _pmf(a, away_lam)
                     for h in range(7) for a in range(7) if h + a > 1.5)
        # Over/Under 3.5
        over35 = sum(_pmf(h, home_lam) * _pmf(a, away_lam)
                     for h in range(7) for a in range(7) if h + a > 3.5)

        def risk(c: float) -> str:
            return "Low" if c >= 0.72 else "Medium" if c >= 0.58 else "High"

        reason_1x2 = f"Model rates {pick_1x2} based on {f['home_form_points']:.1f} vs {f['away_form_points']:.1f} form points, Elo {f['home_elo']:.0f}-{f['away_elo']:.0f}, and league strength {f['league_strength']:.2f}. {home_team} scoring avg {f['home_goals_for']:.2f}, {away_team} avg {f['away_goals_for']:.2f}."
        reason_goals = f"Total goal estimate {home_lam + away_lam:.2f}. Home avg {f['home_goals_for']:.2f}, away avg {f['away_goals_for']:.2f}. Clean sheet rates: home {f['home_clean_sheet_rate_5']:.0%}, away {f['away_clean_sheet_rate_5']:.0%}."
        reason_btts = f"Both teams scoring probability {p['btts']:.1%}. {home_team} failed to score {f['home_failed_score_rate_5']:.0%} of last 5, {away_team} {f['away_failed_score_rate_5']:.0%}."
        reason_dc = f"Double chance backed by form ({f['home_form_points']:.1f}-{f['away_form_points']:.1f}) and Elo ({f['home_elo']:.0f}-{f['away_elo']:.0f}). League quality: {f['league_strength']:.2f}."

        score_conf = max(1.0, min(p.get("score_prob", 0.0) * 100, 42.0))
        btts_pick = "BTTS Yes" if btts_conf >= 0.53 else "BTTS No"
        btts_final_conf = round(max(btts_conf, 1 - btts_conf) * 100, 1)
        goals_pick = "Over 2.5 Goals" if over_conf >= 0.5 else "Under 2.5 Goals"
        goals_conf = round(max(over_conf, 1 - over_conf) * 100, 1)
        over15_pick = "Over 1.5 Goals" if over15 >= 0.5 else "Under 1.5 Goals"
        over15_conf = round(max(over15, 1 - over15) * 100, 1)
        over35_pick = "Over 3.5 Goals" if over35 >= 0.5 else "Under 3.5 Goals"
        over35_conf = round(max(over35, 1 - over35) * 100, 1)

        return [
            {"market": "1X2", "pick": pick_1x2, "confidence": round(conf_1x2 * 100, 1), "edge_score": round(conf_1x2 * 100, 1), "risk_level": risk(conf_1x2), "reasoning": reason_1x2, "engine_meta": {"private": {**p, "home_lam": home_lam, "away_lam": away_lam}}},
            {"market": "Double Chance", "pick": dc_pick, "confidence": round(dc_conf * 100, 1), "edge_score": round(dc_conf * 100, 1), "risk_level": risk(dc_conf), "reasoning": reason_dc, "engine_meta": {"private": p}},
            {"market": "Goals", "pick": goals_pick, "confidence": goals_conf, "edge_score": goals_conf, "risk_level": risk(goals_conf / 100), "reasoning": reason_goals, "engine_meta": {"private": p}},
            {"market": "Over/Under 1.5", "pick": over15_pick, "confidence": over15_conf, "edge_score": over15_conf, "risk_level": risk(over15_conf / 100), "reasoning": reason_goals, "engine_meta": {"private": p}},
            {"market": "Over/Under 3.5", "pick": over35_pick, "confidence": over35_conf, "edge_score": over35_conf, "risk_level": risk(over35_conf / 100), "reasoning": reason_goals, "engine_meta": {"private": p}},
            {"market": "BTTS", "pick": btts_pick, "confidence": btts_final_conf, "edge_score": btts_final_conf, "risk_level": risk(btts_final_conf / 100), "reasoning": reason_btts, "engine_meta": {"private": p}},
            {"market": "Correct Score", "pick": p["score"], "confidence": round(score_conf, 1), "edge_score": round(score_conf, 1), "risk_level": "High", "reasoning": high_variance_warning(), "engine_meta": {"private": p}},
        ]

