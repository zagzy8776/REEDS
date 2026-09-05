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
    """Install leakage-safe training and customer-facing prediction quality gates."""
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
                print(f"  quality large-data ensemble fallback for {sport}: {exc}")
                return original_fast(X_train, y_train, X_test, y_test, labels, sport)

        train_module._train_fast_large_dataset_model = quality_large_dataset_model
    except Exception:
        pass

    # The model can be statistically strong while an individual generated pick is
    # still too weak to expose publicly. Install this gate at the real publication
    # path so every scheduler/cron/manual generation route gets the same policy.
    try:
        import app.services.predictions as predictions_module
        from app.services.prediction_quality import evaluate_publication

        if not getattr(predictions_module, "_quality_gate_installed", False):
            original_select = predictions_module.select_public_picks
            original_fallback = predictions_module.choose_provisional_public_pick

            def quality_select_public_picks(items: list[dict], max_picks: int = 4) -> set[int]:
                annotated = []
                for idx, item in enumerate(items):
                    accepted, reasons = evaluate_publication(item)
                    meta = item.get("engine_meta") if isinstance(item.get("engine_meta"), dict) else {}
                    item["engine_meta"] = {
                        **meta,
                        "publication_quality": {
                            "accepted": accepted,
                            "reasons": reasons,
                        },
                    }
                    annotated.append((idx, item, accepted))

                # Preserve the existing market diversity/threshold logic, but never
                # allow it to promote an item rejected by the quality gate.
                eligible = [item for item in annotated if item[2]]
                if not eligible:
                    return set()
                return original_select([item for _, item, _ in eligible], max_picks=max_picks) and {
                    eligible[position][0]
                    for position, (idx, _, _) in enumerate(eligible)
                    if position in original_select([item for _, item, _ in eligible], max_picks=max_picks)
                }

            def quality_fallback(items: list[dict]) -> dict | None:
                accepted = [item for item in items if evaluate_publication(item)[0]]
                if not accepted:
                    return None
                return max(accepted, key=lambda item: float(item.get("confidence", 0) or 0))

            predictions_module.select_public_picks = quality_select_public_picks
            predictions_module.choose_provisional_public_pick = quality_fallback
            predictions_module._quality_gate_installed = True
    except Exception:
        # Do not block startup if the optional quality module cannot import.
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
    """Validate model structure and metadata before it reaches production."""
    bundle = joblib.load(path)
    if not isinstance(bundle, dict):
        raise ValueError("invalid model bundle: expected dictionary")

    models = bundle.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("invalid model bundle: no models")

    invalid_models = [
        name for name, model in models.items()
        if not callable(getattr(model, "predict_proba", None))
    ]
    if invalid_models:
        raise ValueError(
            "invalid model bundle: missing predict_proba for "
            + ", ".join(str(name) for name in invalid_models[:5])
        )

    labels = bundle.get("labels")
    if labels is not None and (not isinstance(labels, (list, tuple)) or len(labels) < 2):
        raise ValueError("invalid model bundle: labels")

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
