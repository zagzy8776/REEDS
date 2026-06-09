from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

from app.ml.features import build_basketball_features, build_soccer_features
from app.ml.train import BASKETBALL_FEATURES, FEATURES, _build_model_factories, _train_ensemble


@dataclass
class WalkForwardConfig:
    initial_train_ratio: float = 0.6
    step_size: int = 50
    min_train_rows: int = 100


def _safe_log_loss(y_true, probabilities, labels) -> float | None:
    try:
        return float(log_loss(y_true, probabilities, labels=labels))
    except ValueError:
        return None


def _multiclass_brier(y_true: pd.Series, probabilities: np.ndarray, labels: list[int]) -> float:
    label_index = {label: idx for idx, label in enumerate(labels)}
    encoded = np.zeros_like(probabilities, dtype=float)
    for row_idx, value in enumerate(y_true):
        if value in label_index:
            encoded[row_idx, label_index[value]] = 1.0
    return float(np.mean(np.sum((probabilities - encoded) ** 2, axis=1)))


def _binary_brier(y_true: pd.Series, probabilities: np.ndarray, positive_label: int = 1) -> float:
    classes = [0, 1]
    positive_idx = classes.index(positive_label)
    return float(brier_score_loss(y_true, probabilities[:, positive_idx]))


def _confidence_buckets(y_true: list[int], y_pred: list[int], confidences: list[float]) -> list[dict]:
    buckets = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]
    out = []
    for low, high in buckets:
        indexes = [i for i, c in enumerate(confidences) if low <= c < high]
        total = len(indexes)
        wins = sum(1 for i in indexes if y_true[i] == y_pred[i])
        out.append({
            "bucket": f"{int(low * 100)}-{int((high if high < 1 else 1) * 100)}%",
            "total": total,
            "wins": wins,
            "hit_rate": round((wins / total) * 100, 1) if total else 0,
        })
    return out


def walk_forward_backtest(fixtures: pd.DataFrame, sport: str, config: WalkForwardConfig | None = None) -> dict:
    """Run a simple time-series walk-forward validation.

    This is intentionally conservative: it never shuffles rows and only tests future
    windows after training on earlier rows. It is a foundation for future ROI and
    calibration work, not a final betting-proof audit.
    """

    config = config or WalkForwardConfig()
    if sport == "basketball":
        X, y = build_basketball_features(fixtures)
        feature_names = BASKETBALL_FEATURES
        model_factory = lambda: RandomForestClassifier(n_estimators=200, random_state=42, class_weight="balanced")
        labels = [0, 1]
        model_type = "random_forest_walk_forward"
    else:
        X, y = build_soccer_features(fixtures)
        feature_names = FEATURES
        model_factory = lambda: RandomForestClassifier(n_estimators=250, random_state=42, class_weight="balanced")
        labels = [0, 1, 2]
        model_type = "random_forest_walk_forward"

    X = X.reindex(columns=feature_names, fill_value=0)
    if len(X) < max(config.min_train_rows, 20):
        raise ValueError(f"Need at least {max(config.min_train_rows, 20)} rows for walk-forward backtest, got {len(X)}")

    start = max(config.min_train_rows, int(len(X) * config.initial_train_ratio))
    y_true_all: list[int] = []
    y_pred_all: list[int] = []
    confidence_all: list[float] = []
    probability_rows: list[np.ndarray] = []
    windows = []

    for test_start in range(start, len(X), config.step_size):
        test_end = min(test_start + config.step_size, len(X))
        if test_start >= test_end:
            continue
        model = model_factory()
        X_train, y_train = X.iloc[:test_start], y.iloc[:test_start]
        X_test, y_test = X.iloc[test_start:test_end], y.iloc[test_start:test_end]
        model.fit(X_train, y_train)
        probabilities = model.predict_proba(X_test)
        classes = list(model.classes_)
        aligned = np.zeros((len(X_test), len(labels)))
        for source_idx, cls in enumerate(classes):
            if cls in labels:
                aligned[:, labels.index(cls)] = probabilities[:, source_idx]
        preds = [labels[int(np.argmax(row))] for row in aligned]
        confs = [float(np.max(row)) for row in aligned]

        y_true_all.extend([int(v) for v in y_test.tolist()])
        y_pred_all.extend([int(v) for v in preds])
        confidence_all.extend(confs)
        probability_rows.extend(list(aligned))
        window_wins = sum(1 for a, b in zip(y_test.tolist(), preds) if int(a) == int(b))
        windows.append({"train_rows": len(X_train), "test_rows": len(X_test), "accuracy": round(window_wins / len(X_test), 4)})

    probability_matrix = np.array(probability_rows)
    accuracy = float(accuracy_score(y_true_all, y_pred_all)) if y_true_all else 0.0
    brier = _binary_brier(pd.Series(y_true_all), probability_matrix) if sport == "basketball" else _multiclass_brier(pd.Series(y_true_all), probability_matrix, labels)
    ll = _safe_log_loss(y_true_all, probability_matrix, labels)
    return {
        "sport": sport,
        "model_type": model_type,
        "split_strategy": "walk_forward",
        "sample_size": len(y_true_all),
        "accuracy": accuracy,
        "brier_score": brier,
        "log_loss": ll,
        "metrics": {
            "windows": windows,
            "confidence_buckets": _confidence_buckets(y_true_all, y_pred_all, confidence_all),
            "note": "ROI/closing-line value requires complete odds snapshots and will be added when odds history is available.",
        },
    }