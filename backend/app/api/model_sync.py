"""Safe production model synchronization and durable model uploads."""

from __future__ import annotations

import gc
import os
import shutil
import tempfile

import joblib
import requests
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pathlib import Path
from sqlalchemy.orm import Session
from sklearn import __version__ as SKLEARN_VERSION

from app.core.config import get_settings
from app.db.models import ModelArtifact
from app.db.session import get_db
from app.services.model_registry import register_model

router = APIRouter()


def _admin_key(x_admin_key: str = Header(default="")):
    from app.api.admin import require_admin
    return require_admin(x_admin_key)


def _safe_filename(name: str) -> str:
    clean = Path(str(name)).name
    if clean != str(name) or not clean.endswith(".joblib"):
        raise ValueError(f"unsafe model asset name: {name}")
    return clean


def _validate_bundle(path: Path) -> dict:
    """Validate a locally managed release artifact when it is safe to deserialize.

    This function is intentionally not used by the hot upload endpoint on the
    512 MiB Render instance. Training workers validate their own artifacts before
    upload; Render only needs to persist the already-produced artifact.
    """
    bundle = joblib.load(path)
    try:
        if not isinstance(bundle, dict):
            raise ValueError("model bundle must be a dictionary")
        models = bundle.get("models")
        sport = str(bundle.get("sport") or "").strip().lower()
        accuracy = float(bundle.get("accuracy", 0.0))
        sample_size = int(bundle.get("sample_size", 0))
        model_types = bundle.get("model_types") or []
        if not isinstance(models, dict) or not models:
            raise ValueError("model bundle contains no models")
        if not sport:
            lowered = path.name.lower()
            sport = "basketball" if "basketball" in lowered else "soccer"
        runtime_versions = bundle.get("runtime_versions") or {}
        artifact_sklearn = str(runtime_versions.get("scikit_learn") or "").strip()
        if artifact_sklearn and artifact_sklearn != SKLEARN_VERSION:
            raise ValueError(
                f"incompatible scikit-learn artifact version {artifact_sklearn}; production uses {SKLEARN_VERSION}"
            )
        if not 0.0 <= accuracy <= 1.0:
            raise ValueError(f"invalid accuracy: {accuracy}")
        if sample_size <= 0:
            raise ValueError(f"invalid sample_size: {sample_size}")
        return {
            "sport": sport,
            "accuracy": accuracy,
            "sample_size": sample_size,
            "model_type": "+".join(str(x) for x in model_types)[:50] or "uploaded",
            "runtime_versions": runtime_versions,
        }
    finally:
        del bundle
        gc.collect()


