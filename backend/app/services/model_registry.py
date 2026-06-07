from sqlalchemy.orm import Session

from app.db.models import ModelVersion


MIN_ACTIVE_SAMPLES = {
    "soccer": 250,
    "basketball": 500,
}


def active_model(db: Session, sport: str = "soccer") -> ModelVersion | None:
    min_samples = MIN_ACTIVE_SAMPLES.get(sport, 100)
    mv = db.query(ModelVersion).filter(ModelVersion.sport == sport, ModelVersion.is_active == True, ModelVersion.sample_size >= min_samples).order_by(ModelVersion.trained_at.desc()).first()
    if not mv:
        mv = db.query(ModelVersion).filter(ModelVersion.sport == sport, ModelVersion.sample_size >= min_samples).order_by(ModelVersion.accuracy.desc(), ModelVersion.trained_at.desc()).first()
    return mv


def active_model_path(db: Session, sport: str = "soccer") -> str | None:
    mv = active_model(db, sport)
    return mv.path if mv else None


def register_model(db: Session, sport: str, model_type: str, path: str, accuracy: float, sample_size: int) -> ModelVersion:
    current = db.query(ModelVersion).filter_by(sport=sport, is_active=True).first()
    min_samples = MIN_ACTIVE_SAMPLES.get(sport, 100)
    sample_ok = sample_size >= min_samples
    current_sample_ok = bool(current and current.sample_size >= min_samples)
    activate = sample_ok and (current is None or not current_sample_ok or accuracy >= current.accuracy)
    if activate:
        db.query(ModelVersion).filter_by(sport=sport, is_active=True).update({"is_active": False})
    mv = ModelVersion(sport=sport, model_type=model_type, path=path, accuracy=accuracy, sample_size=sample_size, is_active=activate)
    db.add(mv)
    db.commit()
    db.refresh(mv)
    return mv
