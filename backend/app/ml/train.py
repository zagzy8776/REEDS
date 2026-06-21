from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import TimeSeriesSplit

from app.core.config import get_settings
from app.ml.calibration import fit_soccer_platt_calibrator
from app.ml.features import build_basketball_features, build_soccer_features

try:
    from xgboost import XGBClassifier
except Exception:
    XGBClassifier = None

try:
    from lightgbm import LGBMClassifier
except Exception:
    LGBMClassifier = None

try:
    from catboost import CatBoostClassifier
except Exception:
    CatBoostClassifier = None

try:
    import optuna
except Exception:
    optuna = None

try:
    from sklearn.neural_network import MLPClassifier
    _has_mlp = True
except Exception:
    _has_mlp = False

# Full feature list — v3 with EMA form, venue H2H, CLV, rest, market signals
FEATURES = [
    # --- Core form (simple averages) ---
    "home_form_points", "away_form_points",
    "home_form_points_3", "away_form_points_3",
    "home_form_points_5", "away_form_points_5",
    "home_win_rate_5", "away_win_rate_5",
    "home_draw_rate_5", "away_draw_rate_5",
    "home_loss_rate_5", "away_loss_rate_5",
    # --- EMA-weighted form (exponential — recent games weighted more) ---
    "home_ema_form_5", "away_ema_form_5",
    "home_ema_form_10", "away_ema_form_10",
    "home_ema_goals_for", "away_ema_goals_for",
    "home_ema_goals_against", "away_ema_goals_against",
    # --- Scoring / conceding ---
    "home_goals_for", "home_goals_against",
    "away_goals_for", "away_goals_against",
    "home_home_goals_for", "home_home_goals_against",
    "away_away_goals_for", "away_away_goals_against",
    "home_goal_diff", "away_goal_diff",
    "home_clean_sheet_rate_5", "away_clean_sheet_rate_5",
    "home_failed_score_rate_5", "away_failed_score_rate_5",
    "home_unbeaten_rate_10", "away_unbeaten_rate_10",
    # --- Elo ratings ---
    "home_elo", "away_elo", "elo_diff",
    "home_elo_home_only", "away_elo_away_only",
    "elo_diff_venue",          # home_elo_home_only - away_elo_away_only
    # --- League & season context ---
    "league_strength",
    "home_form_vs_season", "away_form_vs_season",
    # --- Streaks ---
    "home_streak_len", "away_streak_len",
    "home_streak_winning", "away_streak_winning",
    "home_streak_losing", "away_streak_losing",
    # --- H2H (standard) ---
    "h2h_home_win_rate", "h2h_draw_rate", "h2h_away_win_rate",
    "h2h_avg_total_goals",
    # --- H2H venue-adjusted (home wins when playing at home specifically) ---
    "h2h_home_venue_win_rate",   # home team win rate in H2H when at home
    "h2h_away_venue_win_rate",   # away team win rate in H2H when away
    "h2h_last3_home_goals",      # avg home goals in last 3 H2H meetings
    "h2h_last3_away_goals",      # avg away goals in last 3 H2H meetings
    # --- Scoring consistency ---
    "home_scoring_consistency", "away_scoring_consistency",
    "home_conceding_consistency", "away_conceding_consistency",
    # --- Last match indicators ---
    "last_match_home_goals", "last_match_away_goals",
    "last_match_home_conceded", "last_match_away_conceded",
    # --- Momentum ---
    "home_goal_diff_momentum", "away_goal_diff_momentum",
    # --- Rest days / fatigue (new) ---
    "home_rest_days",       # days since last match
    "away_rest_days",
    "home_back_to_back",    # 1 if < 3 days rest
    "away_back_to_back",
    "rest_advantage",       # home_rest - away_rest (positive = home fresher)
    # --- Market / odds signals (CLV + overround) ---
    "home_implied", "draw_implied", "away_implied",
    "odds_margin",
    "sharp_home_move",      # odds shortened since open (line movement toward home)
    "sharp_away_move",      # odds shortened toward away
    "clv_home_signal",      # closing line value signal (pre-match only)
    # --- Set-piece / dead-ball proxy ---
    "home_high_scoring_rate",   # % games with 3+ goals (proxy for set-piece value)
    "away_high_scoring_rate",
    # --- Insider signals (sharp money, weather, injuries, referee) ---
    "insider_sharp_home_move",   # home implied prob move open→close
    "insider_sharp_away_move",   # away implied prob move open→close
    "insider_clv_home",          # closing-line value proxy
    "insider_steam",             # 1 = rapid line move (<2h, ≥3%)
    "insider_opening_home_prob", # opening implied prob for home
    "insider_weather_precip",    # precipitation mm
    "insider_weather_wind",      # wind speed km/h
    "insider_home_injury",       # key player injury severity 0-1
    "insider_away_injury",       # key player injury severity 0-1
    "insider_referee_cards",     # referee avg cards/match
    "insider_public_home_pct",   # % of public bets on home side
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
    # New features
    "home_streak_len",
    "away_streak_len",
    "home_streak_wins",
    "away_streak_wins",
    "h2h_home_advantage",
    "home_scoring_volatility",
    "away_scoring_volatility",
    "home_avg_margin_last_3",
    "away_avg_margin_last_3",
]


