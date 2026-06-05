from sqlalchemy.orm import Session

from app.db.models import ModelVersion


def active_model_path(db: Session, sport: str = "soccer") -> str | None:
    mv = db.query(ModelVersion).filter_by(sport=sport, is_active=True).order_by(ModelVersion.trained_at.desc()).first()
    return mv.path if mv else None


def register_model(db: Session, sport: str, model_type: str, path: str, accuracy: float, sample_size: int) -> ModelVersion:
    current = db.query(ModelVersion).filter_by(sport=sport, is_active=True).first()
    activate = current is None or accuracy >= current.accuracy
    if activate:
        db.query(ModelVersion).filter_by(sport=sport, is_active=True).update({"is_active": False})
    mv = ModelVersion(sport=sport, model_type=model_type, path=path, accuracy=accuracy, sample_size=sample_size, is_active=activate)
    db.add(mv)
    db.commit()
    db.refresh(mv)
    return mv
