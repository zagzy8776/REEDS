"""Runtime patches applied at app startup."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def apply_runtime_patches() -> None:
    """Wire JSON-safe signatures and league normalization into live modules."""
    try:
        from app.services import predictions as pred_mod
        from app.services.prediction_signature import (
            prediction_signature,
            existing_prediction_changed,
        )
        pred_mod._prediction_signature = prediction_signature
        pred_mod._existing_prediction_changed = existing_prediction_changed
        log.info("patched predictions signature helpers")
    except Exception:
        log.exception("failed to patch prediction signatures")

    try:
        from app.ml import features as feat_mod
        from app.ml.league_normalize import soccer_league_difficulty
        feat_mod._soccer_league_difficulty = soccer_league_difficulty
        log.info("patched soccer league difficulty normalizer")
    except Exception:
        log.exception("failed to patch league difficulty")
