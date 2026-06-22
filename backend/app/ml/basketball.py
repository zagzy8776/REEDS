from pathlib import Path

import joblib
import numpy as np
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

    def _bundle_home_win_probability(self, features: dict) -> float | None:
        """Serve both legacy single-model bundles and new ensemble bundles."""

        if not self.bundle:
            return None
        x = pd.DataFrame([features]).reindex(columns=self.bundle["features"], fill_value=0)

        if "model" in self.bundle:
            probs = self.bundle["model"].predict_proba(x)[0]
            classes = list(self.bundle["model"].classes_)
            return float(probs[classes.index(1)]) if 1 in classes else None

        if "models" not in self.bundle:
            return None

        labels = self.bundle.get("labels", [0, 1])
        models = self.bundle["models"]
        weights = self.bundle.get("weights", [1.0] * len(models))
        total_weight = sum(weights) or 1.0
        all_probas = []

        for model in models.values():
            try:
                probs = model.predict_proba(x)[0]
                aligned = np.zeros(len(labels))
                for src_idx, cls in enumerate(model.classes_):
                    if cls in labels:
                        aligned[labels.index(cls)] = probs[src_idx]
                all_probas.append(aligned)
            except Exception:
                all_probas.append(np.ones(len(labels)) / len(labels))

        ensemble_probas = sum(proba * (w / total_weight) for proba, w in zip(all_probas, weights))

        if "meta_learner" in self.bundle and all_probas:
            stacked = np.array([np.concatenate(all_probas)])
            try:
                meta_probas = self.bundle["meta_learner"].predict_proba(stacked)[0]
                aligned_meta = np.zeros(len(labels))
                for src_idx, cls in enumerate(self.bundle["meta_learner"].classes_):
                    if cls in labels:
                        aligned_meta[labels.index(cls)] = meta_probas[src_idx]
                if aligned_meta.sum() > 0:
                    ensemble_probas = 0.7 * ensemble_probas + 0.3 * aligned_meta
            except Exception:
                pass

        return float(ensemble_probas[labels.index(1)]) if 1 in labels else None

    def predict(self, history: pd.DataFrame, fixture: dict, line_total: float | None = None) -> list[dict]:
        home_team = normalize_team_name(fixture["home_team"], "basketball")
        away_team = normalize_team_name(fixture["away_team"], "basketball")
        f = basketball_features_for_fixture(history, home_team, away_team, fixture.get("match_date"))
        home_avg = (f["home_recent_points_for"] + f["away_recent_points_against"]) / 2
        away_avg = (f["away_recent_points_for"] + f["home_recent_points_against"]) / 2
        projected_total = home_avg + away_avg
        spread_edge = (home_avg - away_avg) + 2.5

        # Detect pure-default prediction: no real game history found for either team.
        # home_recent_points_for default=112, away_recent_points_for default=108.
        # When both are at exact defaults, the pick is a guess — cap confidence below publish threshold.
        is_default_fallback = (
            f["home_recent_points_for"] == 112.0
            and f["away_recent_points_for"] == 108.0
            and f["home_win_rate"] == 0.55
            and f["away_win_rate"] == 0.45
        )

        home_win_prob = 0.55
        if self.bundle:
            home_win_prob = self._bundle_home_win_probability(f) or 0.55
        else:
            home_win_prob = min(0.78, max(0.22, 0.50 + (spread_edge / 24)))

        winner_conf = max(home_win_prob, 1 - home_win_prob) * 100
        spread_conf = min(74, max(52, abs(spread_edge) * 4 + 50))

        # Cap confidence below the 55% publish threshold when there's no real data
        if is_default_fallback:
            winner_conf = min(winner_conf, 50.0)
            spread_conf = min(spread_conf, 50.0)
            home_win_prob = 0.50  # treat as coin flip — no edge claimed

        default_line = line_total or 220.5
        total_pick = f"Over {default_line}" if projected_total > default_line else f"Under {default_line}"
        total_conf = round(min(68, max(54, abs(projected_total - default_line) * 0.8 + 54)), 1)
        if is_default_fallback:
            total_conf = min(total_conf, 50.0)

        reason = (
            f"Insufficient game history for {home_team} or {away_team} — using league averages only. "
            "This pick uses default values and should not be treated as a strong signal."
            if is_default_fallback else
            f"Recent scoring projects {home_team} {home_avg:.1f} pts, {away_team} {away_avg:.1f} pts; model win edge {home_win_prob:.1%}. Combined projection: {projected_total:.1f} vs line {default_line}."
        )
        base_meta = {
            "summary": "Basketball engine compares recent points for/against, model win probability, projected total, and spread edge.",
            "factors": [
                {"label": "Home projected points", "value": round(home_avg, 1), "note": f"Blend of {home_team} scoring and {away_team} defense"},
                {"label": "Away projected points", "value": round(away_avg, 1), "note": f"Blend of {away_team} scoring and {home_team} defense"},
                {"label": "Home win probability", "value": f"{home_win_prob:.1%}", "note": "Model/fallback moneyline probability"},
                {"label": "Spread edge", "value": round(spread_edge, 2), "note": "Positive leans home, negative leans away"},
                {"label": "Projected total", "value": round(projected_total, 1), "note": "Estimated combined points"},
            ],
            "probabilities": {"home_win": round(home_win_prob, 4), "away_win": round(1 - home_win_prob, 4)},
            "projection": {"home_points": round(home_avg, 1), "away_points": round(away_avg, 1), "total_points": round(projected_total, 1)},
        }

        spread_line = f"Home {spread_edge:+.1f}" if spread_edge >= 0 else f"Away {spread_edge:+.1f}"
        return [
            {"market": "Moneyline", "pick": "Home Win" if home_win_prob >= 0.5 else "Away Win", "confidence": round(winner_conf, 1), "edge_score": round(winner_conf, 1), "risk_level": _risk(winner_conf), "reasoning": reason, "engine_meta": {**base_meta, "market_logic": "Moneyline chooses the side with the stronger win probability after recent scoring and model edge are blended.", "private": f}},
            {"market": "Point Spread", "pick": spread_line, "confidence": round(spread_conf, 1), "edge_score": round(spread_conf, 1), "risk_level": _risk(spread_conf), "reasoning": reason, "engine_meta": {**base_meta, "market_logic": "Point spread uses projected point margin plus home-court adjustment.", "private": {**f, "spread_edge": spread_edge}}},
            {"market": "Total Points", "pick": total_pick, "confidence": total_conf, "edge_score": total_conf, "risk_level": _risk(total_conf), "reasoning": f"Projected combined points: {projected_total:.1f} vs line {default_line}. {home_team} avg {home_avg:.1f}, {away_team} avg {away_avg:.1f}.", "engine_meta": {**base_meta, "market_logic": "Total points compares the projected combined score with the standard line.", "private": {**f, "projected_total": projected_total}}},
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