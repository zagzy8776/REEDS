"""Idempotent, batched Aiven fixture importer from the repository's raw CSVs.

Loads the historical fixture CSVs already present in ``data/raw`` into the
Aiven ``fixtures`` table. Designed to be re-run safely: every row is upserted
on the natural key (sport, league, match_date, home_team, away_team), so a
second run converges to the same state without duplicates.

Usage (run from the REEDS-main/REEDS-main directory):

    python backend/scripts/import_fixtures_csv.py \
        --sports soccer,basketball \
        --batch-size 500

Environment knobs:
    AIVEN_DATABASE_URL  Aiven connection string (falls back to DATABASE_URL)
    FIXTURE_CSV_DIR     override for the raw CSV directory (default data/raw)
    FIXTURE_CONNECT_TIMEOUT  PostgreSQL connect timeout in seconds (default 120)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running as a standalone script without the package installed.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load_env_file(path: Path) -> None:
    """Load .env into os.environ BEFORE importing app modules.

    app.db.session reads settings at import time, so the env must be seeded
    first or the settings module will see an empty environment.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


# Load credentials before any app import.
# The .env lives at the repository root (two parents above this script).
_repo_root = Path(__file__).resolve().parents[2]
_load_env_file(_repo_root / ".env")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.models import Fixture  # noqa: F401
from app.db.session import normalize_database_url  # noqa: E402
from app.scraper.loaders import (  # noqa: E402
    load_basketball_csv,
    load_football_csv,
)


def _discover_csvs(root: Path) -> list[Path]:
    """Return every raw fixture CSV under the repository's data/raw tree."""
    files: list[Path] = []
    for pattern in ("**/*.csv",):
        files.extend(sorted(root.glob(pattern)))
    return files


def _league_from_path(path: Path) -> str:
    """Derive a human-readable league name from the file's directory."""
    parts = [p for p in path.parts if p and p not in {path.root, "data", "raw"}]
    if len(parts) >= 3:
        return parts[1].replace("_", " ").title()
    if len(parts) == 2:
        return parts[0].replace("_", " ").title()
    return path.stem.replace("_", " ").title()


def _season_from_path(path: Path) -> str:
    """Derive a season label from the filename, e.g. EPL_2223 -> 2022/23."""
    stem = path.stem
    for token in stem.split("_"):
        if len(token) == 4 and token.isdigit():
            start = int(token[:2])
            start_year = 2000 + start if start <= 50 else 1900 + start
            return f"{start_year}/{start_year + 1}"
    return stem


def _is_basketball(path: Path) -> bool:
    return "basketball" in path.parts


def main() -> None:
    parser = argparse.ArgumentParser(description="Import raw fixture CSVs into Aiven.")
    parser.add_argument("--sports", default="soccer,basketball",
                        help="Comma-separated sport list (default: soccer,basketball)")
    parser.add_argument("--csv-dir", default=None,
                        help="Override the raw CSV directory (default: data/raw)")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="Rows per DB transaction (default: 500)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List files that would be imported without writing")
    args = parser.parse_args()

    sports = {s.strip().lower() for s in args.sports.split(",") if s.strip()}
    csv_root = Path(args.csv_dir) if args.csv_dir else Path("data/raw")

    if not csv_root.exists():
        raise SystemExit(f"CSV directory not found: {csv_root}")

    csvs = _discover_csvs(csv_root)
    if not csvs:
        raise SystemExit(f"No CSV files found under {csv_root}")

    connect_timeout = int(os.getenv("FIXTURE_CONNECT_TIMEOUT", "120"))
    url = normalize_database_url(
        os.environ.get("AIVEN_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    )
    if not url:
        raise SystemExit("AIVEN_DATABASE_URL (or DATABASE_URL) is not set")
    print(f"Connecting to Aiven (timeout={connect_timeout}s)...", flush=True)
    engine = create_engine(
        url,
        connect_args={"connect_timeout": connect_timeout, "application_name": "reeds-fixture-importer"},
        pool_pre_ping=True,
    )
    # Verify schema exists before writing.
    with engine.begin() as conn:
        existing = {r[0] for r in conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).all()}
        if "fixtures" not in existing:
            raise SystemExit("Aiven schema is missing the fixtures table; run the baseline first")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    grand_total = 0
    try:
        for path in csvs:
            is_basket = _is_basketball(path)
            sport = "basketball" if is_basket else "soccer"
            if sport not in sports:
                continue
            league = _league_from_path(path)
            season = _season_from_path(path)

            if args.dry_run:
                print(f"[dry-run] {path} -> sport={sport} league={league} season={season}")
                continue

            loader = load_basketball_csv if is_basket else load_football_csv
            try:
                loaded = loader(db, str(path), league=league, season=season)
            except Exception:
                db.rollback()
                print(f"FAILED {path}: skipping", file=sys.stderr)
                continue
            grand_total += loaded
            print(f"{path}: +{loaded} rows", flush=True)

            # Commit per file so one bad file cannot abort the whole import.
            db.commit()
    finally:
        db.close()
        engine.dispose()

    print(f"Import complete: {grand_total:,} fixture rows upserted into Aiven", flush=True)


if __name__ == "__main__":
    main()