"""REEDS Kaggle training worker.

Kaggle provides compute; Render remains the production API and model registry.
Secrets are read from Kaggle User Secrets and are never printed.
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
MODEL_DIR = Path("/kaggle/working/reeds_models")


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
if not DATABASE_URL or not ADMIN_API_KEY or not RENDER_URL:
    raise RuntimeError("Missing DATABASE_URL, ADMIN_API_KEY, or RENDER_URL Kaggle secret")

os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["APP_ENV"] = "production"
os.environ["MODEL_DIR"] = str(MODEL_DIR)
os.environ["MIN_TRAINING_ROWS"] = "200"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def wait_for_render_ready(max_wait_seconds: int = 600) -> None:
    """Never upload models into a Render instance while it is restarting."""
    deadline = time.time() + max_wait_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            response = requests.get(f"{RENDER_URL}/ready", timeout=20)
            if response.ok and response.json().get("ready") is True:
                print(f"Render readiness confirmed on attempt {attempt}")
                return
            print(f"Render not ready yet: HTTP {response.status_code}; waiting...")
        except requests.RequestException as exc:
            print(f"Render readiness request failed; waiting: {exc}")
        time.sleep(min(5 + attempt, 20))
    raise RuntimeError("Render did not become ready within 10 minutes")


def _rewind_files(files) -> None:
    if not isinstance(files, dict):
        return
    for value in files.values():
        handle = value[1] if isinstance(value, tuple) and len(value) >= 2 else None
        if hasattr(handle, "seek"):
            handle.seek(0)


def post(path: str, *, timeout: int = 180, retries: int = 12, **kwargs):
    """POST to Render with restart tolerance and safe multipart retries."""
    headers = {"X-Admin-Key": ADMIN_API_KEY}
    headers.update(kwargs.pop("headers", {}))
    files = kwargs.get("files")
    last_response = None
    for attempt in range(1, retries + 1):
        try:
            _rewind_files(files)
            response = requests.post(f"{RENDER_URL}{path}", headers=headers, timeout=timeout, **kwargs)
            last_response = response
            if response.ok:
                return response
            if response.status_code not in {502, 503, 504}:
                raise RuntimeError(f"{path} returned HTTP {response.status_code}: {response.text[:500]}")
            print(f"{path}: transient HTTP {response.status_code}; retry {attempt}/{retries}")
        except requests.RequestException as exc:
            print(f"{path}: transient request error; retry {attempt}/{retries}: {exc}")
        if attempt < retries:
            time.sleep(min(10 * attempt, 60))
    detail = last_response.text[:500] if last_response is not None else "no response"
    status = last_response.status_code if last_response is not None else "request-error"
    raise RuntimeError(f"{path} failed after {retries} retries: HTTP {status}: {detail}")


if WORKDIR.exists():
    run(["git", "-C", str(WORKDIR), "fetch", "origin", "main"])
    run(["git", "-C", str(WORKDIR), "reset", "--hard", "origin/main"])
else:
    run(["git", "clone", "--depth=1", REPO_URL, str(WORKDIR)])

run([sys.executable, "-m", "pip", "install", "-q", "-r", "backend/requirements.txt"], WORKDIR)
MODEL_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(WORKDIR / "backend"))

from app.db.session import SessionLocal, init_db
from app.db.models import Fixture
from app.services.predictions import dataframe_from_db
from app.ml.train_oof import (
    train_soccer_model_oof,
    train_basketball_model_oof,
    train_generic_sport_model_oof,
)

init_db()

print("\n=== WAIT FOR RENDER ===")
wait_for_render_ready()

print("\n=== PROVIDER HISTORY SYNC ===")
try:
    sync = post("/api/admin/ml/sync-provider-history", params={"days_back": 30}, timeout=600)
    print(sync.json())
except Exception as exc:
    print(f"History sync warning: {exc}")

db = SessionLocal()
try:
    data = dataframe_from_db(db, max_age_days=None)
    source_rows = (
        db.query(Fixture.source, __import__("sqlalchemy").func.count(Fixture.id))
        .filter(Fixture.home_score.isnot(None), Fixture.away_score.isnot(None), Fixture.source != "coverage_seed")
        .group_by(Fixture.source)
        .order_by(__import__("sqlalchemy").func.count(Fixture.id).desc())
        .all()
    )
    coverage_seed_ids = {row[0] for row in db.query(Fixture.id).filter(Fixture.source == "coverage_seed").all()}
finally:
    db.close()

print("\n=== TRAINING DATA PROVENANCE ===")
for source, count in source_rows:
    print(f"{source or 'unknown'}: {int(count):,} completed rows")
api_sources = {"api_football", "sportmonks", "football_data_org", "apifootball_com", "api_basketball", "allsportsapi", "thesportsdb", "bzzoiro", "openfoot"}
api_rows = sum(int(count) for source, count in source_rows if str(source or "").lower() in api_sources)
print(f"API-sourced completed rows: {api_rows:,}")
print("Training policy: hybrid historical DB + freshly synced API history; source provenance is reported before every run.")

if data.empty:
    raise RuntimeError("Neon returned no training data")
if "sport" not in data.columns:
    raise RuntimeError("Training data has no sport column")
if "id" in data.columns and coverage_seed_ids:
    data = data[~data["id"].isin(coverage_seed_ids)].copy()
data["sport"] = data["sport"].astype(str).str.strip().str.lower()
data = data[data["home_score"].notna() & data["away_score"].notna()].copy()
print(f"\nCompleted training rows: {len(data):,}")
print(data.groupby("sport").size().sort_values(ascending=False).to_string())

trainers = {
    "soccer": train_soccer_model_oof,
    "basketball": train_basketball_model_oof,
    "tennis": lambda frame: train_generic_sport_model_oof(frame, "tennis"),
    "american_football": lambda frame: train_generic_sport_model_oof(frame, "american_football"),
    "hockey": lambda frame: train_generic_sport_model_oof(frame, "hockey"),
    "cricket": lambda frame: train_generic_sport_model_oof(frame, "cricket"),
    "rugby": lambda frame: train_generic_sport_model_oof(frame, "rugby"),
    "baseball": lambda frame: train_generic_sport_model_oof(frame, "baseball"),
}

results = []
print("\n=== LEAKAGE-SAFE OOF MODEL TRAINING ===")
for sport, trainer in trainers.items():
    frame = data[data["sport"] == sport].copy()
    if len(frame) < 200:
        print(f"SKIP {sport}: {len(frame):,} completed rows (need 200)")
        continue
    print(f"\nTRAIN {sport}: {len(frame):,} rows")
    started = time.time()
    try:
        result = trainer(frame)
        elapsed = time.time() - started
        print(f"OK {sport}: accuracy={result['accuracy']:.2%}, rows={result['sample_size']:,}, models={','.join(result.get('models_trained', []))}, time={elapsed:.0f}s")
        results.append(result)
    except Exception as exc:
        print(f"FAIL {sport}: {exc}")

if not results:
    raise RuntimeError("No sport model could be trained")

print("\n=== MODEL UPLOAD ===")
wait_for_render_ready()
for result in results:
    path = Path(result["path"])
    if not path.exists():
        print(f"SKIP upload: missing artifact {path}")
        continue
    for upload_attempt in range(1, 4):
        try:
            with path.open("rb") as handle:
                response = post(
                    "/api/admin/upload-model",
                    timeout=300,
                    retries=12,
                    files={"model": (path.name, handle, "application/octet-stream")},
                    data={
                        "sport": result.get("sport") or path.name.split("_")[0],
                        "model_type": result["model_type"],
                        "accuracy": str(result["accuracy"]),
                        "sample_size": str(result["sample_size"]),
                    },
                )
            print(response.json())
            break
        except Exception as exc:
            if upload_attempt == 3:
                raise
            print(f"Upload attempt {upload_attempt}/3 failed; waiting for Render before retry: {exc}")
            wait_for_render_ready()

print("\n=== PRODUCTION REFRESH ===")
for endpoint in ("/api/admin/predict", "/api/admin/backfill-odds", "/api/admin/clear-train-flag"):
    try:
        response = post(endpoint, timeout=300)
        print(endpoint, response.status_code, response.text[:1000])
    except Exception as exc:
        print(endpoint, "warning:", exc)

print("\n=== KAGGLE TRAINING COMPLETE ===")
print(f"Models produced: {len(results)}")
for result in results:
    print(f"- {result.get('sport', 'soccer')}: {result['accuracy']:.2%} on {result['sample_size']:,} rows; method={result.get('training_method', 'oof')}")