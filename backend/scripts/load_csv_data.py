import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.scraper.loaders import load_football_csv


parser = argparse.ArgumentParser()
parser.add_argument("--path", required=True)
parser.add_argument("--league", default="Unknown")
parser.add_argument("--season", default="Unknown")
args = parser.parse_args()

init_db()
db = SessionLocal()
try:
    print({"loaded": load_football_csv(db, args.path, args.league, args.season)})
finally:
    db.close()
