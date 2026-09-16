"""Tests for the EC2 worker's GitHub-only publish path.

The worker must never contact the API instance: model_bootstrap.py restores
missing artifacts from GitHub 'models-v*' releases on startup. These tests mock
the GitHub API at the ``requests`` boundary, so they never hit the network and
never touch real credentials, artifacts, or the production database.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
WORKER_PATH = BACKEND_DIR / "scripts" / "ec2_train_worker.py"


def _load_worker():
    spec = importlib.util.spec_from_file_location("ec2_train_worker_under_test", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.ok = status_code < 400
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class _FakeRequests:
    """In-memory stand-in for the ``requests`` module the worker imports."""

    def __init__(self, *, fail_asset=False):
        self.calls: list[tuple[str, str]] = []
        self.fail_asset = fail_asset

    def post(self, url, **_kwargs):
        self.calls.append(("POST", url))
        if "/releases" in url and "?" not in url.split("/releases")[-1]:
            # Release creation (no asset query string).
            return _FakeResponse(201, {"id": 1, "upload_url": "https://upload.example/{?name,label}"})
        if self.fail_asset:
            return _FakeResponse(422, {"message": "asset rejected"})
        return _FakeResponse(201, {})

    def delete(self, url, **_kwargs):
        self.calls.append(("DELETE", url))
        return _FakeResponse(204, {})

    def get(self, url, **_kwargs):
        self.calls.append(("GET", url))
        return _FakeResponse(200, [{"tag_name": "models-v20260101000000-soccer"}])


@pytest.fixture()
def worker(monkeypatch):
    module = _load_worker()
    monkeypatch.setenv("GITHUB_REPO", "zagzy8776/REEDS")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-not-a-real-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
    monkeypatch.delenv("RENDER_URL", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.setattr(module, "_MODE", "upload", raising=False)
    return module


def _make_artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "soccer_bundle.joblib"
    artifact.write_bytes(b"fake artifact bytes")
    sidecar = tmp_path / "soccer_bundle.json"
    sidecar.write_text(json.dumps({"sport": "soccer", "accuracy": 0.8, "sample_size": 500}), encoding="utf-8")
    return artifact


def test_publish_uploads_artifact_and_sidecar(worker, monkeypatch, tmp_path):
    fake = _FakeRequests()
    monkeypatch.setitem(sys.modules, "requests", fake)
    artifact = _make_artifact(tmp_path)

    worker._upload_artifact(artifact, "soccer")

    posts = [url for method, url in fake.calls if method == "POST"]
    assert any(url.endswith("/releases") for url in posts)
    assert sum(1 for url in posts if "upload.example" in url) == 2  # artifact + sidecar
    assert not any(method == "DELETE" for method, _ in fake.calls)


def test_publish_deletes_release_when_asset_upload_fails(worker, monkeypatch, tmp_path):
    fake = _FakeRequests(fail_asset=True)
    monkeypatch.setitem(sys.modules, "requests", fake)
    artifact = _make_artifact(tmp_path)

    with pytest.raises(RuntimeError):
        worker._upload_artifact(artifact, "soccer")

    deletes = [url for method, url in fake.calls if method == "DELETE"]
    assert len(deletes) == 1  # broken release must not survive as newest models-v*


def test_upload_requires_github_token(worker, monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        worker._setup_app_env()
    assert excinfo.value.code == worker.EXIT_CONFIG


def test_upload_rejects_bad_sidecar_metrics(worker, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "requests", _FakeRequests())
    artifact = tmp_path / "soccer_bundle.joblib"
    artifact.write_bytes(b"fake artifact bytes")
    sidecar = tmp_path / "soccer_bundle.json"
    sidecar.write_text(json.dumps({"sport": "soccer", "accuracy": 1.5, "sample_size": 0}), encoding="utf-8")

    with pytest.raises(RuntimeError):
        worker._upload_artifact(artifact, "soccer")


def test_run_all_rejects_unknown_sport(worker):
    assert worker._main(["run-all", "--sports", "soccer,quidditch"]) == worker.EXIT_CONFIG


@pytest.mark.parametrize("status_code", [200, 401, 503])
def test_validate_uses_read_only_github_check(worker, monkeypatch, tmp_path, status_code):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "sklearn", SimpleNamespace(__version__="1.6.0"))
    db = MagicMock()
    db.query.return_value.filter.return_value.group_by.return_value.order_by.return_value.all.return_value = []
    db.query.return_value.count.return_value = 0
    monkeypatch.setitem(sys.modules, "app.db.session", SimpleNamespace(SessionLocal=lambda: db))
    monkeypatch.setitem(sys.modules, "app.db.models", SimpleNamespace(Fixture=MagicMock()))
    fake = _FakeRequests()

    def get(url, **kwargs):
        fake.calls.append(("GET", url))
        assert kwargs["headers"]["Authorization"] == "Bearer test-token-not-a-real-secret"
        return _FakeResponse(status_code, [])

    fake.get = get
    monkeypatch.setitem(sys.modules, "requests", fake)

    expected = worker.EXIT_OK if status_code == 200 else worker.EXIT_NETWORK
    assert worker._main(["validate"]) == expected
    assert fake.calls == [("GET", "https://api.github.com/repos/zagzy8776/REEDS/releases?per_page=30")]
    db.close.assert_called_once()


def test_cli_reports_failed_publish_and_preserves_files(worker, monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "requests", _FakeRequests(fail_asset=True))
    artifact = _make_artifact(tmp_path)
    assert worker._main(["upload", "--sport", "soccer", "--artifact", str(artifact)]) == worker.EXIT_NETWORK
    assert artifact.read_bytes() == b"fake artifact bytes"
    assert artifact.with_suffix(".json").is_file()


def _inject_registry(worker, monkeypatch):
    """Inject fake app.db.session / app.services.model_registry; return (db, calls)."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    db = MagicMock()
    calls = []

    def fake_register(_db, sport, model_type, path, accuracy, sample_size):
        calls.append((sport, model_type, path, accuracy, sample_size))
        return SimpleNamespace(is_active=True)

    monkeypatch.setitem(sys.modules, "app.db.session", SimpleNamespace(SessionLocal=lambda: db))
    monkeypatch.setitem(sys.modules, "app.services.model_registry", SimpleNamespace(register_model=fake_register))
    return db, calls


def test_upload_registers_published_model(worker, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
    monkeypatch.setitem(sys.modules, "requests", _FakeRequests())
    _db, calls = _inject_registry(worker, monkeypatch)
    artifact = _make_artifact(tmp_path)

    assert worker._main(["upload", "--sport", "soccer", "--artifact", str(artifact)]) == worker.EXIT_OK
    assert calls == [("soccer", "uploaded", str(artifact.resolve()), 0.8, 500)]


def test_registration_failure_returns_network_error_and_preserves_files(worker, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/test")
    monkeypatch.setitem(sys.modules, "requests", _FakeRequests())

    def boom(*_args, **_kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setitem(sys.modules, "app.db.session", SimpleNamespace(SessionLocal=lambda: MagicMock()))
    monkeypatch.setitem(sys.modules, "app.services.model_registry", SimpleNamespace(register_model=boom))
    artifact = _make_artifact(tmp_path)

    assert worker._main(["upload", "--sport", "soccer", "--artifact", str(artifact)]) == worker.EXIT_NETWORK
    assert artifact.read_bytes() == b"fake artifact bytes"
    assert artifact.with_suffix(".json").is_file()