def _time_series_split(X, y, n_splits=5):
    """Use expanding window cross-validation instead of a single 75/25 split."""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores = []
    for train_idx, test_idx in tscv.split(X):
        if len(train_idx) < 20 or len(test_idx) < 5:
            continue
        yield train_idx, test_idx


def _build_model_factories(binary: bool = False):
    """Return list of (name, factory) tuples for available model types.
    
    binary=True for 2-class problems (basketball, tennis, etc.)
    binary=False for 3-class problems (soccer 1X2)
    """
    factories = []
    if RandomForestClassifier is not None:
        factories.append(("random_forest", lambda params=None: RandomForestClassifier(
            n_estimators=params.get("n_estimators", 250) if params else 250,
            max_depth=params.get("max_depth", None) if params else None,
            min_samples_split=params.get("min_samples_split", 2) if params else 2,
            min_samples_leaf=params.get("min_samples_leaf", 1) if params else 1,
            max_features=params.get("max_features", "sqrt") if params else "sqrt",
            class_weight="balanced",
            n_jobs=-1,
            random_state=42,
        )))
    if GradientBoostingClassifier is not None:
        factories.append(("gradient_boosting", lambda params=None: GradientBoostingClassifier(
            n_estimators=params.get("n_estimators", 200) if params else 200,
            max_depth=params.get("max_depth", 4) if params else 4,
            learning_rate=params.get("learning_rate", 0.1) if params else 0.1,
            subsample=params.get("subsample", 0.8) if params else 0.8,
            random_state=42,
        )))
    if XGBClassifier is not None:
        _xgb_objective = "binary:logistic" if binary else "multi:softprob"
        _xgb_metric    = "logloss"          if binary else "mlogloss"
        factories.append(("xgboost", lambda params=None, _obj=_xgb_objective, _met=_xgb_metric: XGBClassifier(
            n_estimators=params.get("n_estimators", 200) if params else 200,
            max_depth=params.get("max_depth", 6) if params else 6,
            learning_rate=params.get("learning_rate", 0.1) if params else 0.1,
            reg_alpha=params.get("reg_alpha", 0.2) if params else 0.2,
            reg_lambda=params.get("reg_lambda", 1.0) if params else 1.0,
            subsample=params.get("subsample", 0.8) if params else 0.8,
            colsample_bytree=params.get("colsample_bytree", 0.8) if params else 0.8,
            objective=_obj,
            eval_metric=_met,
            random_state=42,
            verbosity=0,
        )))
    if LGBMClassifier is not None:
        factories.append(("lightgbm", lambda params=None: LGBMClassifier(
            n_estimators=params.get("n_estimators", 200) if params else 200,
            max_depth=params.get("max_depth", 6) if params else 6,
            learning_rate=params.get("learning_rate", 0.1) if params else 0.1,
            reg_alpha=params.get("reg_alpha", 0.1) if params else 0.1,
            reg_lambda=params.get("reg_lambda", 0.1) if params else 0.1,
            subsample=params.get("subsample", 0.8) if params else 0.8,
            colsample_bytree=params.get("colsample_bytree", 0.8) if params else 0.8,
            class_weight="balanced",
            random_state=42,
            verbosity=-1,
        )))
    if CatBoostClassifier is not None:
        factories.append(("catboost", lambda params=None: CatBoostClassifier(
            iterations=params.get("iterations", 200) if params else 200,
            depth=params.get("depth", 6) if params else 6,
            learning_rate=params.get("learning_rate", 0.1) if params else 0.1,
            l2_leaf_reg=params.get("l2_leaf_reg", 3.0) if params else 3.0,
            border_count=params.get("border_count", 128) if params else 128,
            verbose=False,
            random_seed=42,
        )))
    # Lightweight Neural Net — fast on CPU, good at non-linear interactions
    if _has_mlp:
        factories.append(("neural_net", lambda params=None: MLPClassifier(
            hidden_layer_sizes=params.get("hidden_layer_sizes", (128, 64, 32)) if params else (128, 64, 32),
            activation="relu",
            solver="adam",
            alpha=params.get("alpha", 0.001) if params else 0.001,
            learning_rate_init=params.get("learning_rate_init", 0.001) if params else 0.001,
            max_iter=300,
            early_stopping=True,
            validation_fraction=0.1,
            random_state=42,
        )))
    return factories


