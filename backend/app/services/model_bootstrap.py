"""Restore production model artifacts and harden the training runtime."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import joblib
import requests
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.model_registry import register_model


SPORTS = (
    "american_football", "basketball", "baseball", "soccer",
    "tennis", "hockey", "cricket", "rugby",
)


def install_quality_training() -> None:
    """Install leakage-safe training for both normal and large datasets.

    The production trainer historically switched to a single RandomForest on very
    large datasets. That path is useful as a memory fallback, but it should still
    use probability validation and an ensemble rather than silently changing the
    modelling strategy. This hook replaces both training paths before any worker
    starts a model build.
    """
    try:
        import app.ml.train as train_module
        from app.ml.quality_ensemble import train_quality_ensemble

        train_module._train_ensemble = train_quality_ensemble

        original_fast = train_module._train_fast_large_dataset_model

        def quality_large_dataset_model(X_train, y_train, X_test, y_test, labels, sport):
            # Keep the large-data path memory-conscious: RF + XGBoost only, while
            # retaining the same leakage-safe validation/weighting logic.
            factories = train_module._build_model_factories(
                binary=(len(labels) == 2),
                slim=True,
            )
            try:
                result = train_quality_ensemble(
                    X_train,
                    y_train,
                    X_test,
                    y_test,
                    factories,
                    labels,
                    n_trials=0,
                )
                return result
            except Exception as exc:
                # A hard memory/dependency failure must still leave the existing
                # production fallback available rather than taking the worker down.
                print(f"  quality large-data ensemble fallback for {sport}: {exc}")
                return original_fast(X_train, y_train, X_test, y_test, labels, sport)

        train_module._train_fast_large_dataset_model = quality_large_dataset_model
    except Exception:
        # Training itself will surface the import/dependency error if unavailable.
        pass


def _asset_sport(asset_name: str, bundle: dict | None = None) -> str:
    if bundle and bundle.get("sport"):
        return str(bundle["sport"]).strip().lower()
    name = asset_name.lower()
    for sport in SPORTS:
        if sport in name:
            return sport
    return "soccer"


def _validate(path: Path, asset_name: str) -> dict:
    bundle = joblib.load(path)
    if not isinstance(bundle, dict) or not isinstance(bundle.get("models"), dict) or not bundle["models"]:
        raise ValueError("invalid model bundle")
    accuracy = float(bundle.get("accuracy", 0.0))
    sample_size = int(bundle.get("sample_size", 0))
    if not 0 <= accuracy <= 1 or sample_size <= 0:
        raise ValueError("invalid model metadata")
    model_types = bundle.get("model_types") or []
    return {
        "sport": _asset_sport(asset_name, bundle),
        "accuracy": accuracy,
        "sample_size": sample_size,
        "model_type": "+".join(str(x) for x in model_types)[:50] or "restored",
    }


def restore_missing_models(db: Session) -> dict:
    settings = get_settings()
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(
            f"https://api.github.com/repos/{settings.github_repo}/releases?per_page=30",
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        releases = response.json()
    except Exception as exc:
        return {"restored": 0, "status": "github_unavailable", "error": str(exc)[:200]}

    releases = [r for r in releases if str(r.get("tag_name", "")).startswith("models-v")]
    releases.sort(key=lambda r: r.get("published_at") or r.get("created_at") or "", reverse=True)

    chosen: dict[str, dict] = {}
    for release in releases:
        for asset in release.get("assets", []):
            name = Path(str(asset.get("name", ""))).name
            if not name.endswith(".joblib"):
                continue
            sport = _asset_sport(name)
            if sport not in chosen:
                chosen[sport] = asset

    restored = []
    errors = []
    for sport, asset in chosen.items():
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
            metadata = _validate(temp_path, name)
            os.replace(temp_path, destination)
            mv = register_model(
                db,
                metadata["sport"],
                metadata["model_type"],
                str(destination),
                metadata["accuracy"],
                metadata["sample_size"],
            )
            restored.append({"sport": sport, "file": name, "active": bool(mv.is_active)})
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            errors.append({"sport": sport, "error": str(exc)[:200]})

    return {"restored": len(restored), "models": restored, "errors": errors}
