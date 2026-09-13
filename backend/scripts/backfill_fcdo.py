import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from download_historical_data import FOOTBALL_DATA_LEAGUES, download_football_data
from app.db.session import SessionLocal, init_db
from app.scraper.loaders import load_football_csv

DEFAULT_LEAGUES = "E0,E1,E2,E3,SP1,D1,D2,I1,I2,F1,F2"
DEFAULT_SEASONS = "2425,2324,2223,2122,2021,1920,1819,1718"


def season_start_year(code: str) -> int:
    if len(code) != 4 or not code.isdigit():
        raise ValueError(f"season code must be 4 digits (e.g. 2425), got '{code}'")
    prefix = int(code[:2])
    return 2000 + prefix if prefix <= 50 else 1900 + prefix


def main() -> None:
    leagues = [c.strip().upper() for c in os.getenv("FCDO_LEAGUES", DEFAULT_LEAGUES).split(",") if c.strip()]
    seasons = [s.strip().lower() for s in os.getenv("FCDO_SEASONS", DEFAULT_SEASONS).split(",") if s.strip()]

    valid = set(FOOTBALL_DATA_LEAGUES.values())
    bad = [c for c in leagues if c not in valid]
    if bad:
        raise SystemExit(f"Unknown FCDO league code(s): {', '.join(bad)}. Valid: {', '.join(sorted(valid))}")

    name_for = {code: name for name, code in FOOTBALL_DATA_LEAGUES.items()}
    output = Path("data/raw")
    output.mkdir(parents=True, exist_ok=True)

    print(f"FCDO backfill: {len(leagues)} leagues x {len(seasons)} seasons -> {output.resolve()}", flush=True)
    downloaded: list[Path] = []
    for season in seasons:
        try:
            year = season_start_year(season)
        except ValueError as exc:
            print(f"skip '{season}': {exc}", flush=True)
            continue
        downloaded.extend(download_football_data(year, year + 1, output, [name_for[c] for c in leagues]))

    if not downloaded:
        raise SystemExit("No FCDO files downloaded; check network access to football-data.co.uk")

    init_db()
    db = SessionLocal()
    grand = 0
    try:
        for path in downloaded:
            name, _, code = path.stem.partition("_")
            n = load_football_csv(db, str(path), league=name, season=code)
            grand += n
            print(f"{path.name}: +{n} rows", flush=True)
    finally:
        db.close()

    print(f"Loaded {grand:,} historical rows total", flush=True)
    print("Verify at: GET https://reeds-phj1.onrender.com/api/admin/job-status  (db_soccer_rows)", flush=True)


if __name__ == "__main__":
    main()