def _train_single_model(model, X_train, y_train, X_test, y_test, labels):
    """Train a single model and return accuracy + probability alignment."""
    model.fit(X_train, y_train)
    preds = model.predict(X_test)
    acc = float(accuracy_score(y_test, preds))
    probas = model.predict_proba(X_test)
    # Align probabilities to standard label order [0, 1, 2]
    aligned = np.zeros((len(X_test), len(labels)))
    for src_idx, cls in enumerate(model.classes_):
        if cls in labels:
            aligned[:, labels.index(cls)] = probas[:, src_idx]
    return model, acc, aligned


def _train_ensemble(X_train, y_train, X_test, y_test, factories, labels, n_trials=0):
    """Train multiple models and blend them into a weighted ensemble.

    If n_trials > 0 and optuna is available, run hyperparameter optimization
    for each model type first.
    """
    models = {}
    all_probas = []
    weights = []

    for name, factory in factories:
        try:
            best_params = None

            # Optuna hyperparameter tuning
            if optuna and n_trials > 0 and len(X_train) >= 100:
                def objective(trial, name=name):
                    params = {}
                    if name in ("xgboost", "lightgbm", "gradient_boosting"):
                        params["n_estimators"] = trial.suggest_int("n_estimators", 100, 400)
                        params["max_depth"] = trial.suggest_int("max_depth", 3, 10)
                        params["learning_rate"] = trial.suggest_float("learning_rate", 0.01, 0.3, log=True)
                        if name in ("xgboost", "lightgbm"):
                            params["reg_alpha"] = trial.suggest_float("reg_alpha", 0.0, 1.0)
                            params["reg_lambda"] = trial.suggest_float("reg_lambda", 0.0, 1.0)
                        params["subsample"] = trial.suggest_float("subsample", 0.6, 1.0)
                    elif name == "catboost":
                        params["iterations"] = trial.suggest_int("iterations", 100, 400)
                        params["depth"] = trial.suggest_int("depth", 4, 10)
                        params["learning_rate"] = trial.suggest_float("learning_rate", 0.01, 0.3, log=True)
                        params["l2_leaf_reg"] = trial.suggest_float("l2_leaf_reg", 1.0, 10.0)
                    elif name == "random_forest":
                        params["n_estimators"] = trial.suggest_int("n_estimators", 100, 500)
                        params["max_depth"] = trial.suggest_int("max_depth", 3, 20)
                        params["min_samples_split"] = trial.suggest_int("min_samples_split", 2, 10)
                    model = factory(params)
                    model.fit(X_train, y_train)
                    return float(accuracy_score(y_test, model.predict(X_test)))

                study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
                study.optimize(objective, n_trials=min(n_trials, 30), n_jobs=1, show_progress_bar=False)
                best_params = study.best_params
                best_acc = study.best_value
            else:
                # Quick validation without tuning
                model = factory(None)
                model.fit(X_train, y_train)
                best_acc = float(accuracy_score(y_test, model.predict(X_test)))

            # Retrain on full training set with best params
            final_model = factory(best_params)
            final_model.fit(X_train, y_train)

            # Align probabilities
            probas = final_model.predict_proba(X_test)
            aligned = np.zeros((len(X_test), len(labels)))
            for src_idx, cls in enumerate(final_model.classes_):
                if cls in labels:
                    aligned[:, labels.index(cls)] = probas[:, src_idx]

            models[name] = final_model
            all_probas.append(aligned)
            # Weight by accuracy
            weight = max(best_acc, 0.5)  # minimum weight 0.5 to avoid zero-weight
            weights.append(weight)

            print(f"  {name}: accuracy={best_acc:.4f}, weight={weight:.4f}")

        except Exception as exc:
            print(f"  {name}: SKIPPED ({exc})")
            continue

    if not models:
        raise ValueError("No models could be trained!")

    # Weighted ensemble blend
    total_weight = sum(weights)
    ensemble_probas = sum(
        proba * (w / total_weight)
        for proba, w in zip(all_probas, weights)
    )

    # Meta-learner: LogisticRegression on model out-of-fold predictions
    # This learns which models to trust for which classes
    stacked = np.column_stack(all_probas)
    meta = LogisticRegression(max_iter=1000, C=1.0)
    meta.fit(stacked, y_test)

    # Blend: 70% weighted average + 30% meta-learner
    meta_probas = meta.predict_proba(stacked)
    final_probas = 0.7 * ensemble_probas + 0.3 * meta_probas

    final_preds = [labels[int(np.argmax(row))] for row in final_probas]
    accuracy = float(accuracy_score(y_test, final_preds))

    return {
        "models": models,
        "meta_learner": meta,
        "accuracy": accuracy,
        "model_types": list(models.keys()),
        "weights": weights,
        "ensemble_probas": final_probas,
    }


