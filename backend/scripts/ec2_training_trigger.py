"""REEDS EC2 training trigger — direct on-instance, no GitHub Actions.

Architecture:
  EC2 (REEDS TRAINING) runs the heavy training directly via:
    /home/ubuntu/reeds-venv/bin/python \
      /home/ubuntu/REEDS/backend/scripts/ec2_train_worker.py run-all

  Remote trigger (Kaggle, CI, manual) writes a request file to a well-known
  path and EC2 polls it on a timer.  This keeps AWS credentials and the
  training port off the network while still allowing an external signal.

  The trigger is a plain JSON file:
    /home/ubuntu/REEDS/deploy/ec2/training-request.json
      {"sports": ["soccer","basketball"], "requested_by": "kaggle"}

  The poller reads the file, atomically renames it to .lock so a second poll
  cycle cannot double-fire, runs the worker, then writes a result file.

Usage (on EC2, manual test):
  echo '{"sports":["soccer"],"requested_by":"manual"}' > /home/ubuntu/REEDS/deploy/ec2/training-request.json
  /home/ubuntu/reeds-venv/bin/python /home/ubuntu/REEDS/backend/scripts/ec2_train_worker.py run-all --request /home/ubuntu/REEDS/deploy/ec2/training-request.json

Environment knobs:
  TRAINING_REQUEST_FILE   path to the request JSON (default /home/ubuntu/REEDS/deploy/ec2/training-request.json)
  TRAINING_RESULT_FILE    path to write the result JSON (default <request>.result.json)
  TRAINING_POLL_SECONDS   how often the poller checks (default 300)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_DIR.parent

DEFAULT_REQUEST_FILE = Path(
    os.environ.get("TRAINING_REQUEST_FILE", str(REPO_ROOT / "deploy" / "ec2" / "training-request.json"))
)
DEFAULT_POLL_SECONDS = int(os.environ.get("TRAINING_POLL_SECONDS", "300"))


def _read_request(path: Path) -> dict | None:
    """Read a training request JSON. Returns None if missing or invalid."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _run_worker(sports: list[str], requested_by: str) -> dict:
    """Spawn the real training worker as a child process and capture its result."""
    import subprocess

    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "ec2_train_worker.py"),
        "run-all",
        "--sports",
        ",".join(sports),
    ]
    env = os.environ.copy()
    env["TRAINING_REQUESTED_BY"] = requested_by
    env["TRAINING_REQUEST_ID"] = str(int(time.time()))

    print(f"[trigger] spawning: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, env=env, check=False, cwd=str(BACKEND_DIR))
    return {
        "requested_by": requested_by,
        "sports": sports,
        "exit_code": proc.returncode,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _serve_once(request_path: Path) -> bool:
    """Process one request file if present. Returns True if a request was handled."""
    payload = _read_request(request_path)
    if not payload:
        return False

    # Atomically rename so a concurrent poll cannot double-fire.
    lock_path = request_path.with_suffix(".lock")
    try:
        request_path.rename(lock_path)
    except OSError:
        return False

    sports = [str(s).strip().lower() for s in (payload.get("sports") or []) if str(s).strip()]
    requested_by = str(payload.get("requested_by") or "unknown")
    if not sports:
        result = {"status": "error", "reason": "no sports in request", "requested_by": requested_by}
    else:
        print(f"[trigger] handling request sports={sports} by={requested_by}", flush=True)
        result = _run_worker(sports, requested_by)
        result["status"] = "ok" if result["exit_code"] == 0 else "failed"

    result_path = request_path.with_name(request_path.name + ".result.json")
    _write_result(result_path, result)
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass
    print(f"[trigger] result written to {result_path}", flush=True)
    return True


def serve_forever(poll_seconds: int = DEFAULT_POLL_SECONDS) -> int:
    """Poll the request file forever. Handles SIGTERM/SIGINT gracefully."""
    request_path = Path(os.environ.get("TRAINING_REQUEST_FILE", str(DEFAULT_REQUEST_FILE)))
    request_path.parent.mkdir(parents=True, exist_ok=True)

    stop = False

    def _handler(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    print(f"[trigger] polling {request_path} every {poll_seconds}s", flush=True)
    while not stop:
        handled = _serve_once(request_path)
        if not handled:
            time.sleep(poll_seconds)
    print("[trigger] shutting down", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="REEDS EC2 training trigger")
    sub = parser.add_subparsers(dest="command", required=True)

    p_poll = sub.add_parser("serve", help="Poll the request file and train on demand")
    p_poll.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    p_poll.set_defaults(func=lambda a: serve_forever(a.poll_seconds))

    p_once = sub.add_parser("once", help="Process one request file and exit")
    p_once.add_argument("--request", default=str(DEFAULT_REQUEST_FILE))
    p_once.set_defaults(func=lambda a: 0 if _serve_once(Path(a.request)) else 1)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())