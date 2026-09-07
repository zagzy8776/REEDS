"""REEDS Kaggle training worker.

Run this notebook-side script when a fresh production model is needed.
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

# Kaggle is an external trainer, not the production API.
os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["APP_ENV"] = "production"
os.environ["MODEL_DIR"] = str(MODEL_DIR)
os.environ["MIN_TRAINING_ROWS"] = "200"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def post(path: str, *, timeout: int = 180, **kwargs):
    headers = {"X-Admin-Key": ADMIN_API_KEY}
    headers.update(kwargs.pop("headers", {}))
    response = requests.post(f"{RENDER_URL}{path}", headers=headers, timeout=timeout, **kwargs)
    if not response.ok:
        raise RuntimeError(f"{path} returned HTTP {response.status_code}: {response.text[:500]}")
    return response


# 1. Fresh REEDS source.
if WORKDIR.exists():
    run(["git", "-C", str(WORKDIR), "fetch", "origin", "main"])
    run(["git", "-C", str(WORKDIR), "reset", "--hard", "origin/main"])
else:
    run(["git", "clone", "--depth=1", REPO_URL, str(WORKDIR)])

# 2. Install the same ML stack used by REEDS.
run([sys.executable, "-m", "pip", "install", "-q", "-r", "backend/requirements.txt"], WORKDIR)
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# Kaggle sessions are finite. The production trainer supports a deliberately
# faster large-history path; use it here so a first production training run is
# reliable instead of spending most of the session on dozens of Optuna fits.
# This modifies only Kaggle's working clone; GitHub production code is untouched.
train_file = WORKDIR / "backend" / "app" / "ml" / "train.py"
train_text = train_file.read_text()
train_text = train_text.replace("if len(X) >= 60000:", "if len(X) >= 20000:")
train_text = train_text.replace("n_trials=15", "n_trials=3")
train_text = train_text.replace("n_trials=10", "n_trials=3")
train_file.write_text(train_text)
print("Kaggle runtime optimization: large-history fast path + 3 Optuna trials")

sys.path.insert(0, str(WORKDIR / "backend"))

from app.db.session import SessionLocal, init_db
from app.services.predictions import dataframe_from_db
from app.ml.train import (
    train_soccer_model,
    train_basketball_model,
    train_generic_sport_model,
)

init_db()

# 3. Refresh recent completed provider history in Neon before training.
print("\n=== PROVIDER HISTORY SYNC ===")
try:
    sync = post("/api/admin/ml/sync-provider-history", params={"days_back": 7}, timeout=300)
    print(sync.json())
except Exception as exc:
    # Existing Neon history can still be used; don't destroy a usable training run.
    print(f"History sync warning: {exc}")

# 4. Snapshot all completed Neon fixtures.
db = SessionLocal()
try:
    data = dataframe_from_db(db, max_age_days=None)
finally:
    db.close()

if data.empty:
    raise RuntimeError("Neon returned no training data")

if "sport" not in data.columns:
    raise RuntimeError("Training data has no sport column")

data["sport"] = data["sport"].astype(str).str.strip().str.lower()
data = data[data["home_score"].notna() & data["away_score"].notna()].copy()
print(f"\nCompleted training rows: {len(data):,}")
print(data.groupby("sport").size().sort_values(ascending=False).to_string())

# 5. Train using the production REEDS trainers.
trainers = {
    "soccer": train_soccer_model,
    "basketball": train_basketball_model,
    "tennis": lambda frame: train_generic_sport_model(frame, "tennis"),
    "american_football": lambda frame: train_generic_sport_model(frame, "american_football"),
    "hockey": lambda frame: train_generic_sport_model(frame, "hockey"),
    "cricket": lambda frame: train_generic_sport_model(frame, "cricket"),
    "rugby": lambda frame: train_generic_sport_model(frame, "rugby"),
    "baseball": lambda frame: train_generic_sport_model(frame, "baseball"),
}

results = []
print("\n=== MODEL TRAINING ===")
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
        print(f"OK {sport}: accuracy={result['accuracy']:.2%}, rows={result['sample_size']:,}, time={elapsed:.0f}s")
        results.append(result)
    except Exception as exc:
        print(f"FAIL {sport}: {exc}")

if not results:
    raise RuntimeError("No sport model could be trained")

# 6. Upload only the slim production bundles to Render.
print("\n=== MODEL UPLOAD ===")
for result in results:
    path = Path(result["path"])
    if not path.exists():
        print(f"SKIP upload: missing artifact {path}")
        continue
    with path.open("rb") as handle:
        response = post(
            "/api/admin/upload-model",
            timeout=300,
            files={"model": (path.name, handle, "application/octet-stream")},
            data={
                "sport": result["sport"] if "sport" in result else path.name.split("_")[0],
                "model_type": result["model_type"],
                "accuracy": str(result["accuracy"]),
                "sample_size": str(result["sample_size"]),
            },
        )
    print(response.json())

# 7. Recompute production predictions and fill missing fair-value odds.
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
    print(f"- {result.get('sport', 'soccer')}: {result['accuracy']:.2%} on {result['sample_size']:,} rows")
