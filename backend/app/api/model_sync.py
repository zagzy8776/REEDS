"""Safe production model synchronization.

Downloads model artifacts into a staging directory, validates every artifact,
and only then replaces production files. A failed download/validation never
wipes the last known-good model files.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import joblib
import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.services.model_registry import register_model

router = APIRouter()


def _admin_key(x_admin_key: str = ""):
    # Kept as a dependency wrapper so this endpoint has the same protection as
    # the existing admin router without importing the router itself.
    from app.api.admin import require_admin
    return require_admin(x_admin_key)


def _safe_filename(name: str) -> str:
    # Release assets are untrusted input. Never allow path traversal.
    clean = Path(str(name)).name
    if clean != str(name) or not clean.endswith(".joblib"):
        raise ValueError(f"unsafe model asset name: {name}")
    return clean


def _validate_bundle(path: Path) -> dict:
    bundle = joblib.load(path)
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
        # Existing bundles may not carry sport metadata; infer only from the
        # controlled filename, never from arbitrary path components.
        lowered = path.name.lower()
        sport = "basketball" if "basketball" in lowered else "soccer"
    if not 0.0 <= accuracy <= 1.0:
        raise ValueError(f"invalid accuracy: {accuracy}")
    if sample_size <= 0:
        raise ValueError(f"invalid sample_size: {sample_size}")
    return {
        "sport": sport,
        "accuracy": accuracy,
        "sample_size": sample_size,
        "model_type": "+".join(str(x) for x in model_types)[:50] or "uploaded",
    }


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
        # Phase 1: download and fully validate everything away from production.
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

        # Phase 2: replace each artifact only after ALL artifacts validated.
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

            # Register only after files are physically present on Render.
            for item in installed:
                path = str(model_dir / item["file"])
                mv = register_model(
                    db,
                    item["sport"],
                    item["model_type"],
                    path,
                    item["accuracy"],
                    item["sample_size"],
                )
                item["active"] = bool(mv.is_active)

        except Exception:
            # Restore files if the atomic publication phase fails.
            for _, destination, _ in reversed(staged):
                if destination.exists():
                    destination.unlink(missing_ok=True)
            for backup, destination in reversed(backups):
                if backup.exists():
                    os.replace(backup, destination)
            db.rollback()
            raise

        return {
            "status": "success",
            "release": releases[0].get("tag_name"),
            "installed": len(installed),
            "models": installed,
        }
    finally:
        shutil.rmtree(stage, ignore_errors=True)
