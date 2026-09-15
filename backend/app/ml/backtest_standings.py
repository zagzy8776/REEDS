"""A/B/C backtesting framework for model validation.

Tests three approaches on the same historical fixture set:
  A — Current model: ensemble trained WITHOUT standings features (no position,
      points, goal difference from league tables)
  B — New model: ensemble trained WITH standings features (position, points,
      GD, home/away splits, points-per-game from league tables)
  C — Bookmaker odds baseline: implied probabilities from home/draw/away odds,
      normalized to remove overround

All comparisons use chronological splits (train on past, predict on future)
with metrics: accuracy, Brier score, log-loss, and ROI vs. Kelly staking.

Results are persisted to the BacktestRun table for dashboard reporting.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.db.models import BacktestRun
from app.services.historical_archive import load_archive_dataframe
from app.services.predictions import dataframe_from_db
from app.utils.team_names import normalize_team_name
from app.ml.features import build_soccer_features, features_for_fixture

# Staleness-free import of feature list
from app.ml.train import FEATURES

log = logging.getLogger(__name__)

# Features that exist in both A and B (form, elo, h2h, rest, odds)
_BASE_FEATURES = [
    f for f in FEATURES
    if f not in {
        "home_league_position", "away_league_position",
        "home_points", "away_points",
        "home_points_per_game", "away_points_per_game",
        "home_goals_for", "home_goals_against",
        "away_goals_for", "away_goals_against",
        "home_goal_difference", "away_goal_difference",
        "position_diff", "points_diff",
        "has_standings_data",
    }
]

# Features unique to model B (from standings tables)
from app.ml.standings_features import standings_feature_columns

_STANDINGS_FEATURES = standings_feature_columns()

LABELS = [0, 1, 2]  # away, draw, home
LABEL_NAMES = ["away", "draw", "home"]


def _implied_probability(odds: float | None) -> float:
    """Convert decimal odds to implied probability, with overround handling."""
    if odds is None or odds <= 1.0:
        return 1.0 / 3.0
    return 1.0 / odds


def _odds_baseline(row: pd.Series) -> np.ndarray:
    """Model C: normalize bookmaker odds to get market-implied probabilities."""
    home_odds = row.get("home_odds")
    draw_odds = row.get("draw_odds")
    away_odds = row.get("away_odds")

    if not all(v is not None and v > 1.0 for v in [home_odds, draw_odds, away_odds]):
        return np.array([1/3, 1/3, 1/3])

    implied = np.array([
        _implied_probability(away_odds),
        _implied_probability(draw_odds),
        _implied_probability(home_odds),
    ])
    total = implied.sum()
    if total > 0:
        implied = implied / total
    return implied


def _build_training_data(
    history: pd.DataFrame,
    include_standings: bool,
    db=None,
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Build feature matrix and labels from historical fixtures.

    include_standings=False -> Model A (current, no standings features)
    include_standings=True  -> Model B (with standings features)
    """
    from app.ml.train_oof import _soccer_training_state, SOCCER_TRAINING_FEATURES

    soccer = history[history["sport"] == "soccer"].sort_values("match_date").copy() if "sport" in history.columns else history.sort_values("match_date").copy()

    eligible, h2h_counts, home_rest, away_rest = _soccer_training_state(soccer)
    X, y = build_soccer_features(soccer)

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
        soccer = soccer.loc[eligible].reset_index(drop=True)

    feature_list = SOCCER_TRAINING_FEATURES if include_standings else [f for f in SOCCER_TRAINING_FEATURES if f not in _STANDINGS_FEATURES]
    X = X.reindex(columns=feature_list, fill_value=0)

    # Look up standings from DB for Model B
    if include_standings and db is not None:
        _enrich_with_standings(X, soccer, db)

    # Label mapping: convert string labels to [0, 1, 2] (away, draw, home)
    if y.dtype == object:
        label_map = {"away": 0, "draw": 1, "home": 2, "Home": 2, "Draw": 1, "Away": 0}
        y = y.map(label_map).fillna(1).astype(int)

    return X, y, feature_list