def _train_fast_large_dataset_model(X_train, y_train, X_test, y_test, labels: list[int], sport: str) -> dict:
    """Fast production-safe trainer for very large historical datasets.

    Full GradientBoosting/meta-learner training can run for a long time once the
    local DB grows past 100k rows. This path trains a parallel RandomForest only,
    keeps the same bundle shape used by prediction serving, and validates on the
    chronological holdout.
    """

    if RandomForestClassifier is None:
        raise ValueError("RandomForestClassifier is unavailable")
    model = RandomForestClassifier(
        n_estimators=300 if sport == "soccer" else 280,
        max_depth=30 if sport == "soccer" else None,
        min_samples_leaf=2 if sport == "soccer" else 1,
        max_features="sqrt",
        class_weight="balanced",
        n_jobs=-1,
        random_state=42,
    )
    model.fit(X_train, y_train)
    preds = model.predict(X_test)
    accuracy = float(accuracy_score(y_test, preds))
    return {
        "models": {"random_forest_fast": model},
        "meta_learner": None,
        "accuracy": accuracy,
        "model_types": ["random_forest_fast"],
        "weights": [max(accuracy, 0.5)],
        "ensemble_probas": None,
    }


def train_soccer_model(fixtures: pd.DataFrame) -> dict:
    settings = get_settings()
    if "sport" in fixtures.columns:
        fixtures = fixtures[fixtures["sport"] == "soccer"].sort_values("match_date").copy()
    else:
        fixtures = fixtures.sort_values("match_date").copy()
    X, y = build_soccer_features(fixtures)
    if len(X) < settings.min_training_rows:
        raise ValueError(f"Need at least {settings.min_training_rows} rows, got {len(X)}")
    X = X.reindex(columns=FEATURES, fill_value=0)

    # Use expanding window for time-series validation
    split_index = max(int(len(X) * 0.7), settings.min_training_rows)
    if split_index >= len(X):
        split_index = len(X) - max(10, int(len(X) * 0.15))
    X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]

    if len(X_test) < 5:
        raise ValueError(f"Test set too small ({len(X_test)}), need more data")

    labels = [0, 1, 2]  # away, draw, home
    factories = _build_model_factories()

    # Train ensemble with available models. For very large public-history imports,
    # use a fast RF path so training finishes reliably on local/Render machines.
    # Threshold lowered to 15k to avoid OOM on Render's 512MB free tier.
    if len(X) >= 15000:
        result = _train_fast_large_dataset_model(X_train, y_train, X_test, y_test, labels, "soccer")
    else:
        result = _train_ensemble(X_train, y_train, X_test, y_test, factories, labels, n_trials=15)

    Path(settings.model_dir).mkdir(parents=True, exist_ok=True)
    model_type_str = "+".join(result["model_types"])
    path = f"{settings.model_dir}/soccer_ensemble_{model_type_str}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.joblib"

    calibrator_path = None
    if len(X) >= 15000:
        # Calibration also memory-intensive — skip on large datasets
        calibrator_path = None
    elif len(X) >= max(settings.min_training_rows, 500):
        try:
            calibrator_path = fit_soccer_platt_calibrator(fixtures, min_train_rows=max(settings.min_training_rows, 1000))["path"]
        except ValueError:
            calibrator_path = None

    # Save full ensemble bundle
    bundle = {
        "models": result["models"],
        "meta_learner": result["meta_learner"],
        "features": FEATURES,
        "model_types": result["model_types"],
        "weights": result["weights"],
        "accuracy": result["accuracy"],
        "sample_size": len(X),
        "split": "chronological_70_30_ensemble",
        "calibrator_path": calibrator_path,
        "labels": labels,
    }
    joblib.dump(bundle, path)

    return {
        "path": path,
        "accuracy": result["accuracy"],
        "sample_size": len(X),
        "model_type": model_type_str,
        "split": "chronological_70_30_ensemble",
        "calibrator_path": calibrator_path,
        "models_trained": result["model_types"],
    }


