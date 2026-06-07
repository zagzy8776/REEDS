from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression

from app.core.config import get_settings
from app.ml.features import build_soccer_features


FEATURES = [
    "home_form_points",
    "away_form_points",
    "home_form_points_3",
    "away_form_points_3",
    "home_form_points_5",
    "away_form_points_5",
    "home_win_rate_5",
    "away_win_rate_5",
    "home_draw_rate_5",
    "away_draw_rate_5",
    "home_loss_rate_5",
    "away_loss_rate_5",
    "home_goals_for",
    "home_goals_against",
    "away_goals_for",
    "away_goals_against",
    "home_home_goals_for",
    "home_home_goals_against",
    "away_away_goals_for",
    "away_away_goals_against",
    "home_goal_diff",
    "away_goal_diff",
    "home_clean_sheet_rate_5",
    "away_clean_sheet_rate_5",
    "home_failed_score_rate_5",
    "away_failed_score_rate_5",
    "home_unbeaten_rate_10",
    "away_unbeaten_rate_10",
    "home_elo",
    "away_elo",
    "elo_diff",
    "league_strength",
    "home_implied",
    "draw_implied",
    "away_implied",
]


def calibration_path_for(model_path: str) -> str:
    path = Path(model_path)
    return str(path.with_name(f"{path.stem}_calibrator.joblib"))


def fit_soccer_platt_calibrator(fixtures: pd.DataFrame, min_train_rows: int = 1000) -> dict:
    """Fit a one-vs-rest Platt calibrator from walk-forward probabilities.

    The calibrator learns how raw RandomForest probabilities map to real outcomes.
    It is deliberately small and saved separately from the main model artifact.
    """

    X, y = build_soccer_features(fixtures)
    X = X.reindex(columns=FEATURES, fill_value=0)
    if len(X) < min_train_rows:
        raise ValueError(f"Need at least {min_train_rows} soccer rows for calibration, got {len(X)}")

    labels = [0, 1, 2]
    start = max(min_train_rows, int(len(X) * 0.6))
    step = max(250, int(len(X) * 0.08))
    raw_probs: list[np.ndarray] = []
    true_labels: list[int] = []

    for test_start in range(start, len(X), step):
        test_end = min(test_start + step, len(X))
        model = RandomForestClassifier(n_estimators=250, random_state=42, class_weight="balanced")
        model.fit(X.iloc[:test_start], y.iloc[:test_start])
        probs = model.predict_proba(X.iloc[test_start:test_end])
        aligned = np.zeros((test_end - test_start, len(labels)))
        for source_idx, cls in enumerate(model.classes_):
            if cls in labels:
                aligned[:, labels.index(cls)] = probs[:, source_idx]
        raw_probs.extend(list(aligned))
        true_labels.extend([int(v) for v in y.iloc[test_start:test_end].tolist()])

    if not raw_probs:
        raise ValueError("No calibration folds were produced")

    calibrator = LogisticRegression(max_iter=1000, multi_class="ovr")
    calibrator.fit(np.array(raw_probs), np.array(true_labels))
    settings = get_settings()
    Path(settings.model_dir).mkdir(parents=True, exist_ok=True)
    path = f"{settings.model_dir}/soccer_platt_calibrator.joblib"
    joblib.dump({"calibrator": calibrator, "labels": labels, "sample_size": len(true_labels), "method": "platt_logistic_ovr"}, path)
    return {"path": path, "sample_size": len(true_labels), "method": "platt_logistic_ovr"}


def apply_calibration(raw_probs: dict[str, float], calibrator_path: str | None) -> dict[str, float]:
    if not calibrator_path or not Path(calibrator_path).exists():
        return raw_probs
    bundle = joblib.load(calibrator_path)
    labels = bundle["labels"]
    ordered = np.array([[raw_probs.get("away", 0.0), raw_probs.get("draw", 0.0), raw_probs.get("home", 0.0)]])
    calibrated = bundle["calibrator"].predict_proba(ordered)[0]
    cls_map = {0: "away", 1: "draw", 2: "home"}
    return {cls_map.get(label, str(label)): float(calibrated[idx]) for idx, label in enumerate(labels)}