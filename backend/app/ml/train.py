from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

from app.core.config import get_settings
from app.ml.calibration import fit_soccer_platt_calibrator
from app.ml.features import build_basketball_features, build_soccer_features

try:
    from xgboost import XGBClassifier
except Exception:  # pragma: no cover
    XGBClassifier = None


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
BASKETBALL_FEATURES = [
    "home_recent_points_for",
    "home_recent_points_against",
    "away_recent_points_for",
    "away_recent_points_against",
    "home_recent_margin",
    "away_recent_margin",
    "home_recent_margin_5",
    "away_recent_margin_5",
    "home_win_rate",
    "away_win_rate",
    "home_elo",
    "away_elo",
    "elo_diff",
    "home_rest_days",
    "away_rest_days",
    "home_back_to_back",
    "away_back_to_back",
]


def train_soccer_model(fixtures: pd.DataFrame) -> dict:
    settings = get_settings()
    fixtures = fixtures[fixtures.get("sport", "soccer") == "soccer"].sort_values("match_date").copy()
    X, y = build_soccer_features(fixtures)
    if len(X) < settings.min_training_rows:
        raise ValueError(f"Need at least {settings.min_training_rows} rows, got {len(X)}")
    X = X.reindex(columns=FEATURES, fill_value=0)
    split_index = max(1, int(len(X) * 0.75))
    if split_index >= len(X):
        split_index = len(X) - 1
    X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]
    if XGBClassifier:
        model = XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.1, reg_alpha=0.2, objective="multi:softprob", eval_metric="mlogloss")
        model_type = "xgboost"
    else:
        model = RandomForestClassifier(n_estimators=250, random_state=42, class_weight="balanced")
        model_type = "random_forest"
    model.fit(X_train, y_train)
    acc = float(accuracy_score(y_test, model.predict(X_test)))
    Path(settings.model_dir).mkdir(parents=True, exist_ok=True)
    path = f"{settings.model_dir}/soccer_{model_type}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.joblib"
    calibrator_path = None
    if len(X) >= max(settings.min_training_rows, 1000):
        try:
            calibrator_path = fit_soccer_platt_calibrator(fixtures, min_train_rows=max(settings.min_training_rows, 1000))["path"]
        except ValueError:
            calibrator_path = None
    joblib.dump({"model": model, "features": FEATURES, "accuracy": acc, "model_type": model_type, "split": "chronological_75_25", "calibrator_path": calibrator_path}, path)
    return {"path": path, "accuracy": acc, "sample_size": len(X), "model_type": model_type, "split": "chronological_75_25", "calibrator_path": calibrator_path}


def train_basketball_model(fixtures: pd.DataFrame) -> dict:
    settings = get_settings()
    fixtures = fixtures[fixtures.get("sport", "basketball") == "basketball"].sort_values("match_date").copy()
    X, y = build_basketball_features(fixtures)
    if len(X) < settings.min_training_rows:
        raise ValueError(f"Need at least {settings.min_training_rows} basketball rows, got {len(X)}")
    X = X.reindex(columns=BASKETBALL_FEATURES, fill_value=0)
    split_index = max(1, int(len(X) * 0.75))
    if split_index >= len(X):
        split_index = len(X) - 1
    X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]
    model = RandomForestClassifier(n_estimators=250, random_state=42, class_weight="balanced")
    model.fit(X_train, y_train)
    acc = float(accuracy_score(y_test, model.predict(X_test)))
    Path(settings.model_dir).mkdir(parents=True, exist_ok=True)
    model_type = "random_forest"
    path = f"{settings.model_dir}/basketball_{model_type}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.joblib"
    joblib.dump({"model": model, "features": BASKETBALL_FEATURES, "accuracy": acc, "model_type": model_type, "split": "chronological_75_25"}, path)
    return {"path": path, "accuracy": acc, "sample_size": len(X), "model_type": model_type, "split": "chronological_75_25"}
