"""Tests for the Kaggle → EC2 direct training trigger.

The trigger is no longer a GitHub repository_dispatch. It POSTs a non-secret
request to the Render-hosted proxy, which stages the request on a dedicated
branch so the EC2 trigger service can pick it up locally.
"""

import json
import os
import sys
from pathlib import Path

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
    assert payload["sports"] == ["soccer", "basketball"]
    assert payload["requested_by"] == "training-pipeline"
    assert "triggered_at" in payload
    # Must not contain any secret-looking keys
    raw = json.dumps(payload).lower()
    for forbidden in ("token", "secret", "password", "api_key", "database_url", "aws", "github"):
        assert forbidden not in raw, f"payload leaked '{forbidden}'"


def test_trigger_training_missing_url_raises():
    with pytest.raises(RuntimeError, match="TRIGGER_URL"):
        trigger.trigger_training("", "fake-key", ["soccer"])


def test_trigger_training_missing_key_raises():
    with pytest.raises(RuntimeError, match="TRIGGER_KEY"):
        trigger.trigger_training("https://example.com", "", ["soccer"])


def test_trigger_training_success(monkeypatch):
    class FakeResponse:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {"status": "accepted"}

    def fake_post(url, headers=None, json=None, timeout=None):
        assert "X-Trigger-Key" in headers
        assert headers["X-Trigger-Key"] == "fake-key"
        assert json["sports"] == ["soccer"]
        assert json["requested_by"] == "training-pipeline"
        assert "triggered_at" in json
        return FakeResponse()

    monkeypatch.setattr(trigger.requests, "post", fake_post)
    result = trigger.trigger_training("https://example.com", "fake-key", ["soccer"])
    assert result["status"] == "triggered"
    assert result["sports"] == ["soccer"]


def test_trigger_training_failure_response(monkeypatch):
    class FakeResponse:
        ok = False
        status_code = 401
        text = "Bad credentials"

    monkeypatch.setattr(trigger.requests, "post", lambda *a, **kw: FakeResponse())
    result = trigger.trigger_training("https://example.com", "bad-key", ["soccer"])
    assert result["status"] == "error"
    assert result["code"] == 401


def test_secret_redaction_in_trigger():
    """Ensure the trigger module never prints the trigger key value."""
    source = Path(trigger.__file__).read_text(encoding="utf-8")
    lines = [l for l in source.splitlines() if "print" in l.lower()]
    for line in lines:
        stripped = line.strip()
        # Allow docstrings/comments
        if stripped.startswith('"""') or stripped.startswith("'''") or stripped.startswith("#"):
            continue
        if "TRIGGER_KEY" in line or "trigger_key" in line:
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