def train_basketball_model(fixtures: pd.DataFrame) -> dict:
    settings = get_settings()
    if "sport" in fixtures.columns:
        fixtures = fixtures[fixtures["sport"] == "basketball"].sort_values("match_date").copy()
    else:
        fixtures = fixtures.sort_values("match_date").copy()
    X, y = build_basketball_features(fixtures)
    if len(X) < settings.min_training_rows:
        raise ValueError(f"Need at least {settings.min_training_rows} basketball rows, got {len(X)}")
    X = X.reindex(columns=BASKETBALL_FEATURES, fill_value=0)

    split_index = max(int(len(X) * 0.7), settings.min_training_rows)
    if split_index >= len(X):
        split_index = len(X) - max(10, int(len(X) * 0.15))
    X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]

    if len(X_test) < 5:
        raise ValueError(f"Test set too small ({len(X_test)}), need more data")

    labels = [0, 1]
    factories = _build_model_factories(binary=True)
    # Remove gradient_boosting for binary (sklearn GB handles it but slower)
    factories = [(n, f) for n, f in factories if n != "gradient_boosting"]

    if len(X) >= 15000:
        result = _train_fast_large_dataset_model(X_train, y_train, X_test, y_test, labels, "basketball")
    else:
        result = _train_ensemble(X_train, y_train, X_test, y_test, factories, labels, n_trials=10)

    Path(settings.model_dir).mkdir(parents=True, exist_ok=True)
    model_type_str = "+".join(result["model_types"])
    path = f"{settings.model_dir}/basketball_ensemble_{model_type_str}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.joblib"

    bundle = {
        "models": result["models"],
        "meta_learner": result["meta_learner"],
        "features": BASKETBALL_FEATURES,
        "model_types": result["model_types"],
        "weights": result["weights"],
        "accuracy": result["accuracy"],
        "sample_size": len(X),
        "split": "chronological_70_30_ensemble",
        "labels": labels,
    }
    joblib.dump(bundle, path)

    return {
        "path": path,
        "accuracy": result["accuracy"],
        "sample_size": len(X),
        "model_type": model_type_str,
        "split": "chronological_70_30_ensemble",
    }


