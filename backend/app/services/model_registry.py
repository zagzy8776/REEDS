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

# HF and Render share Neon but do NOT share a filesystem. A worker-local model
# must never become the active production model before its artifact is uploaded.
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
    if not mv:
        mv = (
            db.query(ModelVersion)
            .filter(ModelVersion.sport == sport, ModelVersion.sample_size >= min_samples)
            .order_by(ModelVersion.accuracy.desc(), ModelVersion.trained_at.desc())
            .first()
        )
    return mv


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

    Training can happen on Hugging Face while serving happens on Render. Both
    use the same Neon database, so a worker-local path must stay inactive until
    the artifact has actually been uploaded to Render.
    """
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
    current_sample_ok = bool(current and current.sample_size >= min_samples)

    if worker_local:
        activate = False
    elif not sample_ok:
        activate = False
    elif current is None or not current_sample_ok:
        activate = True
    else:
        # A retrain may replace the current model only if it is not materially
        # worse. 0.2 percentage points is a small tolerance for test variance.
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
