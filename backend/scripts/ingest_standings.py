"""CLI runner for standings ingestion from all configured providers.

Usage:
  python backend/scripts/ingest_standings.py --league "EPL" --season "2024" --date today
  python backend/scripts/ingest_standings.py --league "EPL" --season "2024" --from 2024-08-01 --to 2024-12-01
  python backend/scripts/ingest_standings.py --all --season "2024" --date today
  python backend/scripts/ingest_standings.py --league "EPL" --season "2024" --date today --providers afriscores espn

Providers (fallback priority): football_data_csv -> afriscores -> espn -> flashscore
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from datetime import date, datetime, timedelta

from app.db.session import SessionLocal, init_db
from app.services.standings_service import StandingsIngestor


def _load_env() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_file = repo_root / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_date(s: str) -> date:
    if s == "today":
        return date.today()
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(description="Ingest standings from all providers")
    parser.add_argument("--league", help="League name (e.g. 'EPL', 'La Liga') or 'all'")
    parser.add_argument("--season", required=True, help="Season identifier (e.g. '2024', '2024/25')")
    parser.add_argument("--sport", default="soccer")
    parser.add_argument("--date", help="Single effective date (YYYY-MM-DD or 'today')")
    parser.add_argument("--from", dest="from_date", help="Start date for bulk ingestion")
    parser.add_argument("--to", dest="to_date", help="End date for bulk ingestion")
    parser.add_argument("--providers", nargs="*", default=None, help="Specific providers to use")
    parser.add_argument("--dry-run", action="store_true", help="List what would be ingested without writing")
    args = parser.parse_args()

    if not args.league:
        parser.error("--league is required (use 'all' for every league in the registry)")

    init_db()
    db = SessionLocal()
    ingestor = StandingsIngestor(db)

    try:
        if args.dry_run:
            _print_supported(db, ingestor, args.sport, args.league)
            return

        if args.date:
            d = parse_date(args.date)
            if args.league == "all":
                _ingest_all_leagues(ingestor, args.sport, args.season, d, args.providers)
            else:
                count = ingestor.ingest_standings(
                    sport=args.sport, league=args.league, season=args.season,
                    target_date=d, providers=args.providers,
                )
                print(f"Ingested {count} standing rows for {args.league} {args.season} as of {d}")
        elif args.from_date and args.to_date:
            start = parse_date(args.from_date)
            end = parse_date(args.to_date)
            if args.league == "all":
                _bulk_ingest_all(ingestor, args.sport, args.season, start, end, args.providers)
            else:
                count = ingestor.bulk_ingest_standings(
                    sport=args.sport, league=args.league, season=args.season,
                    start_date=start, end_date=end, providers=args.providers,
                )
                print(f"Ingested {count} total standing rows for {args.league} {args.season} from {start} to {end}")
        else:
            parser.error("Must provide --date or both --from and --to")
    finally:
        db.close()


def _print_supported(db, ingestor, sport, league_filter):
    for provider in ingestor.providers:
        leagues = provider.get_supported_leagues(sport)
        if league_filter != "all":
            leagues = [l for l in leagues if league_filter.lower() in l.lower()]
        print(f"{provider.provider_name}: {len(leagues)} leagues")


def _ingest_all_leagues(ingestor, sport, season, target_date, providers):
    total = 0
    for provider in ingestor.providers:
        for league in provider.get_supported_leagues(sport):
            count = ingestor.ingest_standings(
                sport=sport, league=league, season=season,
                target_date=target_date, providers=[provider.provider_name],
            )
            if count:
                print(f"  {provider.provider_name}/{league}: {count} rows")
                total += count
    print(f"Total: {total} standing rows")


def _bulk_ingest_all(ingestor, sport, season, start, end, providers):
    total = 0
    for provider in ingestor.providers:
        for league in provider.get_supported_leagues(sport):
            count = ingestor.bulk_ingest_standings(
                sport=sport, league=league, season=season,
                start_date=start, end_date=end, providers=[provider.provider_name],
            )
            if count:
                print(f"  {provider.provider_name}/{league}: {count} rows")
                total += count
    print(f"Total: {total} standing rows")


if __name__ == "__main__":
    main()
