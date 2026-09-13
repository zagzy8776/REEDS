"""REEDS Kaggle -> EC2 training trigger (direct on-instance, no GitHub Actions).

Architecture:
  Kaggle never talks to EC2 directly and never runs heavy training.
  It writes a JSON request file to a well-known path on the EC2 instance via
  the Render-hosted trigger endpoint, or directly via SSM/SSH if available.

  The EC2 trigger service (deploy/ec2/reeds-train-trigger.service) polls the
  request file on a timer and runs the heavy training locally.

  Request payload (no secrets):
    {"sports": ["soccer","basketball"], "requested_by": "kaggle"}

  This replaces the previous GitHub Actions repository_dispatch path. GitHub
  remains available for source control and model artifact releases only.

Secrets (Kaggle Add-ons -> Secrets, or env vars):
  TRIGGER_URL   Render endpoint that proxies the request file onto EC2
                (e.g. https://reeds-phj1.onrender.com/api/admin/ec2-training-request)
  TRIGGER_KEY   Shared secret for that endpoint (never printed)

Env knobs:
  TRIGGER_URL    default https://reeds-phj1.onrender.com/api/admin/ec2-training-request
  SPORTS         comma list, default "" (all)

Usage:
  python kaggle/trigger_ec2_training.py
  python kaggle/trigger_ec2_training.py --sports soccer,basketball
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests

DEFAULT_TRIGGER_URL = "https://reeds-phj1.onrender.com/api/admin/ec2-training-request"


def secret(name: str) -> str:
    """Read a secret from Kaggle User Secrets or env, never print it."""
    try:
        from kaggle_secrets import UserSecretsClient
        value = UserSecretsClient().get_secret(name)
        if value:
            return value.strip()
    except Exception:
        pass
    return os.environ.get(name, "").strip()


def _build_payload(sports: list[str], requested_by: str) -> dict[str, Any]:
    """Build a non-secret request payload."""
    return {
        "sports": sports,
        "requested_by": requested_by,
        "triggered_at": _now_iso(),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def trigger_training(
    trigger_url: str,
    trigger_key: str,
    sports: list[str],
    requested_by: str = "training-pipeline",
) -> dict[str, Any]:
    """Send an authenticated training-request to the EC2 trigger proxy.

    Raises RuntimeError on any failure. Never prints the trigger key.
    """
    if not trigger_url:
        raise RuntimeError("TRIGGER_URL is not set.")
    if not trigger_key:
        raise RuntimeError(
            "TRIGGER_KEY is not set. Add it as a Kaggle secret or environment variable."
        )

    payload = _build_payload(sports, requested_by)
    headers = {
        "X-Trigger-Key": trigger_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    response = requests.post(trigger_url, headers=headers, json=payload, timeout=30)
    if response.ok:
        return {
            "status": "triggered",
            "requested_by": requested_by,
            "sports": sports,
            "message": "EC2 training request accepted. The EC2 trigger service will pick it up on its next poll cycle.",
            "trigger_url": trigger_url,
        }
    return {
        "status": "error",
        "code": response.status_code,
        "detail": response.text[:500],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger EC2 training via the direct request-file path")
    parser.add_argument("--sports", default="", help="Comma-separated sports (default: all)")
    parser.add_argument("--requested-by", default="training-pipeline", help="Label for the trigger source")
    args = parser.parse_args()

    trigger_url = secret("TRIGGER_URL") or DEFAULT_TRIGGER_URL
    trigger_key = secret("TRIGGER_KEY")
    sports = [s.strip().lower() for s in args.sports.split(",") if s.strip()]

    result = trigger_training(trigger_url, trigger_key, sports, args.requested_by)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "triggered" else 1


if __name__ == "__main__":
    sys.exit(main())