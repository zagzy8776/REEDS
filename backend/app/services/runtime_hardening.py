"""Runtime safety wrappers for the multi-provider scheduler.

Keeps provider failures isolated and prevents a bad transaction or optional
provider from poisoning the rest of the refresh cycle.
"""
from __future__ import annotations

from functools import wraps
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _install_trained_soccer_meta_bridge() -> None:
    """Use the leakage-safe meta learner when a trained soccer bundle provides one."""
    try:
        import numpy as np
        import pandas as pd
        from app.ml.ensemble import LoyalEdgeEngine

        original = getattr(LoyalEdgeEngine, "_ensemble_predict", None)
        if original is None or getattr(original, "_reeds_meta_bridge", False):
            return

        def meta_ensemble_predict(self, features_row, labels):
            result = original(self, features_row, labels)
            bundle = self._load_bundle()
            meta = bundle.get("meta_learner") if isinstance(bundle, dict) else None
            models = bundle.get("models") if isinstance(bundle, dict) else None
            weights = bundle.get("weights") if isinstance(bundle, dict) else None
            if meta is None or not isinstance(models, dict) or not models:
                return result
            try:
                x = pd.DataFrame([features_row]).reindex(columns=bundle.get("features", []), fill_value=0)
                stacked_parts = []
                used_weights = []
                for idx, (name, model) in enumerate(models.items()):
                    p = model.predict_proba(x)[0]
                    classes = list(getattr(model, "classes_", labels))
                    aligned = np.zeros(len(labels), dtype=float)
                    for pos, cls in enumerate(classes):
                        if cls in labels:
                            aligned[labels.index(cls)] = float(p[pos])
                    stacked_parts.append(aligned)
                    used_weights.append(float(weights[idx]) if isinstance(weights, list) and idx < len(weights) else 1.0)
                if not stacked_parts:
                    return result
                stacked = np.column_stack(stacked_parts).reshape(1, -1)
                meta_raw = meta.predict_proba(stacked)[0]
                meta_aligned = np.zeros(len(labels), dtype=float)
                for pos, cls in enumerate(getattr(meta, "classes_", labels)):
                    if cls in labels:
                        meta_aligned[labels.index(cls)] = float(meta_raw[pos])
                total = sum(used_weights) or float(len(used_weights))
                base = sum(part * (w / total) for part, w in zip(stacked_parts, used_weights))
                final = 0.7 * base + 0.3 * meta_aligned
                final = final / final.sum() if final.sum() > 0 else final
                return {"away": float(final[0]), "draw": float(final[1]), "home": float(final[2])}
            except Exception:
                log.exception("Soccer meta-learner inference failed; using base ensemble")
                return result

        meta_ensemble_predict._reeds_meta_bridge = True
        LoyalEdgeEngine._ensemble_predict = meta_ensemble_predict
        log.info("Trained soccer meta-learner inference bridge installed")
    except Exception:
        log.exception("Could not install trained soccer meta-learner bridge")


