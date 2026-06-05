import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.scraper.loaders import load_basketball_csv, load_football_csv


parser = argparse.ArgumentParser()
parser.add_argument("--sport", choices=["soccer", "basketball"], default="soccer")
parser.add_argument("--path", required=True)
parser.add_argument("--league", default="Unknown")
parser.add_argument("--season", default="Unknown")
args = parser.parse_args()

init_db()
db = SessionLocal()
try:
    if args.sport == "basketball":
        loaded = load_basketball_csv(db, args.path, args.league, args.season)
    else:
        loaded = load_football_csv(db, args.path, args.league, args.season)
    print({"sport": args.sport, "loaded": loaded})
finally:
    db.close()