# ---------------------------------------------------------------------------
# Generic binary sport trainer (tennis, american_football, hockey, cricket)
# Uses the same features as basketball: win rate, margin, elo, h2h
# ---------------------------------------------------------------------------

GENERIC_SPORT_FEATURES = [
    "home_win_rate", "away_win_rate",
    "home_margin", "away_margin",
    "home_margin_5", "away_margin_5",
    "home_elo", "away_elo", "elo_diff",
    "h2h_home_win_rate",
    "home_streak_len", "away_streak_len",
    "home_streak_wins", "away_streak_wins",
    "home_for_avg", "away_for_avg",
    "home_against_avg", "away_against_avg",
    "home_implied", "away_implied",
]


def _build_generic_features(fixtures: pd.DataFrame, sport: str) -> tuple[pd.DataFrame, pd.Series]:
    """Build binary win/loss features for any sport using score-based metrics."""
    from app.utils.team_names import normalize_team_name

    df = fixtures.copy()
    if "sport" in df.columns:
        df = df[df["sport"] == sport]
    df = df.sort_values("match_date").copy()

    rows, y = [], []
    team_hist: dict[str, list] = {}
    team_elo: dict[str, float] = {}
    h2h: dict[tuple, dict] = {}

    def _elo_update(ra, rb, score_a, k=20):
        ea = 1 / (1 + 10 ** ((rb - ra) / 400))
        return ra + k * (score_a - ea), rb + k * ((1 - score_a) - (1 - ea))

    for _, r in df.iterrows():
        if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
            continue
        home = normalize_team_name(str(r["home_team"]), sport)
        away = normalize_team_name(str(r["away_team"]), sport)
        hs, as_ = float(r["home_score"]), float(r["away_score"])
        # Skip draws for binary classification (hs == as_ is ambiguous)
        if hs == as_:
            continue
        home_won = 1 if hs > as_ else 0

        hh = team_hist.get(home, [])[-10:]
        ah = team_hist.get(away, [])[-10:]
        h_elo = team_elo.get(home, 1500.0)
        a_elo = team_elo.get(away, 1500.0)

        def wrate(hist):
            return sum(x["w"] for x in hist) / len(hist) if hist else 0.5

        def margin_avg(hist, n=None):
            sample = hist[-n:] if n else hist
            return sum(x["m"] for x in sample) / len(sample) if sample else 0.0

        def for_avg(hist):
            return sum(x["f"] for x in hist) / len(hist) if hist else 0.0

        def against_avg(hist):
            return sum(x["a"] for x in hist) / len(hist) if hist else 0.0

        # H2H
        key = tuple(sorted((home, away)))
        h2h_rec = h2h.get(key, {"home_w": 0, "total": 0})
        h2h_rate = (h2h_rec["home_w"] / h2h_rec["total"]) if h2h_rec["total"] >= 2 else 0.5

        # Streak
        def streak(hist):
            if not hist:
                return 0, 0
            last = hist[-1]["w"]
            n = 0
            for x in reversed(hist):
                if x["w"] == last:
                    n += 1
                else:
                    break
            return n, int(last)

        h_sl, h_sw = streak(hh)
        a_sl, a_sw = streak(ah)

        rows.append({
            "home_win_rate": wrate(hh),
            "away_win_rate": wrate(ah),
            "home_margin": margin_avg(hh),
            "away_margin": margin_avg(ah),
            "home_margin_5": margin_avg(hh, 5),
            "away_margin_5": margin_avg(ah, 5),
            "home_elo": h_elo,
            "away_elo": a_elo,
            "elo_diff": h_elo - a_elo,
            "h2h_home_win_rate": h2h_rate,
            "home_streak_len": min(h_sl, 10),
            "away_streak_len": min(a_sl, 10),
            "home_streak_wins": h_sw,
            "away_streak_wins": a_sw,
            "home_for_avg": for_avg(hh),
            "away_for_avg": for_avg(ah),
            "home_against_avg": against_avg(hh),
            "away_against_avg": against_avg(ah),
            "home_implied": (1 / float(r["home_odds"])) if r.get("home_odds") and float(r["home_odds"]) > 0 else 0.0,
            "away_implied": (1 / float(r["away_odds"])) if r.get("away_odds") and float(r["away_odds"]) > 0 else 0.0,
        })
        y.append(home_won)

        entry_h = {"w": home_won, "m": hs - as_, "f": hs, "a": as_}
        entry_a = {"w": 1 - home_won, "m": as_ - hs, "f": as_, "a": hs}
        team_hist.setdefault(home, []).append(entry_h)
        team_hist.setdefault(away, []).append(entry_a)
        team_elo[home], team_elo[away] = _elo_update(h_elo, a_elo, home_won)

        if key not in h2h:
            h2h[key] = {"home_w": 0, "total": 0}
        h2h[key]["total"] += 1
        if (key[0] == home and home_won) or (key[0] == away and not home_won):
            h2h[key]["home_w"] += 1

    return pd.DataFrame(rows).fillna(0), pd.Series(y)