def _install_trained_generic_prediction_bridge() -> None:
    """Make trained generic-sport artifacts the primary Moneyline engine."""
    try:
        import pandas as pd
        from app.ml.generic import GenericSportEngine
        from app.ml.model_cache import load_model_bundle
        from app.ml.train import GENERIC_SPORT_FEATURES, _build_generic_features
        from app.services.model_registry import active_model_path
        from app.core.config import get_settings

        original = getattr(GenericSportEngine, "predict", None)
        if original is None or getattr(original, "_reeds_trained_bridge", False):
            return

        supported = {
            "tennis", "american_football", "hockey", "cricket",
            "rugby", "baseball",
        }

        def trained_predict(self, history, fixture):
            sport = str(fixture.get("sport") or "").strip().lower()
            if sport not in supported:
                return original(self, history, fixture)

            try:
                db = fixture.get("_db")
                model_path = active_model_path(db, sport) if db is not None else None
                if not model_path:
                    model_dir = Path(get_settings().model_dir)
                    candidates = sorted(
                        model_dir.glob(f"{sport}_ensemble_*_slim.joblib"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    model_path = str(candidates[0]) if candidates else None
                if not model_path:
                    return original(self, history, fixture)

                bundle = load_model_bundle(model_path)
                models = bundle.get("models") if isinstance(bundle, dict) else None
                if not isinstance(models, dict) or not models:
                    return original(self, history, fixture)

                target = dict(fixture)
                target["sport"] = sport
                target["home_score"] = 1
                target["away_score"] = 0
                target.setdefault("league", "")
                target.setdefault("season", "prediction")
                target.setdefault("home_odds", None)
                target.setdefault("away_odds", None)
                target.setdefault("draw_odds", None)

                hist = history.copy() if isinstance(history, pd.DataFrame) else pd.DataFrame()
                if hist.empty:
                    return original(self, history, fixture)
                target_frame = pd.DataFrame([target])
                combined = pd.concat([hist, target_frame], ignore_index=True, sort=False)
                X, _ = _build_generic_features(combined, sport)
                if X.empty:
                    return original(self, history, fixture)
                X = X.reindex(columns=GENERIC_SPORT_FEATURES, fill_value=0)
                row = X.iloc[[-1]]

                labels = list(bundle.get("labels") or [0, 1])
                weights = list(bundle.get("weights") or [])
                probas = []
                used_weights = []
                for idx, (name, model) in enumerate(models.items()):
                    try:
                        if hasattr(model, "predict_proba"):
                            p = model.predict_proba(row)[0]
                            classes = list(getattr(model, "classes_", labels))
                            aligned = {int(cls): float(p[pos]) for pos, cls in enumerate(classes) if int(cls) in labels}
                            if aligned:
                                probas.append(aligned)
                                used_weights.append(float(weights[idx]) if idx < len(weights) else 1.0)
                    except Exception:
                        log.exception("Generic model inference failed: %s/%s", sport, name)

                if not probas:
                    return original(self, history, fixture)

                total_weight = sum(used_weights) or float(len(used_weights))
                home_prob = sum(p.get(1, 0.5) * w for p, w in zip(probas, used_weights)) / total_weight
                home_prob = max(0.02, min(0.98, home_prob))
                away_prob = 1.0 - home_prob
                confidence = max(home_prob, away_prob) * 100
                pick = "Home Win" if home_prob >= 0.5 else "Away Win"
                risk = "Low" if confidence >= 72 else "Medium" if confidence >= 58 else "High"

                items = original(self, history, fixture) or []
                moneyline = next((item for item in items if item.get("market") == "Moneyline"), None)
                meta = {
                    "summary": f"Trained {sport.replace('_', ' ')} production model used for the Moneyline read.",
                    "model_source": "active_model_registry" if db is not None else "latest_local_model_artifact",
                    "model_path": model_path,
                    "model_accuracy": float(bundle.get("accuracy", 0.0)),
                    "training_rows": int(bundle.get("sample_size", 0)),
                    "model_types": list(models.keys()),
                    "probabilities": {"home_win": round(home_prob, 4), "away_win": round(away_prob, 4)},
                    "market_logic": "Production model probability replaces the heuristic Moneyline probability; other sport-specific markets remain analytical overlays.",
                }
                if moneyline is None:
                    items.insert(0, {
                        "market": "Moneyline",
                        "pick": pick,
                        "confidence": round(confidence, 1),
                        "edge_score": round(confidence, 1),
                        "risk_level": risk,
                        "reasoning": f"Trained {sport.replace('_', ' ')} model leans {pick} at {confidence:.1f}% confidence.",
                        "engine_meta": meta,
                    })
                else:
                    moneyline.update({
                        "pick": pick,
                        "confidence": round(confidence, 1),
                        "edge_score": round(confidence, 1),
                        "risk_level": risk,
                        "reasoning": f"Trained {sport.replace('_', ' ')} model leans {pick} at {confidence:.1f}% confidence using {int(bundle.get('sample_size', 0)):,} completed rows.",
                        "engine_meta": {**(moneyline.get("engine_meta") or {}), **meta},
                    })
                return items
            except Exception:
                log.exception("Trained generic model bridge failed for %s; using heuristic fallback", sport)
                return original(self, history, fixture)

        trained_predict._reeds_trained_bridge = True
        GenericSportEngine.predict = trained_predict
        log.info("Trained generic prediction bridge installed")
    except Exception:
        log.exception("Could not install trained generic prediction bridge")


def install_provider_runtime_hardening() -> None:
    try:
        import app.services.scheduler as scheduler
        import app.scraper.deep_coverage as deep
        import app.scraper.loaders as loaders

        original_upsert = getattr(loaders, "upsert_fixture", None)
        if original_upsert and not getattr(original_upsert, "_reeds_hardened", False):
            def hardened_upsert(db, fixture):
                try:
                    return original_upsert(db, fixture)
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        log.exception("Fixture rollback failed")
                    raise
            hardened_upsert._reeds_hardened = True
            loaders.upsert_fixture = hardened_upsert
            if hasattr(scheduler, "upsert_fixture"):
                scheduler.upsert_fixture = hardened_upsert
            if hasattr(deep, "upsert_fixture"):
                deep.upsert_fixture = hardened_upsert

        def wrap_module_fn(module, name: str):
            current = getattr(module, name, None)
            if current is None or getattr(current, "_reeds_provider_hardened", False):
                return
            @wraps(current)
            def wrapped(*args, **kwargs):
                try:
                    return current(*args, **kwargs)
                except Exception:
                    db = args[0] if args and hasattr(args[0], "rollback") else None
                    if db is not None:
                        try:
                            db.rollback()
                        except Exception:
                            log.exception("Provider rollback failed: %s", name)
                    raise
            wrapped._reeds_provider_hardened = True
            setattr(module, name, wrapped)
            if module is scheduler:
                return

        provider_names = [
            "ingest_api_football_fixtures",
            "ingest_sportmonks_football_fixtures",
            "ingest_football_data_org_matches",
            "ingest_apifootball_com_events",
            "ingest_bzzoiro_football",
            "ingest_openfoot_football",
            "ingest_api_basketball_games",
            "ingest_allsportsapi_events",
            "ingest_thesportsdb_events",
            "ingest_web_score_sources",
        ]
        for name in provider_names:
            wrap_module_fn(scheduler, name)

        current_openfoot = getattr(deep, "_ingest_openfoot", None)
        if current_openfoot and not getattr(current_openfoot, "_reeds_openfoot_safe", False):
            from app.scraper.coverage_sources import ingest_openfoot_football
            def safe_openfoot(db, dates):
                from app.core.config import get_settings
                key = get_settings().openfoot_api_key
                if not key:
                    log.info("OpenFoot skipped: API key not configured")
                    return 0
                return ingest_openfoot_football(db, key, dates)
            safe_openfoot._reeds_openfoot_safe = True
            deep._ingest_openfoot = safe_openfoot

        if hasattr(deep, "_ingest_openfoot"):
            deep._ingest_openfoot = getattr(deep, "_ingest_openfoot")

        _install_trained_soccer_meta_bridge()
        _install_trained_generic_prediction_bridge()
        log.info("Runtime provider hardening installed")
    except Exception:
        log.exception("Runtime provider hardening installation failed")
