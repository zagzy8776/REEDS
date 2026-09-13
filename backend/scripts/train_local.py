#!/usr/bin/env python
"""
Local training script — run this on your own machine.

Usage:
    cd e:\REEDS-main\REEDS\backend
    pip install -r requirements.txt          (first time only)
    python scripts/train_local.py

What it does:
  1. Connects to your Neon DB (reads DATABASE_URL from .env)
  2. Downloads free historical data if not enough rows (football-data.co.uk etc.)
  3. Trains all sport models using your FULL local RAM (no 512MB limit)
  4. Saves .joblib files to data/models/
  5. Uploads each model to Render via POST /api/admin/upload-model

Your laptop has 8-32GB RAM vs Render's 512MB.
This will complete in 3-5 minutes and massively improve accuracy.
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path

# Make sure we can import from the backend package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

RENDER_URL = os.environ.get("RENDER_URL", "https://reeds-phj1.onrender.com")
ADMIN_KEY  = os.environ.get("ADMIN_API_KEY", "23235567Jjmt.")


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def main():
    parser = argparse.ArgumentParser(description="Train LOYAL EDGE models locally")
    parser.add_argument("--ingest", action="store_true", help="Download free data first")
    parser.add_argument("--sports", default="all", help="Sports to train (all, soccer, basketball, tennis...)")
    parser.add_argument("--upload", action="store_true", default=True, help="Upload models to Render after training")
    parser.add_argument("--no-upload", dest="upload", action="store_false")
    args = parser.parse_args()

    print_section("LOYAL EDGE — Local Training Pipeline")
    print(f"  Database: {os.environ.get('DATABASE_URL', 'NOT SET')[:40]}...")
    print(f"  Render:   {RENDER_URL}")
    print(f"  Upload:   {args.upload}")

    # ------------------------------------------------------------------ #
    # 1. Connect to database                                               #
    # ------------------------------------------------------------------ #
    print_section("Step 1: Connecting to database")
    from app.db.session import SessionLocal, init_db
    init_db()
    db = SessionLocal()
    print("  Connected.")

    # ------------------------------------------------------------------ #
    # 2. Optional: download free data                                      #
    # ------------------------------------------------------------------ #
    if args.ingest:
        print_section("Step 2: Downloading free historical data")
        print("  This downloads football-data.co.uk, ATP/WTA tennis, NBA, NFL, NHL, IPL...")
        print("  Takes ~5 minutes. You can skip with --no-ingest if DB is already loaded.")
        from app.scraper.free_data import ingest_all_free_sources
        result = ingest_all_free_sources(db, max_leagues=21)
        for source, r in result.items():
            total = r.get("total", 0) if isinstance(r, dict) else 0
            errors = len(r.get("errors", [])) if isinstance(r, dict) else 0
            print(f"  {source}: {total} rows loaded, {errors} errors")
    else:
        print_section("Step 2: Checking existing data")

    # ------------------------------------------------------------------ #
    # 3. Load training data                                                #
    # ------------------------------------------------------------------ #
    print_section("Step 3: Loading training data")
    from app.services.predictions import dataframe_from_db
    data = dataframe_from_db(db, max_age_days=None)
    print(f"  Total rows: {len(data)}")
    if "sport" in data.columns:
        for sport, count in data.groupby("sport").size().sort_values(ascending=False).items():
            completed = data[(data["sport"] == sport) & data["home_score"].notna()].shape[0]
            print(f"    {sport}: {count} total, {completed} completed")

    # ------------------------------------------------------------------ #
    # 4. Train models                                                      #
    # ------------------------------------------------------------------ #
    print_section("Step 4: Training models")
    from app.ml.train import train_soccer_model, train_basketball_model, train_generic_sport_model
    from app.services.model_registry import register_model

    trained = []
    errors = []
    target_sports = args.sports.split(",") if args.sports != "all" else None

    def should_train(sport: str) -> bool:
        if target_sports is None:
            return True
        return sport in target_sports

    # Soccer
    if should_train("soccer"):
        try:
            soccer_data = data[data["sport"] == "soccer"].copy()
            print(f"\n  Training soccer on {len(soccer_data)} rows...")
            t0 = time.time()
            result = train_soccer_model(soccer_data)
            elapsed = time.time() - t0
            mv = register_model(db, "soccer", result["model_type"], result["path"],
                                result["accuracy"], result["sample_size"])
            print(f"  Soccer done in {elapsed:.0f}s: {result['accuracy']:.1%} accuracy on {result['sample_size']} rows")
            print(f"  Model saved: {result['path']}")
            trained.append({"sport": "soccer", "path": result["path"], **result})
        except Exception as e:
            print(f"  Soccer FAILED: {e}")
            errors.append({"sport": "soccer", "error": str(e)})

    # Basketball
    if should_train("basketball"):
        try:
            bball_data = data[data["sport"] == "basketball"].copy()
            print(f"\n  Training basketball on {len(bball_data)} rows...")
            t0 = time.time()
            result = train_basketball_model(bball_data)
            elapsed = time.time() - t0
            mv = register_model(db, "basketball", result["model_type"], result["path"],
                                result["accuracy"], result["sample_size"])
            print(f"  Basketball done in {elapsed:.0f}s: {result['accuracy']:.1%} accuracy on {result['sample_size']} rows")
            trained.append({"sport": "basketball", "path": result["path"], **result})
        except Exception as e:
            print(f"  Basketball FAILED: {e}")
            errors.append({"sport": "basketball", "error": str(e)})

    # Other sports
    for sport in ("tennis", "american_football", "hockey", "cricket", "rugby", "baseball"):
        if not should_train(sport):
            continue
        try:
            sport_data = data[data["sport"] == sport].copy()
            if len(sport_data) < 200:
                print(f"\n  {sport}: only {len(sport_data)} rows — skipping (need 200+)")
                continue
            print(f"\n  Training {sport} on {len(sport_data)} rows...")
            t0 = time.time()
            result = train_generic_sport_model(sport_data, sport)
            elapsed = time.time() - t0
            mv = register_model(db, sport, result["model_type"], result["path"],
                                result["accuracy"], result["sample_size"])
            print(f"  {sport} done in {elapsed:.0f}s: {result['accuracy']:.1%} accuracy on {result['sample_size']} rows")
            trained.append({"sport": sport, "path": result["path"], **result})
        except Exception as e:
            print(f"  {sport} FAILED: {e}")
            errors.append({"sport": sport, "error": str(e)})

    db.close()

    # ------------------------------------------------------------------ #
    # 5. Summary                                                           #
    # ------------------------------------------------------------------ #
    print_section("Training Summary")
    print(f"  Trained: {len(trained)} models")
    for t in trained:
        print(f"    {t['sport']}: {t.get('accuracy', 0):.1%} accuracy on {t.get('sample_size', 0)} rows")
    if errors:
        print(f"  Errors: {len(errors)}")
        for e in errors:
            print(f"    {e['sport']}: {e['error']}")

    # ------------------------------------------------------------------ #
    # 6. Upload models to Render                                           #
    # ------------------------------------------------------------------ #
    if args.upload and trained:
        print_section("Step 5: Uploading models to Render")
        import requests
        uploaded = 0
        for t in trained:
            model_path = t["path"]
            if not Path(model_path).exists():
                print(f"  {t['sport']}: file not found at {model_path}")
                continue
            try:
                print(f"  Uploading {t['sport']} ({Path(model_path).stat().st_size / 1024 / 1024:.1f}MB)...")
                with open(model_path, "rb") as f:
                    resp = requests.post(
                        f"{RENDER_URL}/api/admin/upload-model",
                        headers={"x-admin-key": ADMIN_KEY},
                        files={"model": (Path(model_path).name, f, "application/octet-stream")},
                        data={
                            "sport":       t["sport"],
                            "model_type":  t.get("model_type", "random_forest"),
                            "accuracy":    str(t.get("accuracy", 0)),
                            "sample_size": str(t.get("sample_size", 0)),
                        },
                        timeout=120,
                    )
                if resp.status_code == 200:
                    result = resp.json()
                    print(f"  {t['sport']}: uploaded and activated on Render ✅")
                    uploaded += 1
                else:
                    print(f"  {t['sport']}: upload failed ({resp.status_code}): {resp.text[:100]}")
            except Exception as e:
                print(f"  {t['sport']}: upload error: {e}")

        print(f"\n  Uploaded {uploaded}/{len(trained)} models to Render")

        # Trigger prediction regeneration
        if uploaded > 0:
            try:
                print("\n  Regenerating predictions with new models...")
                resp = requests.post(
                    f"{RENDER_URL}/api/admin/predict",
                    headers={"x-admin-key": ADMIN_KEY},
                    timeout=30,
                )
                if resp.ok:
                    print(f"  Predictions regenerated: {resp.json().get('generated', '?')} picks")
            except Exception:
                print("  (Prediction regeneration will happen on next scheduler run)")

    print_section("Done!")
    print("  Check your live model stats at:")
    print(f"  {RENDER_URL}/api/stats/backtest")
    print()


if __name__ == "__main__":
    main()
