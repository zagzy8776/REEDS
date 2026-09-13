"""Restore production model artifacts without startup-time model deserialization.

Model transport is EC2 -> GitHub Release -> Render disk (streamed). The Aiven
``model_artifacts`` table stores METADATA ONLY — large ``data`` blobs are never
loaded by this module and the PostgreSQL ``LargeBinary`` column is not part of
the production model path anymore.

Startup guarantees:
  * never read model blobs out of PostgreSQL,
  * never materialize an entire artifact in RAM (streamed 1 MiB chunks),
  * never deserialize estimators at startup,
  * prefer an existing valid local file (no repeated downloads across
    restart loops),
  * verify SHA-256 when the release sidecar records one,
  * only activate a model after the file is on disk and validated.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import requests
from sqlalchemy.orm import Session
from sklearn import __version__ as SKLEARN_VERSION

from app.core.config import get_settings
from app.services.model_registry import register_model

SPORTS = ("american_football", "basketball", "baseball", "soccer", "tennis", "hockey", "cricket", "rugby")

# 100 MiB sanity cap for any single artifact (full OOF ensembles stay well below).
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024


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


def _sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file (constant memory regardless of size)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sha256(artifact: Path, metadata: dict) -> None:
    """Verify the artifact against the SHA-256 recorded in the release metadata.

    Raises RuntimeError when a recorded checksum does not match. Missing
    checksum metadata is accepted (older releases) rather than blocking startup.
    """
    expected = str(metadata.get("sha256") or "").strip().lower()
    if not expected:
        return
    actual = _sha256_file(artifact)
    if actual != expected:
        raise RuntimeError(
            f"SHA-256 mismatch for {artifact.name}: expected {expected[:16]}..., got {actual[:16]}..."
        )


def _github_releases(settings, token: str) -> list[dict]:
    """Latest GitHub model releases (models-v*), newest first. Never logs tokens."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(
            f"https://api.github.com/repos/{settings.github_repo}/releases?per_page=30",
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
    except Exception:
        return []
    releases = [r for r in response.json() if str(r.get("tag_name", "")).startswith("models-v")]
    releases.sort(key=lambda r: r.get("published_at") or r.get("created_at") or "", reverse=True)
    return releases


def _latest_assets_per_sport(releases: list[dict]) -> dict[str, dict]:
    """Pick the newest artifact + sidecar pair per sport from the releases."""
    by_sport: dict[str, dict] = {}
    for release in releases:
        sidecar_by_base = {
            str(a.get("name", "")).rsplit(".", 1)[0]: a
            for a in release.get("assets", [])
            if str(a.get("name", "")).endswith(".json")
        }
        for asset in release.get("assets", []):
            name = Path(str(asset.get("name", ""))).name
            if not name.endswith(".joblib"):
                continue
            sport = _asset_sport(name)
            if sport in by_sport:
                continue
            base = name.rsplit(".", 1)[0]
            sidecar = sidecar_by_base.get(base)
            by_sport[sport] = {
                "name": name,
                "download_url": asset.get("browser_download_url"),
                "sidecar": sidecar.get("browser_download_url") if sidecar else None,
                "release": release.get("tag_name"),
            }
    return by_sport


def _download_streamed(url: str, headers: dict, destination: Path) -> None:
    """Stream a URL to disk in 1 MiB chunks with a size cap. Constant memory."""
    total = 0
    with requests.get(url, headers=headers, timeout=180, stream=True) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_ARTIFACT_BYTES:
                    raise RuntimeError(f"artifact exceeds {MAX_ARTIFACT_BYTES} byte safety limit")
                handle.write(chunk)
    if total <= 0:
        raise RuntimeError("downloaded artifact is empty")


def _restore_from_github(db: Session, model_dir: Path) -> list[dict]:
    """Restore the latest GitHub-release artifact per sport, streaming to disk.

    Uses the sidecar JSON (published next to every artifact by the training
    worker) for metadata + SHA-256 verification. Never deserializes the bundle.
    """
    settings = get_settings()
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    releases = _github_releases(settings, token)
    if not releases:
        return []
    assets = _latest_assets_per_sport(releases)
    if not assets:
        return []

    existing = {item["sport"] for item in _local_model_inventory(model_dir)}
    restored = []
    for sport in sorted(assets):
        if sport in existing:
            continue
        asset = assets[sport]
        name = asset["name"]
        destination = model_dir / name
        if destination.is_file() and destination.stat().st_size > 0:
            continue
        fd, temp_name = tempfile.mkstemp(prefix=".model-", suffix=".joblib", dir=str(model_dir))
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            _download_streamed(asset["download_url"], headers, temp_path)

            # Size sanity (no deserialization).
            if not temp_path.is_file() or temp_path.stat().st_size <= 0:
                raise RuntimeError("artifact file is missing or empty after download")

            metadata: dict = {}
            if asset.get("sidecar"):
                try:
                    side = requests.get(asset["sidecar"], headers=headers, timeout=60)
                    if side.ok:
                        import json
                        metadata = json.loads(side.content.decode("utf-8"))
                except Exception:
                    metadata = {}

            _verify_sha256(temp_path, metadata)

            os.replace(temp_path, destination)
            mv = register_model(
                db,
                sport,
                str(metadata.get("model_type") or "restored")[:50],
                str(destination),
                float(metadata.get("accuracy", 0.0) or 0.0),
                int(metadata.get("sample_size", 0) or 0),
            )
            restored.append({
                "sport": sport,
                "file": name,
                "active": bool(mv.is_active),
                "source": "github",
                "release": asset.get("release"),
                "sha256_verified": bool(metadata.get("sha256")),
            })
        except Exception:
            temp_path.unlink(missing_ok=True)
    if restored:
        try:
            db.commit()
        except Exception:
            db.rollback()
    return restored


def restore_missing_models(db: Session) -> dict:
    """Startup model restore: local disk first, GitHub Release fallback.

    Requirement — check for a valid local model and use it when valid. Only
    stream/activate artifacts that are missing locally so a container restart
    never re-downloads the same release repeatedly.
    """
    settings = get_settings()
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    local_models = _local_model_inventory(model_dir)
    restored_from_github = _restore_from_github(db, model_dir)
    restored = restored_from_github
    return {
        "restored": len(restored),
        "models": restored,
        "local_models": local_models,
        "status": "github" if restored else ("local" if local_models else "no_models"),
        "startup_deserialization": False,
        "production_sklearn": SKLEARN_VERSION,
    }
