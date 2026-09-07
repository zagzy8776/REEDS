"""Verify the REEDS Render/Hugging Face wake boundary without printing secrets."""
from __future__ import annotations

import hashlib
import os
import sys
from urllib.parse import urlparse

import requests


RENDER_URL = os.getenv("RENDER_URL", "https://reeds-phj1.onrender.com").rstrip("/")
CRON_SECRET = os.getenv("CRON_SECRET", "").strip()
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()


def diag(value: str) -> dict:
    value = value.strip()
    return {
        "configured": bool(value),
        "length": len(value),
        "sha256_prefix": hashlib.sha256(value.encode()).hexdigest()[:12] if value else "",
    }


def main() -> int:
    host = urlparse(RENDER_URL).netloc
    print(f"Render target: {host}")
    print(f"Local CRON_SECRET: {diag(CRON_SECRET)}")

    health = requests.get(f"{RENDER_URL}/health", timeout=20)
    health.raise_for_status()
    print(f"/health: HTTP {health.status_code}")

    ready = requests.get(f"{RENDER_URL}/ready", timeout=20)
    print(f"/ready: HTTP {ready.status_code}")
    if not ready.ok:
        print(ready.text[:500])
        return 1

    headers = {"X-Cron-Secret": CRON_SECRET} if CRON_SECRET else {}
    wake = requests.get(f"{RENDER_URL}/api/wake", headers=headers, timeout=60)
    print(f"/api/wake: HTTP {wake.status_code}")
    if wake.ok:
        print(wake.json())
        return 0

    if ADMIN_API_KEY:
        diagnostic = requests.get(
            f"{RENDER_URL}/api/admin/cron-diagnostics",
            headers={"x-admin-key": ADMIN_API_KEY},
            timeout=20,
        )
        print(f"cron diagnostic: HTTP {diagnostic.status_code}")
        if diagnostic.ok:
            data = diagnostic.json()
            print({
                "local": diag(CRON_SECRET),
                "render": {
                    "configured": data.get("configured"),
                    "length": data.get("length"),
                    "sha256_prefix": data.get("sha256_prefix"),
                },
            })

    print(wake.text[:500])
    return 1


if __name__ == "__main__":
    sys.exit(main())
