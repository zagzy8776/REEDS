"""REEDS historical bootstrap worker (run on Kaggle, not Render).

P0 + P1 combined: downloads FDCO CSVs (football-data.co.uk), loads them into
Neon, runs walk-forward historical evidence, pivots into MarketEvidence, and
recomputes the market gate. Safe to re-run (upserts keyed by
(fixture_id, market, fold_index); pivot + gate are idempotent).

Secrets (Kaggle Add-ons -> Secrets, or env vars):
  DATABASE_URL   (Neon/PostgreSQL connection string)
  ADMIN_API_KEY  (Render admin key, only used for optional verify)
  RENDER_URL     (optional, e.g. https://reeds-phj1.onrender.com)

Env knobs:
  SPORTS         comma list, default "soccer"
  MIN_TRAIN_ROWS default 400
  FOLD_SIZE      default 400
  FCDO_LEAGUES   default "E0,E1,E2,E3,SP1,D1,D2,I1,I2,F1,F2"
  FCDO_SEASONS   default "2425,2324,2223,2221,2021,1920,1819,1718"
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

REPO_URL = "https://github.com/zagzy8776/REEDS.git"
WORKDIR = Path("/kaggle/working/REEDS")
SKLEARN_MIN_VER = (1, 6)
SKLEARN_MAX_VER = (1, 7)


def secret(name: str) -> str:
    try:
        from kaggle_secrets import UserSecretsClient
        value = UserSecretsClient().get_secret(name)
        if value:
            return value.strip()
    except Exception:
        pass
    return os.environ.get(name, "").strip()


DATABASE_URL = secret("DATABASE_URL")
ADMIN_API_KEY = secret("ADMIN_API_KEY")
RENDER_URL = secret("RENDER_URL").rstrip("/")
if not DATABASE_URL or not ADMIN_API_KEY:
    raise RuntimeError("Missing DATABASE_URL or ADMIN_API_KEY Kaggle secret")

os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["APP_ENV"] = "production"
os.environ["FCDO_LEAGUES"] = os.getenv("FCDO_LEAGUES", "E0,E1,E2,E3,SP1,D1,D2,I1,I2,F1,F2")
os.environ["FCDO_SEASONS"] = os.getenv("FCDO_SEASONS", "2425,2324,2223,2122,2021,1920,1819,1718")


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def wait_for_render_ready(max_seconds: int = 600) -> None:
    if not RENDER_URL:
        print("RENDER_URL not set; skipping readiness wait", flush=True)
        return
    deadline = time.time() + max_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            response = requests.get(f"{RENDER_URL}/ready", timeout=20)
            if response.ok and response.json().get("ready") is True:
                print(f"Render ready (attempt {attempt})", flush=True)
                return
        except requests.RequestException:
            pass
        time.sleep(min(5 + attempt, 20))
    print("Render readiness not confirmed; continuing against Neon directly", flush=True)


print("=== GIT ===", flush=True)
if WORKDIR.exists():
    run(["git", "-C", str(WORKDIR), "fetch", "origin", "main"])
    run(["git", "-C", str(WORKDIR), "reset", "--hard", "origin/main"])
else:
    run(["git", "clone", "--depth=1", REPO_URL, str(WORKDIR)])

BACKEND = WORKDIR / "backend"

print("=== DEPENDENCIES ===", flush=True)
run([sys.executable, "-m", "pip", "install", "-q",
     "pandas", "numpy", "scikit-learn==1.6.0",
     "SQLAlchemy==2.0.36", "psycopg[binary]==3.2.3",
     "pydantic-settings==2.7.1", "python-dotenv", "requests"], BACKEND)

sys.path.insert(0, str(BACKEND))

import sklearn  # noqa: E402

sklearn_ver = tuple(int(part) for part in sklearn.__version__.split(".")[:2])
if not (SKLEARN_MIN_VER <= sklearn_ver < SKLEARN_MAX_VER):
    raise RuntimeError(
        f"scikit-learn mismatch: got {sklearn.__version__}, need 1.6.x — "
        "the pip install above pinned 1.6.0, re-run the cell"
    )
print(f"ML runtime locked: scikit-learn {sklearn.__version__}", flush=True)

from app.db.session import SessionLocal, init_db  # noqa: E402

init_db()
db = SessionLocal()

print("=== P0: FDCO BACKFILL (download + load into Neon) ===", flush=True)
try:
    subprocess.run(
        [sys.executable, "scripts/backfill_fcdo.py"],
        cwd=str(BACKEND), check=True,
    )
except subprocess.CalledProcessError as exc:
    print(f"backfill_fcdo exited {exc.returncode}; continuing with whatever loaded", flush=True)

from app.db.models import Fixture  # noqa: E402

soccer_rows = db.query(Fixture).filter(Fixture.sport == "soccer").count()
print(f"soccer fixtures now in Neon: {soccer_rows:,}", flush=True)
if soccer_rows < 2000:
    print("WARNING: too few soccer fixtures for meaningful evidence; backfill may have failed", flush=True)

print("=== P1: WALK-FORWARD HISTORICAL EVIDENCE ===", flush=True)
from app.services.historical_evidence import run_historical_evidence  # noqa: E402
from app.services.evidence_pivot import pivot_historical_evidence  # noqa: E402
from app.services.market_gate import compute_market_evidence, market_evidence_summary  # noqa: E402

sports = [s.strip().lower() for s in os.getenv("SPORTS", "soccer").split(",") if s.strip()]
min_train = int(os.getenv("MIN_TRAIN_ROWS", "400"))
fold_size = int(os.getenv("FOLD_SIZE", "400"))
started = time.time()
report = run_historical_evidence(
    db,
    sports=sports,
    min_train_rows=min_train,
    fold_size=fold_size,
    job_id="bootstrap",
    dry_run=False,
)
print(f"evidence report ({(time.time() - started) / 60:.1f} min):", flush=True)
print(report, flush=True)

print("=== PIVOT -> MarketEvidence.historical_* ===", flush=True)
pivot = pivot_historical_evidence(db)
print(pivot, flush=True)

print("=== RECOMPUTE MARKET GATE ===", flush=True)
gate = compute_market_evidence(db)
print(gate, flush=True)

summary = market_evidence_summary(db)
print("=== GATE SUMMARY ===", flush=True)
for m in summary["markets"]:
    print(
        f"{m['sport']} {m['market']}: total={m['total_settled']} "
        f"combined_acc={m['combined_accuracy']} blocked={m['publication_blocked']}",
        flush=True,
    )

print("=== OPTIONAL RENDER VERIFY ===", flush=True)
wait_for_render_ready()
if RENDER_URL:
    for path in ("/api/admin/bootstrap-summary", "/api/admin/bootstrap-markets"):
        try:
            response = requests.get(
                f"{RENDER_URL}{path}",
                headers={"X-Admin-Key": ADMIN_API_KEY},
                timeout=60,
            )
            print(path, response.status_code, response.text[:1000], flush=True)
        except Exception as exc:  # noqa: BLE001
            print(path, "verify warning:", exc, flush=True)

print("\n=== HISTORICAL BOOTSTRAP COMPLETE ===", flush=True)
print("Next: check the public gate at /api/health or the admin bootstrap-markets endpoint.", flush=True)