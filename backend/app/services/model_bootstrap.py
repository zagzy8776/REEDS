"""Restore production model artifacts without startup-time model deserialization."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session
from sklearn import __version__ as SKLEARN_VERSION

from app.core.config import get_settings
from app.db.models import ModelArtifact
from app.services.model_registry import register_model

SPORTS = ("american_football", "basketball", "baseball", "soccer", "tennis", "hockey", "cricket", "rugby")


def install_quality_training() -> None:
    return None


def _asset_sport(asset_name: str) -> str:
    name = Path(asset_name).name.lower()
    for sport in SPORTS:
        if sport in name:
            return sport
    return "soccer"


def _local_model_inventory(model_dir: Path) -> list[dict]:
    inventory = []
    for path in sorted(model_dir.glob("*.joblib")):
        try:
            if path.is_file() and path.stat().st_size > 0:
                inventory.append({"sport": _asset_sport(path.name), "file": path.name, "local": True})
        except OSError:
            pass
    return inventory


def _restore_from_neon(db: Session, model_dir: Path) -> list[dict]:
    """Restore the latest durable artifact per sport without loading all rows.

    Previously this fetched every ``ModelArtifact`` row and materialized each
    ``data`` blob (a full model binary) into memory on Render. Now it queries
    the sports list, then fetches the newest artifact per sport one at a time,
    streams it to disk, and releases the bytes immediately.
    """
    restored = []
    sports = [
        row[0]
        for row in db.query(ModelArtifact.sport)
        .distinct()
        .order_by(ModelArtifact.sport.asc())
        .all()
        if row[0]
    ]
    for sport in sorted(sports):
        sport = str(sport).strip().lower()
        artifact = (
            db.query(ModelArtifact)
            .filter(func.lower(ModelArtifact.sport) == sport)
            .order_by(ModelArtifact.created_at.desc(), ModelArtifact.id.desc())
            .first()
        )
        if artifact is None or not artifact.data:
            continue
        destination = model_dir / Path(artifact.filename).name
        temp_path = destination.with_suffix(destination.suffix + ".tmp")
        payload = artifact.data
        try:
            with temp_path.open("wb") as handle:
                handle.write(payload)
            del payload
            os.replace(temp_path, destination)
            mv = register_model(
                db, sport, str(artifact.model_type or "restored")[:50], str(destination),
                float(artifact.accuracy or 0.0), int(artifact.sample_size or 0),
            )
            restored.append({"sport": sport, "file": destination.name, "active": bool(mv.is_active), "source": "postgres"})
        except Exception:
            temp_path.unlink(missing_ok=True)
        finally:
            payload = None
    return restored


def _restore_github_fallback(db: Session, model_dir: Path, restored_sports: set[str]) -> list[dict]:
    """Stream public-release artifacts without deserializing them at startup."""
    settings = get_settings()
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(f"https://api.github.com/repos/{settings.github_repo}/releases?per_page=30", headers=headers, timeout=15)
        if response.status_code == 401 and token:
            response = requests.get(f"https://api.github.com/repos/{settings.github_repo}/releases?per_page=30", headers={"Accept": "application/vnd.github+json"}, timeout=15)
        response.raise_for_status()
        releases = [r for r in response.json() if str(r.get("tag_name", "")).startswith("models-v")]
        releases.sort(key=lambda r: r.get("published_at") or r.get("created_at") or "", reverse=True)
    except Exception:
        return []

    chosen: dict[str, dict] = {}
    for release in releases:
        for asset in release.get("assets", []):
            name = Path(str(asset.get("name", ""))).name
            if name.endswith(".joblib"):
                sport = _asset_sport(name)
                if sport not in chosen:
                    chosen[sport] = asset

    restored = []
    for sport, asset in chosen.items():
        if sport in restored_sports:
            continue
        name = Path(str(asset.get("name", ""))).name
        destination = model_dir / name
        if destination.is_file() and destination.stat().st_size > 0:
            continue
        fd, temp_name = tempfile.mkstemp(prefix=".model-", suffix=".joblib", dir=str(model_dir))
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            with requests.get(asset["browser_download_url"], headers=headers, timeout=180, stream=True) as dl:
                dl.raise_for_status()
                with temp_path.open("wb") as handle:
                    for chunk in dl.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            os.replace(temp_path, destination)
            mv = register_model(db, sport, "restored", str(destination), 0.0, 0)
            restored.append({"sport": sport, "file": name, "active": bool(mv.is_active), "source": "github"})
        except Exception:
            temp_path.unlink(missing_ok=True)
    return restored


def restore_missing_models(db: Session) -> dict:
    settings = get_settings()
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    restored_from_neon = _restore_from_neon(db, model_dir)
    restored_sports = {item["sport"] for item in restored_from_neon}
    restored_from_github = _restore_github_fallback(db, model_dir, restored_sports)
    if restored_from_github:
        db.commit()
    restored = restored_from_neon + restored_from_github
    return {
        "restored": len(restored),
        "models": restored,
        "local_models": _local_model_inventory(model_dir),
        "status": "neon_primary" if restored_from_neon else ("github_fallback" if restored_from_github else "no_models"),
        "startup_deserialization": False,
        "production_sklearn": SKLEARN_VERSION,
    }
