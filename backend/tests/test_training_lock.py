"""Tests for EC2 training lock, checksum verification, and secret redaction."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Make backend scripts importable
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Load ec2_train_worker directly (scripts/ has no __init__.py)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "ec2_train_worker",
    BACKEND_DIR / "scripts" / "ec2_train_worker.py",
)
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)


# ── Training lock ──────────────────────────────────────────────────────────

def test_acquire_and_release_training_lock(tmp_path):
    lock_file = tmp_path / "test-training.lock"
    if lock_file.exists():
        lock_file.unlink()
    with mock.patch.object(worker, "TRAINING_LOCK_FILE", lock_file):
        assert worker._acquire_training_lock() is True
        worker._release_training_lock()
        # After release, should be acquirable again
        assert worker._acquire_training_lock() is True
        worker._release_training_lock()


def test_training_lock_prevents_double_acquire(tmp_path):
    """Lock file persists; a second process cannot acquire while held."""
    import subprocess
    lock_file = tmp_path / "test-training.lock"
    if lock_file.exists():
        lock_file.unlink()
    with mock.patch.object(worker, "TRAINING_LOCK_FILE", lock_file):
        assert worker._acquire_training_lock() is True
        # Simulate another process trying to acquire
        result = subprocess.run(
            [sys.executable, "-c", f"""
