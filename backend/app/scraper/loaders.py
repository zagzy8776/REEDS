from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture


FOOTBALL_DATA_MAP = {
    "Date": "match_date",
    "HomeTeam": "home_team",
    "AwayTeam": "away_team",
    "FTHG": "home_score",
    "FTAG": "away_score",
    "B365H": "home_odds",
    "B365D": "draw_odds",
    "B365A": "away_odds",
}


def load_football_csv(db: Session, path: str, league: str = "Unknown", season: str = "Unknown") -> int:
    df = pd.read_csv(path).rename(columns={k: v for k, v in FOOTBALL_DATA_MAP.items() if k in pd.read_csv(path, nrows=0).columns})
    count = 0
    for _, r in df.iterrows():
        if not {"match_date", "home_team", "away_team"}.issubset(df.columns):
            continue
        parsed_date = pd.to_datetime(r["match_date"], dayfirst=True, errors="coerce")
        if pd.isna(parsed_date):
            continue
        fx = Fixture(
            sport="soccer",
            league=league,
            season=season,
            match_date=parsed_date.date(),
            home_team=str(r["home_team"]),
            away_team=str(r["away_team"]),
            home_score=None if pd.isna(r.get("home_score")) else int(r.get("home_score")),
            away_score=None if pd.isna(r.get("away_score")) else int(r.get("away_score")),
            home_odds=None if pd.isna(r.get("home_odds")) else float(r.get("home_odds")),
            draw_odds=None if pd.isna(r.get("draw_odds")) else float(r.get("draw_odds")),
            away_odds=None if pd.isna(r.get("away_odds")) else float(r.get("away_odds")),
            source=Path(path).name,
        )
        db.merge(fx)
        count += 1
    db.commit()
    return count