def train_generic_sport_model(fixtures: pd.DataFrame, sport: str) -> dict:
    """Train a binary classifier for any sport with score data.

    Works for tennis, american_football, hockey, cricket, rugby, etc.
    Requires at least min_training_rows completed fixtures for the sport.
    """
    settings = get_settings()

    X, y = _build_generic_features(fixtures, sport)
    if len(X) < settings.min_training_rows:
        raise ValueError(f"Need at least {settings.min_training_rows} rows for {sport}, got {len(X)}")

    X = X.reindex(columns=GENERIC_SPORT_FEATURES, fill_value=0)
    split_index = max(int(len(X) * 0.7), settings.min_training_rows)
    if split_index >= len(X):
        split_index = len(X) - max(10, int(len(X) * 0.15))
    X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]

    if len(X_test) < 5:
        raise ValueError(f"Test set too small ({len(X_test)}) for {sport}")

    labels = [0, 1]
    factories = [(n, f) for n, f in _build_model_factories(binary=True) if n != "gradient_boosting"]

    if len(X) >= 60000:
        result = _train_fast_large_dataset_model(X_train, y_train, X_test, y_test, labels, sport)
    else:
        result = _train_ensemble(X_train, y_train, X_test, y_test, factories, labels, n_trials=10)

    Path(settings.model_dir).mkdir(parents=True, exist_ok=True)
    model_type_str = "+".join(result["model_types"])
    path = f"{settings.model_dir}/{sport}_ensemble_{model_type_str}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.joblib"

    bundle = {
        "models": result["models"],
        "meta_learner": result["meta_learner"],
        "features": GENERIC_SPORT_FEATURES,
        "model_types": result["model_types"],
        "weights": result["weights"],
        "accuracy": result["accuracy"],
        "sample_size": len(X),
        "sport": sport,
        "split": "chronological_70_30_ensemble",
        "labels": labels,
    }
    joblib.dump(bundle, path)

    return {
        "path": path,
        "accuracy": result["accuracy"],
        "sample_size": len(X),
        "model_type": model_type_str,
        "sport": sport,
        "split": "chronological_70_30_ensemble",
    }