import sys
sys.path.insert(0, r"{BACKEND_DIR}")
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "ec2_train_worker",
    r"{BACKEND_DIR / 'scripts' / 'ec2_train_worker.py'}",
)
w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w)
import pathlib
w.TRAINING_LOCK_FILE = pathlib.Path(r"{lock_file}")
print("ACQUIRED" if w._acquire_training_lock() else "BLOCKED")
"""],
            capture_output=True, text=True, timeout=10,
        )
        assert "BLOCKED" in result.stdout
        worker._release_training_lock()


# ── Checksum verification ──────────────────────────────────────────────────

def test_sha256_file_computes_correct_hash(tmp_path):
    import hashlib
    content = b"hello world"
    f = tmp_path / "test.bin"
    f.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert worker._sha256_file(f) == expected


def test_sha256_file_missing_returns_none():
    assert worker._sha256_file(Path("/nonexistent/file.bin")) is None


def test_verify_checksum_match(tmp_path):
    import hashlib
    content = b"model data here"
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(content)
    sidecar = tmp_path / "model.json"
    expected = hashlib.sha256(content).hexdigest()
    sidecar.write_text(json.dumps({"sha256": expected}))
    # Should not raise
    worker._verify_checksum(artifact, sidecar)


def test_verify_checksum_mismatch(tmp_path):
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(b"real content")
    sidecar = tmp_path / "model.json"
    sidecar.write_text(json.dumps({"sha256": "deadbeef"}))
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        worker._verify_checksum(artifact, sidecar)


def test_verify_checksum_no_checksum_in_sidecar(tmp_path):
    """If no sha256 in sidecar, verification is skipped silently."""
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(b"content")
    sidecar = tmp_path / "model.json"
    sidecar.write_text(json.dumps({"sport": "soccer"}))
    # Should not raise
    worker._verify_checksum(artifact, sidecar)


def test_verify_checksum_missing_sidecar(tmp_path):
    """If sidecar is missing, verification is skipped."""
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(b"content")
    # Should not raise
    worker._verify_checksum(artifact, tmp_path / "missing.json")


# ── Secret redaction ───────────────────────────────────────────────────────

def test_redact_masks_postgres_url():
    text = "Connection failed: postgres://user:secret@host:5432/db"
    redacted = worker._redact(text)
    assert "secret" not in redacted
    assert "<REDACTED>" in redacted


def test_redact_masks_password():
    text = "Error: password=supersecret123"
    redacted = worker._redact(text)
    assert "supersecret123" not in redacted
    assert "<REDACTED>" in redacted


def test_redact_does_not_alter_non_secret_text():
    text = "Training completed successfully"
    assert worker._redact(text) == text


# ── Sidecar checksum inclusion ─────────────────────────────────────────────

def test_write_sidecar_includes_checksum(tmp_path):
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(b"test model data")
    result = {
        "sport": "soccer",
        "model_type": "oof_ensemble",
        "accuracy": 0.75,
        "sample_size": 1000,
        "models_trained": ["random_forest", "xgboost"],
        "runtime_versions": {"scikit_learn": "1.6.0"},
    }
    with mock.patch.object(worker, "BACKEND_DIR", tmp_path):
        worker._write_sidecar(result, artifact)
    sidecar = tmp_path / "model.json"
    assert sidecar.is_file()
    meta = json.loads(sidecar.read_text())
    assert "sha256" in meta
    assert meta["sha256"] == worker._sha256_file(artifact)


# ── Training state transitions ─────────────────────────────────────────────

def test_cmd_train_lock_held_returns_config(tmp_path):
    """When lock is held by another process, train returns EXIT_CONFIG."""
    lock_file = tmp_path / "test-training.lock"
    if lock_file.exists():
        lock_file.unlink()
    with mock.patch.object(worker, "TRAINING_LOCK_FILE", lock_file):
        # Acquire lock first
        assert worker._acquire_training_lock() is True
        # Now try to run train — should fail with EXIT_CONFIG
        args = mock.Mock()
        args.sport = "soccer"
        args.skip_upload = True
        args.force = False
        # Mock _setup_app_env to avoid DB connection
        with mock.patch.object(worker, "_setup_app_env"), \
             mock.patch.dict(os.environ, {"MODEL_DIR": str(tmp_path)}):
            code = worker._cmd_train(args)
        assert code == worker.EXIT_CONFIG
        worker._release_training_lock()


def test_cmd_train_force_overrides_lock(tmp_path):
    """--force should override the training lock."""
    lock_file = tmp_path / "test-training.lock"
    if lock_file.exists():
        lock_file.unlink()
    with mock.patch.object(worker, "TRAINING_LOCK_FILE", lock_file):
        assert worker._acquire_training_lock() is True
        args = mock.Mock()
        args.sport = "soccer"
        args.skip_upload = True
        args.force = True
        # Mock the heavy parts
        with mock.patch.object(worker, "_setup_app_env"), \
             mock.patch.object(worker, "_train_sport", return_value=worker.EXIT_OK), \
             mock.patch.dict(os.environ, {"MODEL_DIR": str(tmp_path)}):
            code = worker._cmd_train(args)
        assert code == worker.EXIT_OK
        worker._release_training_lock()


# ── Stale-lock regression ──────────────────────────────────────────────────

def test_stale_lock_with_dead_pid_is_reclaimed(tmp_path):
    """A lock file whose recorded PID is dead must be reclaimed, not block."""
    lock_file = tmp_path / "stale-training.lock"
    # Write a PID that cannot exist (negative).
    lock_file.write_text("-999999", encoding="utf-8")
    with mock.patch.object(worker, "TRAINING_LOCK_FILE", lock_file):
        assert worker._acquire_training_lock() is True
        worker._release_training_lock()


def test_nonexistent_lock_file_acquires_cleanly(tmp_path):
    """No lock file at all must acquire without error."""
    lock_file = tmp_path / "missing-training.lock"
    assert not lock_file.exists()
    with mock.patch.object(worker, "TRAINING_LOCK_FILE", lock_file):
        assert worker._acquire_training_lock() is True
        worker._release_training_lock()


def test_release_when_no_lock_held_is_safe(tmp_path):
    """Releasing without holding must not raise."""
    lock_file = tmp_path / "never-acquired-training.lock"
    with mock.patch.object(worker, "TRAINING_LOCK_FILE", lock_file):
        worker._release_training_lock()
        assert not lock_file.exists()


def test_lock_file_removed_on_release(tmp_path):
    """The lock file must be unlinked after a clean release."""
    lock_file = tmp_path / "cleanup-training.lock"
    with mock.patch.object(worker, "TRAINING_LOCK_FILE", lock_file):
        assert worker._acquire_training_lock() is True
        assert lock_file.is_file()
        worker._release_training_lock()
        assert not lock_file.exists()


def test_lock_directory_failure_returns_false(tmp_path):
    """A lock path whose parent is a file (not a dir) must return False, not raise.

    Previously the mkdir exception was swallowed by a bare except, which then
    fell through to the fcntl path and could surface a misleading 'lock held'
    message. The new implementation prints the error and returns False.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")
    lock_file = blocker / "perm-training.lock"
    with mock.patch.object(worker, "TRAINING_LOCK_FILE", lock_file):
        result = worker._acquire_training_lock()
        assert result is False