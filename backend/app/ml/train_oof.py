"""Production-safe OOF ensemble trainer.

Chronological holdout stays untouched. Expanding-window OOF trains the meta
learner. Training uses only signals that exist in the historical corpus and
removes cold-start fixtures from the supervised sample while preserving their
state for feature construction.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import TimeSeriesSplit

from app.core.config import get_settings
from app.ml.features import build_basketball_features, build_soccer_features
from app.ml.train import (
    BASKETBALL_FEATURES,
    FEATURES,
    GENERIC_SPORT_FEATURES,
    _build_generic_features,
    _build_model_factories,
)
from app.utils.team_names import normalize_team_name


# Historical corpus has bookmaker odds, but does not contain reliable
# opening/closing movement, weather, injury, referee, or public-bet snapshots.
# Those live-only signals must not become synthetic zero-valued training inputs.
_DEAD_HISTORICAL_FEATURES = {
    "sharp_home_move",
    "sharp_away_move",
    "clv_home_signal",
    "insider_sharp_home_move",
    "insider_sharp_away_move",
    "insider_clv_home",
    "insider_steam",
    "insider_opening_home_prob",
    "insider_weather_precip",
    "insider_weather_wind",
    "insider_home_injury",
    "insider_away_injury",
    "insider_referee_cards",
    "insider_public_home_pct",
}

SOCCER_TRAINING_FEATURES = [
    f for f in FEATURES if f not in _DEAD_HISTORICAL_FEATURES
]
if "h2h_meetings" not in SOCCER_TRAINING_FEATURES:
    SOCCER_TRAINING_FEATURES.append("h2h_meetings")


def _aligned_proba(model, X, labels):
    proba = model.predict_proba(X)
    out = np.zeros((len(X), len(labels)), dtype=float)
    for i, cls in enumerate(model.classes_):
        if cls in labels:
            out[:, labels.index(cls)] = proba[:, i]
    return out


def _class_weight_dict(y) -> dict:
    values, counts = np.unique(np.asarray(y), return_counts=True)
    total = float(counts.sum())
    n_classes = max(len(values), 1)
    return {int(v): float(total / (n_classes * c)) for v, c in zip(values, counts)}


def _recency_weights(index_like, match_dates: pd.Series | None) -> np.ndarray | None:
    if match_dates is None or len(match_dates) == 0:
        return None
    try:
        dates = pd.to_datetime(match_dates.loc[index_like], errors="coerce")
    except Exception:
        return None
    if dates.isna().all():
        return None
    max_ts = dates.max()
    age_days = (max_ts - dates).dt.total_seconds() / 86400.0
    age_days = age_days.fillna(age_days.median() if age_days.notna().any() else 0)
    weights = np.exp(-np.log(2) * age_days.to_numpy(dtype=float) / (365.0 * 2.0))
    return np.clip(weights, 0.15, 1.0)


def _apply_class_weight(model, name: str, y):
    try:
        if name in {"random_forest", "lightgbm"} and hasattr(model, "set_params"):
            model.set_params(class_weight="balanced")
    except Exception:
        pass
    return model, _class_weight_dict(y)


def _soccer_training_state(fixtures: pd.DataFrame):
    """Return masks/signals computed strictly from prior fixtures."""
    counts: dict[str, int] = {}
    h2h_counts: dict[tuple[str, str], int] = {}
    last_match: dict[str, pd.Timestamp] = {}
    eligible = []
    h2h = []
    home_rest = []
    away_rest = []

    for _, row in fixtures.iterrows():
        home = normalize_team_name(str(row["home_team"]), "soccer")
        away = normalize_team_name(str(row["away_team"]), "soccer")
        key = tuple(sorted((home, away)))
        eligible.append(counts.get(home, 0) >= 5 and counts.get(away, 0) >= 5)
        h2h.append(float(h2h_counts.get(key, 0)))

        current = pd.to_datetime(row.get("match_date"), errors="coerce")
        hr = 4.0
        ar = 4.0
        if not pd.isna(current):
            if home in last_match:
                hr = max((current - last_match[home]).total_seconds() / 86400.0, 0.0)
            if away in last_match:
                ar = max((current - last_match[away]).total_seconds() / 86400.0, 0.0)
        home_rest.append(hr)
        away_rest.append(ar)

        counts[home] = counts.get(home, 0) + 1
        counts[away] = counts.get(away, 0) + 1
        h2h_counts[key] = h2h_counts.get(key, 0) + 1
        if not pd.isna(current):
            last_match[home] = current
            last_match[away] = current

    return np.asarray(eligible, dtype=bool), np.asarray(h2h), np.asarray(home_rest), np.asarray(away_rest)


def _fit_oof_ensemble(X_train, y_train, X_test, y_test, factories, labels, n_splits=3, sample_weight_train=None):
    models = {}
    oof_by_model = {}
    holdout_by_model = {}
    scores = {}

    splitter = TimeSeriesSplit(n_splits=n_splits)
    for name, factory in factories:
        try:
            oof = np.full((len(X_train), len(labels)), np.nan, dtype=float)
            fold_scores = []
            for fold_train, fold_valid in splitter.split(X_train):
                if len(fold_train) < 30 or len(fold_valid) < 5:
                    continue
                model = factory(None)
                model, _ = _apply_class_weight(model, name, y_train.iloc[fold_train])
                fit_kwargs = {}
                if sample_weight_train is not None:
                    fit_kwargs["sample_weight"] = sample_weight_train[fold_train]
                try:
                    model.fit(X_train.iloc[fold_train], y_train.iloc[fold_train], **fit_kwargs)
                except TypeError:
                    model.fit(X_train.iloc[fold_train], y_train.iloc[fold_train])
                pred = model.predict(X_train.iloc[fold_valid])
                fold_scores.append(float(accuracy_score(y_train.iloc[fold_valid], pred)))
                oof[fold_valid] = _aligned_proba(model, X_train.iloc[fold_valid], labels)

            valid_mask = ~np.isnan(oof).any(axis=1)
            if valid_mask.sum() < max(30, len(labels) * 10):
                print(f"  {name}: SKIPPED (insufficient OOF rows: {int(valid_mask.sum())})")
                continue

            final_model = factory(None)
            final_model, _ = _apply_class_weight(final_model, name, y_train)
            fit_kwargs = {}
            if sample_weight_train is not None:
                fit_kwargs["sample_weight"] = sample_weight_train
            try:
                final_model.fit(X_train, y_train, **fit_kwargs)
            except TypeError:
                final_model.fit(X_train, y_train)
            models[name] = final_model
            oof_by_model[name] = oof
            holdout_by_model[name] = _aligned_proba(final_model, X_test, labels)
            scores[name] = float(np.mean(fold_scores)) if fold_scores else 0.5
            print(f"  {name}: OOF accuracy={scores[name]:.4f}, OOF rows={int(valid_mask.sum())}")
        except Exception as exc:
            print(f"  {name}: SKIPPED ({exc})")
            continue

    if not models:
        raise ValueError("No models could be trained in OOF ensemble")

    valid_mask = np.ones(len(X_train), dtype=bool)
    for values in oof_by_model.values():
        valid_mask &= ~np.isnan(values).any(axis=1)

    stacked_oof = np.column_stack([oof_by_model[name][valid_mask] for name in models])
    y_oof = np.asarray(y_train.iloc[np.flatnonzero(valid_mask)])
    meta = None
    meta_holdout = None
    if len(np.unique(y_oof)) >= 2 and len(y_oof) >= 30:
        meta = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs", class_weight="balanced")
        meta.fit(stacked_oof, y_oof)
        stacked_holdout = np.column_stack([holdout_by_model[name] for name in models])
        meta_holdout = meta.predict_proba(stacked_holdout)
        aligned_meta = np.zeros((len(X_test), len(labels)), dtype=float)
        for i, cls in enumerate(meta.classes_):
            if cls in labels:
                aligned_meta[:, labels.index(cls)] = meta_holdout[:, i]
        meta_holdout = aligned_meta

    raw_scores = np.array([max(scores.get(name, 0.5), 0.5) for name in models], dtype=float)
    weights = raw_scores / raw_scores.sum()
    ensemble = sum(holdout_by_model[name] * weight for name, weight in zip(models, weights))
    final_proba = ensemble if meta_holdout is None else 0.7 * ensemble + 0.3 * meta_holdout
    final_proba = np.clip(final_proba, 1e-6, 1 - 1e-6)
    final_proba = final_proba / final_proba.sum(axis=1, keepdims=True)
    final_preds = np.asarray([labels[int(np.argmax(row))] for row in final_proba])
    accuracy = float(accuracy_score(y_test, final_preds))
    try:
        ll = float(log_loss(y_test, final_proba, labels=labels))
    except Exception:
        ll = None
    print(f"  holdout accuracy={accuracy:.4f}" + (f", log_loss={ll:.4f}" if ll is not None else ""))

    return {
        "models": models,
        "meta_learner": meta,
        "accuracy": accuracy,
        "log_loss": ll,
        "model_types": list(models.keys()),
        "weights": weights.tolist(),
        "ensemble_probas": final_proba,
    }


def _factories(binary: bool):
    return _build_model_factories(binary=binary, slim=False)


def _save_bundle(result, features, labels, sport, sample_size, calibrator_path=None):
    settings = get_settings()
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    path = model_dir / f"{sport}_oof_ensemble_{stamp}.joblib"
    model_type = "+".join(result["model_types"]) if result.get("model_types") else "oof_ensemble"
    bundle = {
        "bundle_version": 3,
        "sport": sport,
        "models": result["models"],
        "meta_learner": result["meta_learner"],
        "features": features,
        "model_types": result["model_types"],
        "weights": result["weights"],
        "accuracy": result["accuracy"],
        "log_loss": result.get("log_loss"),
        "sample_size": sample_size,
        "split": "chronological_70_30_oof_meta",
        "calibrator_path": calibrator_path,
        "labels": labels,
        "feature_medians": result.get("feature_medians", {}),
        "training_method": "expanding_window_oof_meta_recency_classweight_nan_medians_v2",
        "runtime_versions": {
            "python": f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}.{__import__('sys').version_info.micro}",
            "scikit_learn": sklearn.__version__,
            "numpy": np.__version__,
            "joblib": joblib.__version__,
        },
    }
    joblib.dump(bundle, path, compress=3)
    return {
        "path": str(path),
        "full_path": str(path),
        "sport": sport,
        "accuracy": result["accuracy"],
        "log_loss": result.get("log_loss"),
        "sample_size": sample_size,
        "model_type": model_type,
        "split": "chronological_70_30_oof_meta",
        "models_trained": result["model_types"],
        "training_method": "expanding_window_oof_meta_recency_classweight",
        "runtime_versions": bundle["runtime_versions"],
    }


def _filter_recent(fixtures: pd.DataFrame) -> pd.DataFrame:
    import os
    years = float(os.environ.get("REEDS_TRAIN_YEARS", "6"))
    if "match_date" not in fixtures.columns or fixtures.empty:
        return fixtures
    dates = pd.to_datetime(fixtures["match_date"], errors="coerce")
    max_date = dates.max()
    if pd.isna(max_date):
        return fixtures
    cutoff = max_date - pd.Timedelta(days=int(365 * years))
    mask = dates >= cutoff
    kept = fixtures.loc[mask].copy()
    print(f"  recency filter: kept {len(kept):,}/{len(fixtures):,} rows (last {years:.0f}y)")
    return kept if len(kept) >= 500 else fixtures


def _split_and_train(X, y, labels, factories, sport, features, match_dates=None):
    settings = get_settings()
    if len(X) < settings.min_training_rows:
        raise ValueError(f"Need at least {settings.min_training_rows} rows, got {len(X)}")
    split_index = max(int(len(X) * 0.7), settings.min_training_rows)
    if split_index >= len(X):
        split_index = len(X) - max(10, int(len(X) * 0.15))
    X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]
    if len(X_test) < 5:
        raise ValueError(f"Test set too small ({len(X_test)})")

    # NaN-aware training: missing feature values (teams with no history for a
    # slice) are imputed with the TRAINING-SET median — computed from real
    # data and stored in the bundle, never a hardcoded constant.
    feature_medians = {k: float(v) for k, v in X_train.median(numeric_only=True).to_dict().items()}
    X_train = X_train.fillna(feature_medians)
    X_test = X_test.fillna(feature_medians)

    sw = _recency_weights(X_train.index, match_dates) if match_dates is not None else None
    result = _fit_oof_ensemble(X_train, y_train, X_test, y_test, factories, labels, sample_weight_train=sw)
    result["feature_medians"] = feature_medians
    return _save_bundle(result, features, labels, sport, len(X))


def train_soccer_model_oof(fixtures):
    if "sport" in fixtures.columns:
        fixtures = fixtures[fixtures["sport"] == "soccer"].sort_values("match_date").copy()
    else:
        fixtures = fixtures.sort_values("match_date").copy()
    fixtures = _filter_recent(fixtures)

    eligible, h2h_counts, home_rest, away_rest = _soccer_training_state(fixtures)
    X, y = build_soccer_features(fixtures)

    # build_soccer_features preserves chronological state, so apply the
    # cold-start mask only after feature construction.
    if len(eligible) == len(X):
        X = X.copy()
        X["h2h_meetings"] = h2h_counts
        X["home_rest_days"] = home_rest
        X["away_rest_days"] = away_rest
        X["rest_advantage"] = home_rest - away_rest
        X["home_back_to_back"] = (home_rest < 3.0).astype(int)
        X["away_back_to_back"] = (away_rest < 3.0).astype(int)
        X = X.loc[eligible].reset_index(drop=True)
        y = y.loc[eligible].reset_index(drop=True)

    X = X.reindex(columns=SOCCER_TRAINING_FEATURES, fill_value=0)
    dates = pd.to_datetime(fixtures["match_date"], errors="coerce") if "match_date" in fixtures.columns else None
    if dates is not None and len(dates) == len(eligible):
        dates = dates.loc[eligible].reset_index(drop=True)
        match_dates = pd.Series(dates.to_numpy(), index=X.index)
    else:
        match_dates = None
    print(f"  soccer training schema: {len(SOCCER_TRAINING_FEATURES)} features")
    print(f"  soccer cold-start rows excluded: {int((~eligible).sum())}")
    return _split_and_train(X, y, [0, 1, 2], _factories(False), "soccer", SOCCER_TRAINING_FEATURES, match_dates=match_dates)


def train_basketball_model_oof(fixtures):
    if "sport" in fixtures.columns:
        fixtures = fixtures[fixtures["sport"] == "basketball"].sort_values("match_date").copy()
    else:
        fixtures = fixtures.sort_values("match_date").copy()
    X, y = build_basketball_features(fixtures)
    X = X.reindex(columns=BASKETBALL_FEATURES, fill_value=0)
    return _split_and_train(X, y, [0, 1], _factories(True), "basketball", BASKETBALL_FEATURES)


def train_generic_sport_model_oof(fixtures, sport: str):
    sport = str(sport).strip().lower()
    X, y = _build_generic_features(fixtures, sport)
    X = X.reindex(columns=GENERIC_SPORT_FEATURES, fill_value=0)
    return _split_and_train(X, y, [0, 1], _factories(True), sport, GENERIC_SPORT_FEATURES)
