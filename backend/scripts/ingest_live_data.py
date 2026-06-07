import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db.session import SessionLocal, init_db
from app.scraper.loaders import ingest_api_basketball_games, ingest_api_football_fixtures


def date_window(days: int) -> list[str]:
    return [(date.today() + timedelta(days=offset)).isoformat() for offset in range(max(days, 1))]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest real upcoming football/basketball fixtures and odds from configured APIs.")
    parser.add_argument("--days", type=int, default=7, help="Number of calendar days to ingest starting today. Default: 7")
    parser.add_argument("--sport", choices=["all", "soccer", "basketball"], default="all")
    parser.add_argument("--skip-odds", action="store_true", help="Skip API-Football odds ingestion.")
    args = parser.parse_args()

    settings = get_settings()
    football_key = settings.api_football_key or settings.api_sports_key
    basketball_key = settings.api_basketball_key or settings.api_sports_key
    dates = date_window(args.days)

    init_db()
    db = SessionLocal()
    try:
        result = {"dates": dates, "soccer": 0, "basketball": 0, "skipped": []}
        if args.sport in {"all", "soccer"}:
            if football_key:
                result["soccer"] = ingest_api_football_fixtures(db, football_key, dates, include_odds=not args.skip_odds)
            else:
                result["skipped"].append({"sport": "soccer", "reason": "API_FOOTBALL_KEY or API_SPORTS_KEY not configured"})
        if args.sport in {"all", "basketball"}:
            if basketball_key:
                result["basketball"] = ingest_api_basketball_games(db, basketball_key, dates)
            else:
                result["skipped"].append({"sport": "basketball", "reason": "API_BASKETBALL_KEY or API_SPORTS_KEY not configured"})
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()