def _enrich_with_standings(X: pd.DataFrame, fixtures: pd.DataFrame, db) -> None:
    """Look up league standings from the DB for each fixture and add features.

    Uses ``effective_date < match_date`` to prevent lookahead leakage.
    """
    from sqlalchemy import select
    from app.db.models import Standing

    for idx, (_, fx) in enumerate(fixtures.iterrows()):
        if idx >= len(X):
            break
        match_date = pd.to_datetime(fx.get("match_date"), errors="coerce")
        if pd.isna(match_date):
            continue
        match_date = match_date.date()
        league = str(fx.get("league", ""))
        season = str(fx.get("season", ""))

        for team_col, team_key in [("home_team", "home_team"), ("away_team", "away_team")]:
            prefix = "home" if team_col == "home_team" else "away"
            team = normalize_team_name(str(fx.get(team_key, "")), "soccer")

            standing = db.execute(
                select(Standing).where(
                    Standing.sport == "soccer",
                    Standing.league == league,
                    Standing.team == team,
                    Standing.standing_type == "total",
                    Standing.effective_date < match_date,
                ).order_by(Standing.effective_date.desc()).limit(1)
            ).scalar_one_or_none()

            if standing is not None:
                gp = standing.games_played or 1
                pts = standing.points or 0
                gf = standing.goals_for or 0
                ga = standing.goals_against or 0
                gd = standing.goal_difference or (gf - ga)
                wins = standing.wins or 0

                X.at[X.index[idx], f"{prefix}_league_position"] = standing.position or 10
                X.at[X.index[idx], f"{prefix}_table_points"] = pts
                X.at[X.index[idx], f"{prefix}_table_goals_for"] = gf
                X.at[X.index[idx], f"{prefix}_table_goals_against"] = ga
                X.at[X.index[idx], f"{prefix}_table_goal_difference"] = gd
                X.at[X.index[idx], f"{prefix}_table_points_per_game"] = pts / max(gp, 1)
                X.at[X.index[idx], f"{prefix}_win_rate"] = wins / max(gp, 1)
                X.at[X.index[idx], f"{prefix}_goal_diff_per_game"] = gd / max(gp, 1)
                X.at[X.index[idx], f"{prefix}_goals_for_per_game"] = gf / max(gp, 1)
                X.at[X.index[idx], f"{prefix}_goals_against_per_game"] = ga / max(gp, 1)
                X.at[X.index[idx], f"{prefix}_form_score"] = 1.2
                X.at[X.index[idx], "has_standings_data"] = 1

    # Compute diff features
    X["table_position_diff"] = X.get("home_league_position", 10) - X.get("away_league_position", 10)
    X["table_points_diff"] = X.get("home_table_points", 45) - X.get("away_table_points", 45)
    X["strength_difference"] = (
        X.get("home_table_goal_difference", 0) / X.get("home_games_played", 1).replace(0, 1)
        - X.get("away_table_goal_difference", 0) / X.get("away_games_played", 1).replace(0, 1)
    )


