import argparse
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.scraper.loaders import load_football_csv
from download_historical_data import download_football_data


TIER_1_LEAGUES = ["EPL", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1"]
HIGH_SCORING_LEAGUES = ["EREDIVISIE", "BELGIUM", "PORTUGAL"]
UK_VOLUME_LEAGUES = ["CHAMPIONSHIP", "LEAGUE_ONE", "LEAGUE_TWO"]
EURO_DEPTH_LEAGUES = ["LIGUE_2", "BUNDESLIGA_2", "SERIE_B"]
GLOBAL_MARKET_LEAGUES = [
    *TIER_1_LEAGUES,
    *HIGH_SCORING_LEAGUES,
    *UK_VOLUME_LEAGUES,
    *EURO_DEPTH_LEAGUES,
    "SCOTLAND",
    "GREECE",
]


def season_from_path(path: Path) -> str:
    return path.stem.split("_")[-1]


def feed_files(paths: list[Path]) -> dict:
    print({"feed_start": len(paths)}, flush=True)
    init_db()
    db = SessionLocal()
    totals = {"files": 0, "rows": 0, "by_league": {}}
    try:
        for path in paths:
            league = path.parent.name.upper()
            print({"feeding": str(path), "league": league}, flush=True)
            loaded = load_football_csv(db, str(path), league=league, season=season_from_path(path))
            totals["files"] += 1
            totals["rows"] += loaded
            totals["by_league"][league] = totals["by_league"].get(league, 0) + loaded
            print({"fed": str(path), "league": league, "loaded": loaded}, flush=True)
    finally:
        db.close()
    return totals


def run_admin_backtest(api_url: str, admin_key: str | None) -> None:
    if not admin_key:
        print({"backtest": "skipped", "reason": "ADMIN_API_KEY not provided"})
        return
    subprocess.run([
        "curl",
        "-sS",
        "-X",
        "POST",
        f"{api_url.rstrip('/')}/api/admin/backtest",
        "-H",
        f"X-Admin-Key: {admin_key}",
    ], check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and feed focused soccer data for model growth.")
    parser.add_argument("--phase", choices=["tier1", "high_scoring", "uk_volume", "euro_depth", "global", "all"], default="tier1")
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--end-year", type=int, default=2026, help="Exclusive end year. 2026 means through 2025/26 if provider publishes it.")
    parser.add_argument("--output-dir", default="data/raw")
    parser.add_argument("--run-backtest", action="store_true")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--admin-key", default=None)
    args = parser.parse_args()

    start_year = args.start_year if args.start_year is not None else 2015 if args.phase == "global" else 2020
    leagues = []
    if args.phase in {"tier1", "all"}:
        leagues.extend(TIER_1_LEAGUES)
    if args.phase in {"high_scoring", "all"}:
        leagues.extend(HIGH_SCORING_LEAGUES)
    if args.phase in {"uk_volume", "all"}:
        leagues.extend(UK_VOLUME_LEAGUES)
    if args.phase in {"euro_depth", "all"}:
        leagues.extend(EURO_DEPTH_LEAGUES)
    if args.phase == "global":
        leagues.extend(GLOBAL_MARKET_LEAGUES)

    leagues = list(dict.fromkeys(leagues))
    print({"download_start": {"phase": args.phase, "start_year": start_year, "end_year": args.end_year, "leagues": leagues}}, flush=True)
    downloaded = download_football_data(start_year, args.end_year, Path(args.output_dir), leagues)
    print({"download_done": len(downloaded)}, flush=True)
    totals = feed_files(downloaded)
    print({"phase": args.phase, "downloaded": len(downloaded), "fed": totals}, flush=True)
    if args.run_backtest:
        run_admin_backtest(args.api_url, args.admin_key)


if __name__ == "__main__":
    main()