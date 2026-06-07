import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.scraper.loaders import load_basketball_csv, load_football_csv


def infer_sport(path: Path) -> str:
    lowered = str(path).lower()
    if "basket" in lowered or "nba" in lowered:
        return "basketball"
    return "soccer"


def infer_league(path: Path, sport: str) -> str:
    parts = [p.upper() for p in path.parts]
    if sport == "basketball":
        if "NBA" in parts:
            return "NBA"
        return "Basketball"
    known = ["EPL", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "CHAMPIONSHIP",
             "EREDIVISIE", "PORTUGAL", "BELGIUM", "SCOTLAND", "TURKEY"]
    for item in known:
        if item in parts or item.lower() in str(path).lower():
            return item.replace("_", " ")
    return "Football"


def main() -> dict:
    root = Path("data/raw")
    files = sorted(root.rglob("*.csv"))
    init_db()
    db = SessionLocal()
    totals = {"soccer": 0, "basketball": 0, "files": 0}
    try:
        for path in files:
            sport = infer_sport(path)
            league = infer_league(path, sport)
            try:
                if sport == "basketball":
                    loaded = load_basketball_csv(db, str(path), league=league, season="Historical")
                else:
                    loaded = load_football_csv(db, str(path), league=league, season="Historical")
                totals[sport] += loaded
                totals["files"] += 1
                print(f"✅ {path.name} → {sport}/{league}: {loaded} rows")
            except Exception as exc:
                print(f"❌ {path.name}: {exc}")
        print(f"\n📊 Total: {totals['soccer']} soccer + {totals['basketball']} basketball = {totals['soccer'] + totals['basketball']} rows")
        return totals
    finally:
        db.close()


if __name__ == "__main__":
    main()