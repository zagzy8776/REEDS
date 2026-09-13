"""Tests for Render memory safety: no model deserialization during
upload/startup paths, bounded model cache, and missing-model fallback.
"""

import os
import tempfile
from unittest import mock

from app.ml.model_cache import model_bundle_filesize, load_model_bundle


def test_load_model_bundle_missing_path_returns_none():
    assert load_model_bundle(None) is None
    assert load_model_bundle("") is None
    assert load_model_bundle("e:/does-not-exist.joblib") is None


def test_load_model_bundle_corrupt_file_returns_none():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "bundle.joblib")
        with open(path, "wb") as handle:
            handle.write(b"not a joblib payload at all")
        bundle = load_model_bundle(path)
        assert bundle is None


def test_model_bundle_size_is_zero_for_missing():
    assert model_bundle_filesize("e:/nope.joblib") == 0


def test_loyal_edge_engine_does_not_deserialize_at_construction():
    """Construction must not deserialize anything (startup/upload safety)."""
    from app.ml.ensemble import LoyalEdgeEngine

    engine = LoyalEdgeEngine("e:/does-not-exist.joblib")
    assert engine.bundle is None
    proba = engine._ensemble_predict(
        {"home_form_points": 1.5, "away_form_points": 1.0}, [0, 1, 2]
    )
    assert abs(sum(proba.values()) - 1.0) < 1e-6


def test_basketball_engine_lazy_load():
    from app.ml.basketball import BasketballEngine

    engine = BasketballEngine("e:/does-not-exist.joblib")
    assert engine.bundle is None
    assert engine._bundle_home_win_probability({}) is None


def test_upload_sync_validation_does_not_deserialize():
    """_validate_bundle does structural checks only (header + sidecar)."""
    import json

    from app.api import model_sync

    with tempfile.TemporaryDirectory() as td:
        fake = os.path.join(td, "soccer_model.joblib")
        with open(fake, "wb") as handle:
            handle.write(b"PK empty-ish")
        # Metadata comes from the training sidecar, never from unpickling.
        sidecar = os.path.join(td, "soccer_model.json")
        with open(sidecar, "w", encoding="utf-8") as handle:
            json.dump(
                {"sport": "soccer", "accuracy": 0.55, "sample_size": 26013},
                handle,
            )
        metadata = model_sync._validate_bundle(fake)
        assert metadata["sport"] == "soccer"
        assert metadata["sample_size"] == 26013


def test_market_registry_active_model_uses_cheap_check():
    """active_model must not deserialize artifacts (no joblib usage at all)."""
    import ast
    import inspect

    from app.services import model_registry
    from app.db.models import ModelVersion

    # The registry must not even import joblib: cheap filesystem checks only.
    tree = ast.parse(inspect.getsource(model_registry))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "joblib" not in imported

    mv = ModelVersion()
    mv.sport = "soccer"
    mv.is_active = True
    mv.sample_size = 500
    mv.accuracy = 0.5
    mv.trained_at = None
    mv.path = "e:/does-not-exist.joblib"

    db = mock.Mock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = mv
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    result = model_registry.active_model(db, "soccer")
    # The artifact file doesn't exist, so it must fall through to the
    # backup candidates (which are also empty).
    assert result is None