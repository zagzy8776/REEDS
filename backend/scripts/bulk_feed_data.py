import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.scraper.loaders import load_basketball_csv, load_football_csv


def infer_sport(path: Path) -> str:
    lowered = str(path).lower()
    if "basket" in lowered or "nba" in lowered or "euroleague" in lowered:
        return "basketball"
    return "soccer"


def infer_league(path: Path, sport: str) -> str:
    parts = [p.upper() for p in path.parts]
    if sport == "basketball":
        if "NBA" in parts:
            return "NBA"
        if "EUROLEAGUE" in parts:
            return "Euroleague"
        return "Basketball"
    known = ["EPL", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "CHAMPIONSHIP"]
    for item in known:
        if item in parts or item.lower() in str(path).lower():
            return item
    return "Football"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk feed CSV files from data/raw into the LOYAL EDGE database.")
    parser.add_argument("--root", default="data/raw")
    parser.add_argument("--sport", choices=["auto", "soccer", "basketball"], default="auto")
    parser.add_argument("--season", default="Historical")
    args = parser.parse_args()

    root = Path(args.root)
    files = sorted(root.rglob("*.csv"))
    init_db()
    db = SessionLocal()
    totals = {"soccer": 0, "basketball": 0, "files": 0}
    try:
        for path in files:
            sport = infer_sport(path) if args.sport == "auto" else args.sport
            league = infer_league(path, sport)
            try:
                if sport == "basketball":
                    loaded = load_basketball_csv(db, str(path), league=league, season=args.season)
                else:
                    loaded = load_football_csv(db, str(path), league=league, season=args.season)
                totals[sport] += loaded
                totals["files"] += 1
                print({"file": str(path), "sport": sport, "league": league, "loaded": loaded})
            except Exception as exc:
                print({"file": str(path), "error": str(exc)})
        print(totals)
    finally:
        db.close()


if __name__ == "__main__":
    main()
