import os

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

WORKER_MODEL_PREFIXES = ("/tmp/models/",)


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
    if mv and os.path.isfile(mv.path):
        return mv

    # A database row alone is not enough: Render's filesystem is ephemeral.
    # Prefer a model whose artifact actually exists on this instance.
    available = (
        db.query(ModelVersion)
        .filter(ModelVersion.sport == sport, ModelVersion.sample_size >= min_samples)
        .order_by(ModelVersion.accuracy.desc(), ModelVersion.trained_at.desc())
        .all()
    )
    for candidate in available:
        if os.path.isfile(candidate.path):
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
    """Register a model while protecting the last known-good production model."""
    sport = str(sport).strip().lower()
    path = str(path)
    accuracy = float(accuracy)
    sample_size = int(sample_size)

    min_samples = MIN_ACTIVE_SAMPLES.get(sport, 100)
    sample_ok = sample_size >= min_samples
    worker_local = path.startswith(WORKER_MODEL_PREFIXES)

    current = (
        db.query(ModelVersion)
        .filter_by(sport=sport, is_active=True)
        .order_by(ModelVersion.trained_at.desc())
        .first()
    )
    current_artifact_ok = bool(current and os.path.isfile(current.path))
    current_sample_ok = bool(current and current.sample_size >= min_samples and current_artifact_ok)

    if worker_local:
        activate = False
    elif not sample_ok:
        activate = False
    elif current is None or not current_sample_ok:
        # Includes the important restart case: DB says active, but Render lost
        # the local artifact during a restart/redeploy.
        activate = True
    else:
        activate = accuracy >= (current.accuracy - 0.002)

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
