from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split

from app.core.config import get_settings
from app.ml.features import build_soccer_features

try:
    from xgboost import XGBClassifier
except Exception:  # pragma: no cover
    XGBClassifier = None


FEATURES = ["home_form_points", "away_form_points", "home_goals_for", "home_goals_against", "away_goals_for", "away_goals_against", "home_implied", "draw_implied", "away_implied"]


def train_soccer_model(fixtures: pd.DataFrame) -> dict:
    settings = get_settings()
    X, y = build_soccer_features(fixtures)
    if len(X) < settings.min_training_rows:
        raise ValueError(f"Need at least {settings.min_training_rows} rows, got {len(X)}")
    X = X.reindex(columns=FEATURES, fill_value=0)
    stratify = y if y.nunique() > 1 and min(y.value_counts()) > 1 else None
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=42, stratify=stratify)
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
    joblib.dump({"model": model, "features": FEATURES, "accuracy": acc, "model_type": model_type}, path)
    return {"path": path, "accuracy": acc, "sample_size": len(X), "model_type": model_type}
