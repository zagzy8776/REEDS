"""Runtime patches applied at app startup."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def apply_runtime_patches() -> None:
    """Wire JSON-safe signatures, league normalization, and fixture quality gate."""
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

    try:
        from app.scraper import loaders as loaders_mod
        from app.services.fixture_quality import save_rejected_fixture, validate_fixture

        original = loaders_mod.upsert_fixture
        if not getattr(original, "_reeds_quality_wrapped", False):
            def upsert_fixture_with_quality(db, fixture):
                quality = validate_fixture(fixture.home_team, fixture.away_team, fixture.sport)
                if not quality.get("valid"):
                    save_rejected_fixture(
                        db,
                        provider=getattr(fixture, "source", None) or "unknown",
                        raw_home=fixture.home_team,
                        raw_away=fixture.away_team,
                        reason=str(quality.get("reason") or "invalid_team_name"),
                        sport=fixture.sport,
                        league=fixture.league,
                        match_date=fixture.match_date,
                        raw_payload=fixture.extra if isinstance(getattr(fixture, "extra", None), dict) else None,
                    )
                    return None
                fixture.home_team = quality["home"]
                fixture.away_team = quality["away"]
                return original(db, fixture)

            upsert_fixture_with_quality._reeds_quality_wrapped = True  # type: ignore[attr-defined]
            loaders_mod.upsert_fixture = upsert_fixture_with_quality
            log.info("wrapped loaders.upsert_fixture with fixture quality gate")
    except Exception:
        log.exception("failed to wrap upsert_fixture quality gate")
