"""Leakage-safe ensemble training for LOYAL EDGE.

The old ensemble fitted its meta-learner directly on the final test
probabilities. That makes the reported test accuracy optimistic and can cause
a model to learn the answer key. This module separates:

    base-train -> meta-validation -> untouched final test

Base models are evaluated on the meta-validation window, weights are learned
from validation log-loss, then the base models are retrained on all training
rows before the untouched test window is scored.
"""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss


def _align(model, probabilities, labels):
    aligned = np.zeros((len(probabilities), len(labels)), dtype=float)
    for source_idx, cls in enumerate(model.classes_):
        if cls in labels:
            aligned[:, labels.index(cls)] = probabilities[:, source_idx]
    # Numerical safety: every row must remain a valid probability distribution.
    row_sums = aligned.sum(axis=1, keepdims=True)
    return np.divide(aligned, np.maximum(row_sums, 1e-12))


def train_quality_ensemble(
    X_train,
    y_train,
    X_test,
    y_test,
    factories,
    labels,
    n_trials=0,
):
    """Train a chronological, leakage-safe probability ensemble.

    ``n_trials`` is accepted for API compatibility but deliberately ignored:
    hyperparameter selection must not inspect the final test window.
    """
    del n_trials

    n_train = len(X_train)
    if n_train < 40:
        raise ValueError("Need at least 40 training rows for leakage-safe stacking")

    # Reserve the newest 20% of the training window for meta-learning.
    meta_size = max(20, int(n_train * 0.20))
    if n_train - meta_size < 20:
        meta_size = max(10, n_train // 3)
    base_end = n_train - meta_size
    if base_end < 20:
        raise ValueError("Not enough chronological rows for base/meta split")

    X_base, y_base = X_train.iloc[:base_end], y_train.iloc[:base_end]
    X_meta, y_meta = X_train.iloc[base_end:], y_train.iloc[base_end:]

    models = {}
    meta_probas = []
    test_probas = []
    validation_scores = {}

    for name, factory in factories:
        try:
            # First fit exists only to produce genuinely out-of-sample meta data.
            validation_model = factory(None)
            validation_model.fit(X_base, y_base)
            val_probs = _align(validation_model, validation_model.predict_proba(X_meta), labels)
            val_preds = [labels[int(np.argmax(row))] for row in val_probs]
            val_accuracy = float(accuracy_score(y_meta, val_preds))
            try:
                val_logloss = float(log_loss(y_meta, val_probs, labels=labels))
            except ValueError:
                val_logloss = 10.0

            # Convert probabilistic quality into a stable positive weight.
            weight = 1.0 / max(val_logloss, 0.05)
            validation_scores[name] = {
                "accuracy": val_accuracy,
                "log_loss": val_logloss,
                "weight": weight,
            }
            meta_probas.append(val_probs)

            # Final serving model is fit only after meta-learning data is frozen.
            final_model = factory(None)
            final_model.fit(X_train, y_train)
            models[name] = final_model
            test_probas.append(_align(final_model, final_model.predict_proba(X_test), labels))
            print(
                f"  {name}: validation_accuracy={val_accuracy:.4f}, "
                f"validation_logloss={val_logloss:.4f}, weight={weight:.4f}"
            )
        except Exception as exc:
            print(f"  {name}: SKIPPED ({exc})")

    if not models:
        raise ValueError("No models could be trained!")

    total_weight = sum(validation_scores[name]["weight"] for name in models)
    weighted_validation = sum(
        probs * (validation_scores[name]["weight"] / total_weight)
        for name, probs in zip(models, meta_probas)
    )
    weighted_test = sum(
        probs * (validation_scores[name]["weight"] / total_weight)
        for name, probs in zip(models, test_probas)
    )

    # Meta-learner sees only validation predictions that were generated without
    # fitting the corresponding validation rows. It never sees X_test/y_test.
    meta = None
    final_probas = weighted_test
    if len(models) >= 2 and len(np.unique(np.asarray(y_meta))) >= 2:
        try:
            validation_stack = np.column_stack(meta_probas)
            test_stack = np.column_stack(test_probas)
            meta = LogisticRegression(max_iter=1000, C=0.5, solver="lbfgs")
            meta.fit(validation_stack, y_meta)
            meta_test = meta.predict_proba(test_stack)
            meta_aligned = _align(meta, meta_test, labels)
            # Keep the robust weighted blend dominant; stacking contributes only
            # after it has learned on an independent chronological validation set.
            final_probas = 0.70 * weighted_test + 0.30 * meta_aligned
        except Exception as exc:
            print(f"  meta-learner: fallback to weighted blend ({exc})")

    final_preds = [labels[int(np.argmax(row))] for row in final_probas]
    accuracy = float(accuracy_score(y_test, final_preds))

    return {
        "models": models,
        "meta_learner": meta,
        "accuracy": accuracy,
        "model_types": list(models.keys()),
        "weights": [validation_scores[name]["weight"] for name in models],
        "ensemble_probas": final_probas,
        "validation_scores": validation_scores,
    }
