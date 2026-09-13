"""Tests for the Kaggle → GitHub repository_dispatch trigger."""

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Make repo root importable
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load trigger module directly (kaggle/ has no __init__.py)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "trigger_ec2_training",
    REPO_ROOT / "kaggle" / "trigger_ec2_training.py",
)
trigger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trigger)


def test_build_payload_contains_no_secrets():
    payload = trigger._build_payload(["soccer", "basketball"], "training-pipeline")
    assert payload["event_type"] == "reeds-training"
    client = payload["client_payload"]
    assert client["source"] == "kaggle"
    assert client["requested_by"] == "training-pipeline"
    assert client["sports"] == ["soccer", "basketball"]
    # Must not contain any secret-looking keys
    raw = json.dumps(payload).lower()
    for forbidden in ("token", "secret", "password", "api_key", "database_url", "aws"):
        assert forbidden not in raw, f"payload leaked '{forbidden}'"


def test_trigger_training_missing_token_raises():
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        trigger.trigger_training("", "zagzy8776/REEDS", ["soccer"])


def test_trigger_training_missing_repo_raises():
    with pytest.raises(RuntimeError, match="GITHUB_REPO"):
        trigger.trigger_training("fake-token", "", ["soccer"])


def test_trigger_training_success(monkeypatch):
    class FakeResponse:
        status_code = 204
        text = ""

    def fake_post(url, headers=None, json=None, timeout=None):
        assert "Authorization" in headers
        assert "Bearer fake-token" == headers["Authorization"]
        assert json["event_type"] == "reeds-training"
        assert json["client_payload"]["source"] == "kaggle"
        return FakeResponse()

    # Patch requests.post at the module level where it's imported
    monkeypatch.setattr(trigger.requests, "post", fake_post)
    result = trigger.trigger_training("fake-token", "zagzy8776/REEDS", ["soccer"])
    assert result["status"] == "triggered"
    assert result["repo"] == "zagzy8776/REEDS"
    assert "actions_url" in result


def test_trigger_training_failure_response(monkeypatch):
    class FakeResponse:
        status_code = 401
        text = "Bad credentials"

    monkeypatch.setattr(trigger.requests, "post", lambda *a, **kw: FakeResponse())
    result = trigger.trigger_training("bad-token", "zagzy8776/REEDS", ["soccer"])
    assert result["status"] == "error"
    assert result["code"] == 401


def test_secret_redaction_in_trigger():
    """Ensure the trigger module never prints the token value."""
    source = Path(trigger.__file__).read_text(encoding="utf-8")
    lines = [l for l in source.splitlines() if "print" in l.lower()]
    for line in lines:
        stripped = line.strip()
        # Allow docstrings/comments
        if stripped.startswith('"""') or stripped.startswith("'''") or stripped.startswith("#"):
            continue
        if "GITHUB_TOKEN" in line or "github_token" in line:
            continue
        # Allow the word "token" in docstrings like "Never prints the token"
        if "never prints" in stripped.lower() or "raises" in stripped.lower():
            continue
        assert "token" not in line.lower(), f"possible token leak: {line}"


def test_main_returns_nonzero_on_error(monkeypatch):
    monkeypatch.setattr(trigger, "trigger_training", lambda *a, **kw: {"status": "error"})
    monkeypatch.setattr(sys, "argv", ["trigger_ec2_training.py"])
    code = trigger.main()
    assert code != 0


def test_main_returns_zero_on_success(monkeypatch):
    monkeypatch.setattr(trigger, "trigger_training", lambda *a, **kw: {"status": "triggered"})
    monkeypatch.setattr(sys, "argv", ["trigger_ec2_training.py"])
    code = trigger.main()
    assert code == 0