@router.post("/api/admin/upload-model", dependencies=[Depends(_admin_key)])
async def upload_model(
    model: UploadFile = File(...),
    sport: str = Form(""),
    model_type: str = Form("uploaded"),
    accuracy: float = Form(0.0),
    sample_size: int = Form(0),
    db: Session = Depends(get_db),
):
    """Persist a worker-produced model without deserializing it on Render.

    Kaggle is the trusted training/validation worker. Render's free instance has
    only 512 MiB RAM, so loading a multi-estimator joblib merely to validate its
    structure can OOM the production process. The artifact is streamed to disk,
    then copied to Neon as bytes; inference deserializes it only when required.
    """
    settings = get_settings()
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    try:
        filename = _safe_filename(model.filename or "model.joblib")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fd, temp_name = tempfile.mkstemp(prefix="reeds-upload-", suffix=".joblib", dir=str(model_dir))
    os.close(fd)
    temp_path = Path(temp_name)
    payload = None
    try:
        total = 0
        max_bytes = 100 * 1024 * 1024
        with temp_path.open("wb") as handle:
            while True:
                chunk = await model.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=400, detail="Invalid or oversized model artifact")
                handle.write(chunk)
        await model.close()
        if total <= 0:
            raise HTTPException(status_code=400, detail="Invalid or empty model artifact")

        # IMPORTANT: do not call joblib.load() here. That would deserialize all
        # fitted estimators and recreate the exact Render OOM we are preventing.
        final_sport = str(sport).strip().lower()
        if not final_sport:
            lowered = filename.lower()
            final_sport = "basketball" if "basketball" in lowered else "soccer"
        final_type = str(model_type or "uploaded")[:120]
        final_accuracy = float(accuracy)
        final_sample_size = int(sample_size)
        if not 0.0 <= final_accuracy <= 1.0:
            raise HTTPException(status_code=400, detail="Invalid model accuracy")
        if final_sample_size <= 0:
            raise HTTPException(status_code=400, detail="Invalid model sample size")

        payload = temp_path.read_bytes()
        destination = model_dir / filename
        os.replace(temp_path, destination)

        existing = db.query(ModelArtifact).filter_by(sport=final_sport, filename=filename).first()
        if existing:
            existing.model_type = final_type
            existing.accuracy = final_accuracy
            existing.sample_size = final_sample_size
            existing.data = payload
        else:
            db.add(ModelArtifact(
                sport=final_sport,
                filename=filename,
                model_type=final_type,
                accuracy=final_accuracy,
                sample_size=final_sample_size,
                data=payload,
            ))
        db.flush()
        mv = register_model(
            db,
            final_sport,
            final_type,
            str(destination),
            final_accuracy,
            final_sample_size,
        )
        db.commit()
        return {
            "status": "success",
            "durable": True,
            "file": filename,
            "sport": final_sport,
            "accuracy": final_accuracy,
            "sample_size": final_sample_size,
            "model_version_id": mv.id,
            "active": bool(mv.is_active),
            "validation": "worker_validated_streamed_upload",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Model upload failed: {str(exc)[:300]}") from exc
    finally:
        temp_path.unlink(missing_ok=True)
        payload = None
        gc.collect()


@router.post("/api/admin/sync-models-safe", dependencies=[Depends(_admin_key)])
def sync_models_safe(db: Session = Depends(get_db)):
    """Atomically synchronize the latest GitHub model release into production."""
    settings = get_settings()
    github_repo = settings.github_repo
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(
            f"https://api.github.com/repos/{github_repo}/releases",
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        releases = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to inspect model releases: {exc}") from exc
    releases = [r for r in releases if str(r.get("tag_name", "")).startswith("models-v")]
    if not releases:
        return {"status": "no_models_release", "installed": 0}
    assets = [a for a in releases[0].get("assets", []) if str(a.get("name", "")).endswith(".joblib")]
    if not assets:
        return {"status": "no_model_assets", "release": releases[0].get("tag_name"), "installed": 0}
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="reeds-model-stage-", dir=str(model_dir.parent)))
    staged: list[tuple[Path, Path, dict]] = []
    try:
        for asset in assets:
            name = _safe_filename(asset.get("name", ""))
            destination = stage / name
            with requests.get(asset["browser_download_url"], headers=headers, timeout=180, stream=True) as dl:
                dl.raise_for_status()
                with destination.open("wb") as handle:
                    for chunk in dl.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            metadata = _validate_bundle(destination)
            staged.append((destination, model_dir / name, metadata))
        if not staged:
            return {"status": "nothing_staged", "installed": 0}
        backups: list[tuple[Path, Path]] = []
        installed: list[dict] = []
        try:
            for source, destination, metadata in staged:
                backup = stage / f"{destination.name}.previous"
                if destination.exists():
                    os.replace(destination, backup)
                    backups.append((backup, destination))
                os.replace(source, destination)
                installed.append({"file": destination.name, **metadata})
                db.query(ModelArtifact).filter_by(sport=metadata["sport"], filename=destination.name).delete()
                db.add(ModelArtifact(
                    sport=metadata["sport"], filename=destination.name,
                    model_type=metadata["model_type"], accuracy=metadata["accuracy"],
                    sample_size=metadata["sample_size"], data=destination.read_bytes(),
                ))
            for item in installed:
                path = str(model_dir / item["file"])
                mv = register_model(db, item["sport"], item["model_type"], path, item["accuracy"], item["sample_size"])
                item["active"] = bool(mv.is_active)
            db.commit()
        except Exception:
            for _, destination, _ in reversed(staged):
                if destination.exists():
                    destination.unlink(missing_ok=True)
            for backup, destination in reversed(backups):
                if backup.exists():
                    os.replace(backup, destination)
            db.rollback()
            raise
        return {"status": "success", "release": releases[0].get("tag_name"), "installed": len(installed), "models": installed}
    finally:
        shutil.rmtree(stage, ignore_errors=True)
