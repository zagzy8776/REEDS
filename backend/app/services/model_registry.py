import math
import os

import joblib
from sqlalchemy.orm import Session

from app.db.models import ModelVersion


MIN_ACTIVE_SAMPLES = {
    "soccer": 250,
    "basketball": 200,
    "tennis": 200,
    "american_football": 200,
    "hockey": 200,
    "cricket": 200,
    "rugby": 200,
    "baseball": 200,
}

MAX_ACCURACY_REGRESSION = 0.001
MIN_SAMPLE_RATIO_TO_REPLACE = 0.90


def _artifact_is_loadable(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        bundle = joblib.load(path)
    except Exception:
        return False

    candidates = [bundle]
    if isinstance(bundle, dict):
        for key in ("model", "models", "ensemble", "estimator", "classifier"):
            value = bundle.get(key)
            if value is not None:
                if isinstance(value, dict):
                    candidates.extend(value.values())
                elif isinstance(value, (list, tuple)):
                    candidates.extend(value)
                else:
                    candidates.append(value)

    return any(
        callable(getattr(candidate, "predict", None))
        or callable(getattr(candidate, "predict_proba", None))
        for candidate in candidates
    )


def _metric(value, default=None):
    """Return a finite float metric when one is available."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _candidate_is_better(current: ModelVersion, accuracy: float, sample_size: int) -> bool:
    """Conservative replacement gate.

    Accuracy remains the legacy-compatible primary metric. If newer training
    metadata is present in model_type (for example ``accuracy=...;log_loss=...``),
    this function deliberately does not parse it: the current schema has no
    dedicated validation columns. Sample size + accuracy therefore form the
    safe gate until validation metrics get first-class persistence.
    """
    current_accuracy = _metric(current.accuracy, 0.0)
    accuracy_floor = current_accuracy - MAX_ACCURACY_REGRESSION
    sample_floor = max(
        MIN_ACTIVE_SAMPLES.get(current.sport, 100),
        int(current.sample_size * MIN_SAMPLE_RATIO_TO_REPLACE),
    )
    return accuracy >= accuracy_floor and sample_size >= sample_floor


def active_model(db: Session, sport: str = "soccer") -> ModelVersion | None:
    min_samples = MIN_ACTIVE_SAMPLES.get(sport, 100)
    mv = (
        db.query(ModelVersion)
        .filter(
            ModelVersion.sport == sport,
            ModelVersion.is_active == True,
            ModelVersion.sample_size >= min_samples,
        )
        .order_by(ModelVersion.trained_at.desc())
        .first()
    )
    if mv and _artifact_is_loadable(mv.path):
        return mv

    available = (
        db.query(ModelVersion)
        .filter(ModelVersion.sport == sport, ModelVersion.sample_size >= min_samples)
        .order_by(ModelVersion.accuracy.desc(), ModelVersion.trained_at.desc())
        .all()
    )
    for candidate in available:
        if _artifact_is_loadable(candidate.path):
            return candidate
    return None


def active_model_path(db: Session, sport: str = "soccer") -> str | None:
    mv = active_model(db, sport)
    return mv.path if mv else None


def register_model(
    db: Session,
    sport: str,
    model_type: str,
    path: str,
    accuracy: float,
    sample_size: int,
) -> ModelVersion:
    """Register a model without allowing weak worker artifacts into production."""
    sport = str(sport).strip().lower()
    path = str(path)
    accuracy = float(accuracy)
    sample_size = int(sample_size)

    min_samples = MIN_ACTIVE_SAMPLES.get(sport, 100)
    artifact_ok = _artifact_is_loadable(path)
    sample_ok = sample_size >= min_samples
    accuracy_ok = math.isfinite(accuracy) and 0.0 <= accuracy <= 1.0
    worker_training = (
        os.environ.get("MODEL_WORKER", "").strip() == "1"
        or os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
        or path.startswith("/tmp/models/")
    )

    current = (
        db.query(ModelVersion)
        .filter_by(sport=sport, is_active=True)
        .order_by(ModelVersion.trained_at.desc())
        .first()
    )
    current_artifact_ok = bool(current and _artifact_is_loadable(current.path))
    current_sample_ok = bool(current and current.sample_size >= min_samples and current_artifact_ok)

    if worker_training:
        activate = False
    elif not artifact_ok or not sample_ok or not accuracy_ok:
        activate = False
    elif current is None or not current_sample_ok:
        activate = True
    else:
        activate = _candidate_is_better(current, accuracy, sample_size)

    if activate:
        db.query(ModelVersion).filter_by(sport=sport, is_active=True).update({"is_active": False})

    safe_model_type = (model_type or "unknown")[:50]
    mv = ModelVersion(
        sport=sport,
        model_type=safe_model_type,
        path=path,
        accuracy=accuracy,
        sample_size=sample_size,
        is_active=activate,
    )
    db.add(mv)
    db.commit()
    db.refresh(mv)
    return mv
