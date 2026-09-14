import math
import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import ModelVersion
from app.ml.model_cache import model_bundle_filesize


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


def _artifact_exists(path: str) -> bool:
    """Cheap, non-deserializing artifact check."""
    if not path or not os.path.isfile(path):
        return False
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _metric(value, default=None):
    """Return a finite float metric when one is available."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _candidate_is_better(current: ModelVersion, accuracy: float, sample_size: int) -> bool:
    """Conservative replacement gate."""
    current_accuracy = _metric(current.accuracy, 0.0)
    accuracy_floor = current_accuracy - MAX_ACCURACY_REGRESSION
    sample_floor = max(
        MIN_ACTIVE_SAMPLES.get(current.sport, 100),
        int(current.sample_size * MIN_SAMPLE_RATIO_TO_REPLACE),
    )
    return accuracy >= accuracy_floor and sample_size >= sample_floor


def _local_model_candidates(sport: str) -> list[Path]:
    """Find real local artifacts available to the running backend.

    Model metadata can outlive a deployment path (for example a model registered
    while the service lived on Render). The artifact itself may already be on the
    AWS disk. Search only the backend model directories; never invent a URL or
    deserialize anything during this repair.
    """
    try:
        from app.core.config import get_settings

        configured = Path(get_settings().model_dir)
    except Exception:
        configured = Path("data/models")

    roots = [
        configured,
        Path.cwd() / configured,
        Path.cwd() / "data" / "models",
        Path.cwd().parent / "data" / "models",
    ]
    seen: set[Path] = set()
    candidates: list[Path] = []
    token = str(sport).strip().lower()
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        for path in root.glob("*.joblib"):
            name = path.name.lower()
            if token not in name:
                continue
            if not _artifact_exists(str(path)):
                continue
            candidates.append(path)
    return sorted(set(candidates), key=lambda p: (p.stat().st_mtime_ns, p.stat().st_size), reverse=True)


def _repair_active_model_path(db: Session, mv: ModelVersion, sport: str) -> ModelVersion:
    """Repair stale DB paths when the real artifact is already on this host."""
    if _artifact_exists(mv.path):
        return mv

    candidates = _local_model_candidates(sport)
    if not candidates:
        return mv

    local_path = str(candidates[0])
    old_path = mv.path
    mv.path = local_path
    try:
        db.commit()
        db.refresh(mv)
    except Exception:
        db.rollback()
        mv.path = old_path
    return mv


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
    if mv:
        mv = _repair_active_model_path(db, mv, sport)
        if _artifact_exists(mv.path):
            return mv

    # If an older inactive record has a real local artifact, it is still safer
    # to use that validated artifact than to report "no model" and silently
    # suppress all predictions. The existing DB metrics remain authoritative.
    available = (
        db.query(ModelVersion)
        .filter(ModelVersion.sport == sport, ModelVersion.sample_size >= min_samples)
        .order_by(ModelVersion.accuracy.desc(), ModelVersion.trained_at.desc())
        .all()
    )
    for candidate in available:
        candidate = _repair_active_model_path(db, candidate, sport)
        if _artifact_exists(candidate.path):
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
    artifact_ok = _artifact_exists(path)
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
    current_artifact_ok = bool(current and _artifact_exists(current.path))
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
