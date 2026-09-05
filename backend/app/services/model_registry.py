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

# Production models should not be replaced by a statistically weaker run just
# because the new run happens to be close on accuracy. A tiny tolerance is
# retained for normal training noise, while sample size prevents a smaller
# dataset from displacing a better-established model.
MAX_ACCURACY_REGRESSION = 0.001  # 0.1 percentage point
MIN_SAMPLE_RATIO_TO_REPLACE = 0.90


def _artifact_is_loadable(path: str) -> bool:
    """Validate that a model artifact is real and exposes prediction behavior.

    This is intentionally lightweight: it does not execute inference against
    production data, but it catches corrupt/empty uploads and common cases where
    a release contains metadata instead of an actual trained estimator.
    """
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

    for candidate in candidates:
        if callable(getattr(candidate, "predict", None)):
            return True
        if callable(getattr(candidate, "predict_proba", None)):
            return True
    return False


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

    # Runtime fallback is deliberately read-only: if the active artifact is
    # missing/corrupt on ephemeral storage, use the strongest valid local
    # artifact without changing database activation state from a prediction request.
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
    """Register a model while protecting the last known-good production model.

    Worker training is never allowed to activate a model because the worker's
    filesystem is not the Render production filesystem. Production activation
    additionally requires a valid artifact, enough samples, sane metrics, and
    a quality gate against the currently active model.
    """
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
        # Training workers share Neon with Render but do not share its filesystem.
        activate = False
    elif not artifact_ok or not sample_ok or not accuracy_ok:
        activate = False
    elif current is None or not current_sample_ok:
        # No usable production artifact exists, so a valid model may become active.
        activate = True
    else:
        # A replacement must be at least as accurate within a very small noise
        # tolerance and backed by roughly the same amount of evidence.
        accuracy_floor = current.accuracy - MAX_ACCURACY_REGRESSION
        sample_floor = max(min_samples, int(current.sample_size * MIN_SAMPLE_RATIO_TO_REPLACE))
        activate = accuracy >= accuracy_floor and sample_size >= sample_floor

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
