"""REEDS P0 HISTORICAL BACKFILL ONLY — Kaggle worker.

Downloads FDCO CSVs (football-data.co.uk) and loads genuinely completed
historical soccer fixtures into Neon via the existing loader pipeline.

THIS IS P0 ONLY:
  - NO HistoricalEvaluation
  - NO evidence_pivot / market_gate / MarketEvidence writes
  - NO model training or activation
  - NO prediction/publish-gate changes
  - NO scheduled-engine coupling

Season codes are explicit and VERIFIED against football-data.co.uk HTTP 200:
  2425,2324,2223,2122,2021,1920,1819,1718   (2122 == 2021/22; "2221" is INVALID)

Secrets (Kaggle Add-ons -> Secrets, or env vars): DATABASE_URL
Optional (verify only): ADMIN_API_KEY, RENDER_URL
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import requests

REPO_URL = "https://github.com/zagzy8776/REEDS.git"
WORKDIR = Path("/kaggle/working/REEDS")
DEFAULT_LEAGUES = "E0,E1,E2,E3,SP1,D1,D2,I1,I2,F1,F2"
DEFAULT_SEASONS = "2425,2324,2223,2122,2021,1920,1819,1718"


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
if not DATABASE_URL or not DATABASE_URL.startswith("postgres"):
    raise RuntimeError("Missing DATABASE_URL Kaggle secret (postgres://...)")

os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["APP_ENV"] = "production"
os.environ["FCDO_LEAGUES"] = os.getenv("FCDO_LEAGUES", DEFAULT_LEAGUES)
os.environ["FCDO_SEASONS"] = os.getenv("FCDO_SEASONS", DEFAULT_SEASONS)

# Never log secret values.
assert "DATABASE_URL" not in " ".join(sys.argv)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


print("=== GIT ===", flush=True)
if WORKDIR.exists():
    run(["git", "-C", str(WORKDIR), "fetch", "origin", "main"])
    run(["git", "-C", str(WORKDIR), "reset", "--hard", "origin/main"])
else:
    run(["git", "clone", "--depth=1", REPO_URL, str(WORKDIR)])

BACKEND = WORKDIR / "backend"

print("=== DEPENDENCIES (P0-only: no sklearn/joblib) ===", flush=True)
run([sys.executable, "-m", "pip", "install", "-q",
     "pandas", "SQLAlchemy==2.0.36", "psycopg[binary]==3.2.3",
     "pydantic-settings==2.7.1", "python-dotenv", "requests"], BACKEND)

sys.path.insert(0, str(BACKEND))

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.db.models import Fixture, Team  # noqa: E402
from sqlalchemy import func  # noqa: E402

init_db()
db = SessionLocal()

TODAY = date.today()


def snapshot(label: str) -> dict:
    totals = (
        db.query(Fixture.sport, func.count(Fixture.id))
        .group_by(Fixture.sport).all()
    )
    soccer = db.query(func.count(Fixture.id)).filter(Fixture.sport == "soccer").scalar() or 0
    soccer_completed = (
        db.query(func.count(Fixture.id))
        .filter(Fixture.sport == "soccer", Fixture.home_score.isnot(None), Fixture.away_score.isnot(None))
        .scalar() or 0
    )
    soccer_upcoming = (
        db.query(func.count(Fixture.id))
        .filter(Fixture.sport == "soccer", Fixture.match_date > TODAY)
        .scalar() or 0
    )
    soccer_today_pending = (
        db.query(func.count(Fixture.id))
        .filter(Fixture.sport == "soccer", Fixture.match_date == TODAY,
                Fixture.home_score.is_(None), Fixture.away_score.is_(None))
        .scalar() or 0
    )
    snap = {
        "label": label,
        "total_soccer_fixtures": soccer,
        "soccer_completed": soccer_completed,
        "soccer_upcoming": soccer_upcoming,
        "soccer_live_or_today_pending": soccer_today_pending,
    }
    print(f"\n[{label}]", flush=True)
    for k, v in snap.items():
        print(f"  {k}: {v}", flush=True)
    return snap


before = snapshot("BEFORE P0")

print("\n=== P0: RUN FDCO BACKFILL (download + load) ===", flush=True)
try:
    subprocess.run(
        [sys.executable, "scripts/backfill_fcdo.py"],
        cwd=str(BACKEND), check=True,
    )
except subprocess.CalledProcessError as exc:
    print(f"backfill_fcdo exited {exc.returncode}; continuing with whatever loaded", flush=True)

after = snapshot("AFTER P0")

print("\n=== DATA QUALITY ===", flush=True)
soccer = db.query(Fixture).filter(Fixture.sport == "soccer").all()
inserted = after["total_soccer_fixtures"] - before["total_soccer_fixtures"]
published_sources = {"coverage_seed"}
existing = 0
for fx in soccer:
    src = str(fx.source or "")
    if src not in published_sources and src not in {"api_football", "sportmonks", "allsportsapi",
                                                     "thesportsdb", "bzzoiro", "openfoot", "web_score_sources"}:
        existing += 0
with_scores = sum(1 for fx in soccer if fx.home_score is not None and fx.away_score is not None)
with_odds = sum(1 for fx in soccer if fx.home_odds and fx.draw_odds and fx.away_odds)
without_odds = sum(1 for fx in soccer if not (fx.home_odds and fx.draw_odds and fx.away_odds))

# Duplicate detection on the natural (sport, match_date, home, away) key, ignoring league
dup_key = {}
for fx in soccer:
    dup_key.setdefault((fx.sport, fx.match_date, fx.home_team, fx.away_team), 0)
    dup_key[(fx.sport, fx.match_date, fx.home_team, fx.away_team)] += 1
duplicates = sum(1 for v in dup_key.values() if v > 1)

dates = [fx.match_date for fx in soccer if fx.match_date]
seasons = sorted({fx.season for fx in soccer if str(fx.season).isdigit()})
leagues = sorted({fx.league for fx in soccer})
team_count = db.query(func.count(Team.id)).filter(Team.sport == "soccer").scalar() or 0

print(f"  soccer fixtures AFTER:  {after['total_soccer_fixtures']:,}", flush=True)
print(f"  before:                 {before['total_soccer_fixtures']:,}", flush=True)
print(f"  inserted/new:           {inserted:,}", flush=True)
print(f"  existing/upserted:      {after['total_soccer_fixtures'] - inserted:,}", flush=True)
print(f"  completed:              {with_scores:,}", flush=True)
print(f"  with real scores:       {with_scores:,}", flush=True)
print(f"  with pre-match odds:    {with_odds:,}", flush=True)
print(f"  without odds:           {without_odds:,}", flush=True)
print(f"  duplicate natural keys: {duplicates}", flush=True)
print(f"  earliest date:          {min(dates) if dates else 'n/a'}", flush=True)
print(f"  latest date:            {max(dates) if dates else 'n/a'}", flush=True)
print(f"  seasons loaded:         {len(seasons)} -> {seasons}", flush=True)
print(f"  leagues loaded:         {len(leagues)}", flush=True)
print(f"  team count (soccer):    {team_count:,}", flush=True)

print("\n=== SAFETY (current/upcoming/live universe) ===", flush=True)
print(f"  upcoming before -> after:         {before['soccer_upcoming']} -> {after['soccer_upcoming']}", flush=True)
print(f"  today-pending/live before -> after:{before['soccer_live_or_today_pending']} -> {after['soccer_live_or_today_pending']}", flush=True)
print(f"  completed before -> after:        {before['soccer_completed']} -> {after['soccer_completed']}", flush=True)

print("\n=== FEATURE-CONTEXT TEST: 10 upcoming soccer fixtures ===", flush=True)
from app.services.predictions import dataframe_from_db  # noqa: E402
from app.ml.features import features_for_fixture  # noqa: E402

upcoming = (
    db.query(Fixture)
    .filter(Fixture.sport == "soccer", Fixture.match_date > TODAY)
    .order_by(Fixture.match_date.asc())
    .limit(10)
    .all()
)
if not upcoming:
    print("  WARNING: no upcoming soccer fixtures found to test against", flush=True)

# Two windows so we can compare: default live path (90d) vs full corpus (None)
for window_name, window in (("live-path-90d", 90), ("full-corpus", None)):
    history = dataframe_from_db(db, max_age_days=window)
    populated = 0
    print(f"\n  --- history window: {window_name} (rows={len(history):,}) ---", flush=True)
    for fx in upcoming:
        context = features_for_fixture(
            history, fx.home_team, fx.away_team,
            fixture_date=fx.match_date, league=fx.league,
            home_odds=fx.home_odds, draw_odds=fx.draw_odds, away_odds=fx.away_odds,
        )
        nz = sum(1 for k, v in context.items() if isinstance(v, (int, float)) and v != 0)
        zero = sum(1 for k, v in context.items() if isinstance(v, (int, float)) and v == 0)
        has_form = context.get("form") is not None or context.get("home_form_streak") not in (None, 0) or "elo" in " ".join(context.keys())
        print(
            f"  id={fx.id} {fx.home_team} vs {fx.away_team} {fx.match_date} | "
            f"nz={nz} zero={zero} history_based={'yes' if has_form else 'NO'}",
            flush=True,
        )
        if has_form:
            populated += 1
    print(f"  fixtures with populated history-based features: {populated}/{len(upcoming)}", flush=True)

print("\n=== CODE CHECKS (run in this Kaggle environment) ===", flush=True)
run([sys.executable, "-m", "compileall", "-q", "backend"], WORKDIR)

# Tests that are runnable with only pydantic-settings+sqlalchemy+pandas deps
# (fastapi/sklearn/joblib tests are excluded — environment cannot install them).
run([sys.executable, "-m", "pip", "install", "-q", "pytest"], WORKDIR)
try:
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         "tests/test_market_gate.py", "tests/test_evidence_pivot.py", "tests/test_redis_cache.py"],
        cwd=str(BACKEND), check=False,
    )
except Exception as exc:  # noqa: BLE001
    print(f"pytest error: {exc}", flush=True)

print("\n=== P0 BACKFILL COMPLETE (STOPPING — did NOT continue into P1) ===", flush=True)