from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

from app.core.config import get_settings
from app.ml.features import build_soccer_features

try:
    from xgboost import XGBClassifier
except Exception:  # pragma: no cover
    XGBClassifier = None


FEATURES = ["home_form_points", "away_form_points", "home_goals_for", "home_goals_against", "away_goals_for", "away_goals_against", "home_implied", "draw_implied", "away_implied"]


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
    joblib.dump({"model": model, "features": FEATURES, "accuracy": acc, "model_type": model_type, "split": "chronological_75_25"}, path)
    return {"path": path, "accuracy": acc, "sample_size": len(X), "model_type": model_type, "split": "chronological_75_25"}
