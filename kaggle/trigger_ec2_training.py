"""REEDS Kaggle → GitHub repository_dispatch trigger.

Kaggle never talks to EC2 directly. It sends an authenticated repository_dispatch
to GitHub, which triggers the self-hosted runner on EC2 via the ec2-train.yml
workflow. This keeps EC2's training port closed and credentials out of the payload.

Secrets (Kaggle Add-ons -> Secrets, or env vars):
  GITHUB_TOKEN   (GitHub PAT with Contents: Read & write on the REEDS repo)

Env knobs:
  GITHUB_REPO    default "zagzy8776/REEDS"
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

GITHUB_API = "https://api.github.com"


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
    """Build a non-secret repository_dispatch payload."""
    return {
        "event_type": "reeds-training",
        "client_payload": {
            "source": "kaggle",
            "requested_by": requested_by,
            "sports": sports,
            "triggered_at": _now_iso(),
        },
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def trigger_training(
    github_token: str,
    github_repo: str,
    sports: list[str],
    requested_by: str = "training-pipeline",
) -> dict[str, Any]:
    """Send an authenticated repository_dispatch to GitHub.

    Raises RuntimeError on any failure. Never prints the token.
    """
    if not github_token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. Add it as a Kaggle secret or environment variable."
        )
    if not github_repo:
        raise RuntimeError("GITHUB_REPO is not set.")

    payload = _build_payload(sports, requested_by)
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    url = f"{GITHUB_API}/repos/{github_repo}/dispatches"

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code == 204:
        return {
            "status": "triggered",
            "event_type": payload["event_type"],
            "repo": github_repo,
            "sports": sports,
            "message": "GitHub Actions ec2-train workflow triggered.",
            "actions_url": f"https://github.com/{github_repo}/actions",
        }
    return {
        "status": "error",
        "code": response.status_code,
        "detail": response.text[:500],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger EC2 training via GitHub repository_dispatch")
    parser.add_argument("--sports", default="", help="Comma-separated sports (default: all)")
    parser.add_argument("--requested-by", default="training-pipeline", help="Label for the trigger source")
    args = parser.parse_args()

    github_token = secret("GITHUB_TOKEN")
    github_repo = secret("GITHUB_REPO") or "zagzy8776/REEDS"
    sports = [s.strip().lower() for s in args.sports.split(",") if s.strip()]

    result = trigger_training(github_token, github_repo, sports, args.requested_by)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "triggered" else 1


if __name__ == "__main__":
    sys.exit(main())