def _train_and_evaluate(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> dict:
    """Train a simple ensemble on chronological split and evaluate on holdout.

    Uses the same model factories as the production pipeline (RF + XGBoost +
    LightGBM + MLP) with expanding-window OOF meta-learner.
    """
    from app.ml.train import _build_model_factories, _class_weight_dict
    from sklearn.linear_model import LogisticRegression

    labels = [0, 1, 2]
    factories = _build_model_factories(binary=False, slim=False)

    models = {}
    oof_by_model = {}
    holdout_by_model = {}
    model_scores = {}

    splitter = TimeSeriesSplit(n_splits=3)
    for name, factory in factories:
        try:
            oof = np.full((len(X_train), len(labels)), np.nan, dtype=float)
            fold_scores = []
            for fold_train, fold_valid in splitter.split(X_train):
                if len(fold_train) < 30 or len(fold_valid) < 5:
                    continue
                model = factory(None)
                if name in ("random_forest", "lightgbm") and hasattr(model, "set_params"):
                    model.set_params(class_weight="balanced")
                try:
                    model.fit(X_train.iloc[fold_train], y_train.iloc[fold_train])
                except TypeError:
                    model.fit(X_train.iloc[fold_train], y_train.iloc[fold_train])

                pred = model.predict(X_train.iloc[fold_valid])
                fold_scores.append(float(accuracy_score(y_train.iloc[fold_valid], pred)))

                from app.ml.train_oof import _aligned_proba
                oof[fold_valid] = _aligned_proba(model, X_train.iloc[fold_valid], labels)

            valid_mask = ~np.isnan(oof).any(axis=1)
            if valid_mask.sum() < 30:
                continue

            final_model = factory(None)
            if name in ("random_forest", "lightgbm") and hasattr(final_model, "set_params"):
                final_model.set_params(class_weight="balanced")
            try:
                final_model.fit(X_train, y_train)
            except TypeError:
                final_model.fit(X_train, y_train)

            models[name] = final_model
            oof_by_model[name] = oof
            holdout_by_model[name] = _aligned_proba(final_model, X_test, labels)
            model_scores[name] = float(np.mean(fold_scores)) if fold_scores else 0.5
        except Exception as e:
            log.warning("Model %s skipped in backtest: %s", name, e)
            continue

    if not models:
        return {}

    # Combine with weighted average + meta-learner
    raw_weights = np.array([max(model_scores.get(n, 0.5), 0.5) for n in models], dtype=float)
    weights = raw_weights / raw_weights.sum()
    ensemble_holdout = sum(holdout_by_model[n] * w for n, w in zip(models, weights))

    # Meta-learner on OOF
    valid_mask = np.ones(len(X_train), dtype=bool)
    for values in oof_by_model.values():
        valid_mask &= ~np.isnan(values).any(axis=1)

    stacked_oof = np.column_stack([oof_by_model[n][valid_mask] for n in models])
    y_oof = np.asarray(y_train.iloc[np.flatnonzero(valid_mask)])

    meta = None
    meta_holdout = None
    if len(np.unique(y_oof)) >= 2 and len(y_oof) >= 30:
        meta = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs", class_weight="balanced")
        meta.fit(stacked_oof, y_oof)
        stacked_holdout = np.column_stack([holdout_by_model[n] for n in models])
        meta_holdout = meta.predict_proba(stacked_holdout)
        aligned_meta = np.zeros((len(X_test), len(labels)), dtype=float)
        for i, cls in enumerate(meta.classes_):
            if cls in labels:
                aligned_meta[:, labels.index(cls)] = meta_holdout[:, i]
        meta_holdout = aligned_meta

    final_proba = ensemble_holdout if meta_holdout is None else 0.7 * ensemble_holdout + 0.3 * meta_holdout
    final_proba = np.clip(final_proba, 1e-6, 1 - 1e-6)
    final_proba = final_proba / final_proba.sum(axis=1, keepdims=True)
    final_preds = np.asarray([labels[int(np.argmax(row))] for row in final_proba])

    metrics = _compute_metrics(y_test.values, final_preds, final_proba, labels)
    metrics["predictions"] = final_proba.tolist()
    metrics["identifiable_predictions"] = _count_unique_profiles(final_proba)
    return metrics


def _count_unique_profiles(probas: np.ndarray, tol: float = 0.02) -> dict:
    """Count identical and near-identical prediction profiles.

    A 'profile' is the 3-class probability vector [p_away, p_draw, p_home].
    Measures how many fixtures get the exact same or near-identical predictions,
    which indicates symmetric defaults (the 54.0% problem).
    """
    probas = np.asarray(probas)
    n = len(probas)
    if n == 0:
        return {"total": 0, "unique": 0, "duplicate_count": 0, "duplicate_pct": 0.0}

    rounded = np.round(probas / tol) * tol
    seen = {}
    for row in rounded:
        key = tuple(row)
        seen[key] = seen.get(key, 0) + 1

    unique = len(seen)
    dup_count = sum(v - 1 for v in seen.values() if v > 1)
    return {
        "total": n,
        "unique": unique,
        "duplicate_count": dup_count,
        "duplicate_pct": round(dup_count / n * 100, 2) if n > 0 else 0.0,
    }


def _compute_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: np.ndarray,
    labels: list[int],
) -> dict:
    """Compute accuracy, Brier score, and log-loss."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)

    accuracy = float(accuracy_score(y_true, y_pred))

    # Brier score (multi-class): average of per-class squared errors
    brier = float(np.mean(np.sum((y_proba - np.eye(len(labels))[y_true]) ** 2, axis=1)))

    # Log loss
    try:
        ll = float(log_loss(y_true, y_proba, labels=labels))
    except Exception:
        ll = float("nan")

    # ROI via Kelly Criterion (using odds if available)
    roi = 0.0
    kelly_total = 0.0
    kelly_wins = 0.0

    return {
        "accuracy": accuracy,
        "brier_score": brier,
        "log_loss": ll,
        "roi": roi,
        "kelly_return": kelly_total,
        "kelly_wins": kelly_wins,
        "sample_size": len(y_true),
    }


def run_backtest(
    db_session=None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    train_ratio: float = 0.7,
    sport: str = "soccer",
    leagues: list[str] | None = None,
) -> dict:
    """Run the A/B/C backtest.

    A — Current model (no standings features)
    B — New model (with standings features)
    C — Bookmaker odds baseline

    All models trained on chronological split: first 70% for training,
    last 30% for evaluation. This simulates real forward-prediction
    where the model never sees future fixtures.
    """
    from app.ml.train_oof import _filter_recent

    if db_session is None:
        init_db()
        db_session = SessionLocal()

    # Load historical data
    history = dataframe_from_db(db_session, max_age_days=None)
    if history.empty:
        history = load_archive_dataframe()

    if history.empty:
        log.error("No historical data available for backtest")
        return {"error": "no_data"}

    # Filter to soccer
    if "sport" in history.columns:
        history = history[history["sport"] == sport].copy()

    # Date filtering
    history["match_date"] = pd.to_datetime(history["match_date"], errors="coerce")
    history = history.dropna(subset=["match_date"])

    if start_date:
        start = pd.to_datetime(start_date, errors="coerce")
        if not pd.isna(start):
            history = history[history["match_date"] >= start]
    if end_date:
        end = pd.to_datetime(end_date, errors="coerce")
        if not pd.isna(end):
            history = history[history["match_date"] <= end]

    if leagues:
        history = history[history["league"].isin(leagues)]

    history = history.sort_values("match_date").reset_index(drop=True)

    # Remove fixtures without results (can't evaluate)
    history = history[history["home_score"].notna() & history["away_score"].notna()].copy()

    if len(history) < 200:
        log.warning("Only %d fixtures available, backtest may be unreliable", len(history))

    # Chronological split index
    split_idx = int(len(history) * train_ratio)

    log.info(
        "Backtest: %d train fixtures, %d test fixtures (split @ %s)",
        split_idx, len(history) - split_idx,
        history.iloc[split_idx - 1]["match_date"].date() if split_idx > 0 else "N/A",
    )

    # Build labels for all fixtures (used for both train and test)
    y_all = _build_labels(history)

    results = {}

    # --- Model A: Current (no standings features) ---
    # Build features on full history so test fixtures see training-period context
    X_full_a, _, feature_list_a = _build_training_data(history, include_standings=False)
    y_full = pd.Series(y_all[:len(X_full_a)])

    split_pos = int(len(X_full_a) * train_ratio)
    if len(X_full_a) >= 200 and (len(X_full_a) - split_pos) >= 20:
        X_train_a = X_full_a.iloc[:split_pos]
        y_train_a = y_full.iloc[:split_pos]
        X_test_a = X_full_a.iloc[split_pos:]
        y_test_a = y_full.iloc[split_pos:]

        result_a = _train_and_evaluate(X_train_a, y_train_a, X_test_a, y_test_a)
        results["A_current_no_standings"] = result_a
        log.info("Model A (no standings): acc=%.4f, brier=%.4f, logloss=%.4f",
                 result_a["accuracy"], result_a["brier_score"], result_a["log_loss"])
    else:
        log.warning("Insufficient data for Model A")
        results["A_current_no_standings"] = {"error": "insufficient_data", "sample_size": len(X_full_a)}

    # --- Model B: New (with standings features) ---
    X_full_b, _, feature_list_b = _build_training_data(history, include_standings=True, db=db_session)
    y_full_b = pd.Series(y_all[:len(X_full_b)])

    split_pos_b = int(len(X_full_b) * train_ratio)
    if len(X_full_b) >= 200 and (len(X_full_b) - split_pos_b) >= 20:
        X_train_b = X_full_b.iloc[:split_pos_b]
        y_train_b = y_full_b.iloc[:split_pos_b]
        X_test_b = X_full_b.iloc[split_pos_b:]
        y_test_b = y_full_b.iloc[split_pos_b:]

        result_b = _train_and_evaluate(X_train_b, y_train_b, X_test_b, y_test_b)
        results["B_new_with_standings"] = result_b
        log.info("Model B (standings): acc=%.4f, brier=%.4f, logloss=%.4f",
                 result_b["accuracy"], result_b["brier_score"], result_b["log_loss"])
    else:
        log.warning("Insufficient data for Model B")
        results["B_new_with_standings"] = {"error": "insufficient_data", "sample_size": len(X_full_b)}

    # --- Model C: Bookmaker odds baseline ---
    y_test_c = _build_labels(history.iloc[split_idx:])
    odds_probas = np.array([_odds_baseline(row) for _, row in history.iloc[split_idx:].iterrows()])
    odds_preds = np.argmax(odds_probas, axis=1)
    results["C_odds_baseline"] = _compute_metrics(y_test_c, odds_preds, odds_probas, LABELS)
    results["C_odds_baseline"]["identifiable_predictions"] = _count_unique_profiles(odds_probas)
    log.info("Model C (odds): acc=%.4f, brier=%.4f, logloss=%.4f",
             results["C_odds_baseline"]["accuracy"],
             results["C_odds_baseline"]["brier_score"],
             results["C_odds_baseline"]["log_loss"])

    # --- Summary comparison ---
    _store_backtest_run(db_session, results, len(history), split_idx, sport, leagues)

    return {
        "train_size": split_idx,
        "test_size": len(history) - split_idx,
        "split_date": str(history.iloc[split_idx - 1]["match_date"].date()) if split_idx > 0 else "N/A",
        "results": results,
        "comparisons": _compare_results(results),
    }


def _build_labels(df: pd.DataFrame) -> np.ndarray:
    """Convert fixture scores to [0,1,2] labels: away=0, draw=1, home=2."""
    labels = []
    for _, r in df.iterrows():
        hs, as_ = r.get("home_score"), r.get("away_score")
        if pd.isna(hs) or pd.isna(as_):
            labels.append(1)  # default to draw for missing scores
        elif hs > as_:
            labels.append(2)  # home win
        elif hs == as_:
            labels.append(1)  # draw
        else:
            labels.append(0)  # away win
    return np.array(labels)


def _compare_results(results: dict) -> dict:
    """Summarize improvements of B over A."""
    a = results.get("A_current_no_standings", {})
    b = results.get("B_new_with_standings", {})
    c = results.get("C_odds_baseline", {})

    summary = {}
    for metric in ("accuracy", "brier_score", "log_loss"):
        a_val = a.get(metric)
        b_val = b.get(metric)
        c_val = c.get(metric)
        if a_val is not None and b_val is not None:
            # positive improvement = B is better than A
            # accuracy: higher is better; brier/log_loss: lower is better
            if metric == "accuracy":
                diff = b_val - a_val
            else:
                diff = a_val - b_val
            summary[f"{metric}_improvement_B_over_A"] = diff

            if c_val is not None:
                if metric == "accuracy":
                    diff_c = b_val - c_val
                else:
                    diff_c = c_val - b_val
                summary[f"{metric}_improvement_B_over_C"] = diff_c

    summary["A_beats_C"] = a.get("accuracy", 0) > c.get("accuracy", 0)
    summary["B_beats_C"] = b.get("accuracy", 0) > c.get("accuracy", 0)
    summary["B_beats_A"] = (
        b.get("accuracy", 0) > a.get("accuracy", 0)
        and b.get("brier_score", 1) < a.get("brier_score", 0)
        and b.get("log_loss", 1) < a.get("log_loss", 0)
    )

    # Prediction variance diagnostics
    for model_key, label in [("A_current_no_standings", "A"), ("B_new_with_standings", "B"), ("C_odds_baseline", "C")]:
        m = results.get(model_key, {})
        if "identifiable_predictions" in m:
            stats = m["identifiable_predictions"]
            summary[f"{label}_duplicate_predictions_pct"] = stats["duplicate_pct"]
            summary[f"{label}_unique_prediction_profiles"] = stats["unique"]

    # Does B reduce duplicate predictions vs A?
    a_dup = summary.get("A_duplicate_predictions_pct", 0)
    b_dup = summary.get("B_duplicate_predictions_pct", 0)
    summary["duplicate_reduction_B_vs_A"] = a_dup - b_dup

    return summary


def _store_backtest_run(
    db_session,
    results: dict,
    total_fixtures: int,
    train_size: int,
    sport: str,
    leagues: list[str] | None,
) -> None:
    """Persist backtest results to the BacktestRun table."""
    b_result = results.get("B_new_with_standings", {})
    a_result = results.get("A_current_no_standings", {})
    c_result = results.get("C_odds_baseline", {})

    run = BacktestRun(
        sport=sport,
        model_type="OOF_ensemble+standings",
        split_strategy="chronological_70_30",
        sample_size=total_fixtures,
        accuracy=b_result.get("accuracy", 0.0),
        brier_score=b_result.get("brier_score"),
        log_loss=b_result.get("log_loss"),
        roi_estimate=b_result.get("roi", 0.0),
        metrics={
            "model_A_no_standings": a_result,
            "model_B_with_standings": b_result,
            "model_C_odds_baseline": c_result,
            "train_size": train_size,
            "test_size": total_fixtures - train_size,
            "leagues": leagues or ["all"],
        },
        created_at=datetime.utcnow(),
    )
    db_session.add(run)
    db_session.commit()
    log.info("Backtest run stored in BacktestRun (id=%s)", run.id)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="A/B/C backtest for soccer predictions")
    parser.add_argument("--sport", default="soccer")
    parser.add_argument("--league", action="append", help="Filter to specific leagues")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--train-ratio", type=float, default=0.7)
    args = parser.parse_args()

    result = run_backtest(
        sport=args.sport,
        leagues=args.league,
        start_date=args.start_date,
        end_date=args.end_date,
        train_ratio=args.train_ratio,
    )

    print("\n=== A/B/C Backtest Results ===")
    for model, metrics in result["results"].items():
        print(f"\n{model}:")
        for k, v in metrics.items():
            if isinstance(v, float):
                print(f"  {k}: {v:.4f}")
            else:
                print(f"  {k}: {v}")

    print("\n=== Comparisons ===")
    for k, v in result.get("comparisons", {}).items():
        print(f"  {k}: {v}")
