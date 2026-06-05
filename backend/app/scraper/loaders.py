from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.utils.team_names import normalize_team_name


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

BASKETBALL_DATA_MAP = {
    "Date": "match_date",
    "GAME_DATE": "match_date",
    "HomeTeam": "home_team",
    "HOME_TEAM": "home_team",
    "VisitorTeam": "away_team",
    "AwayTeam": "away_team",
    "AWAY_TEAM": "away_team",
    "HomePTS": "home_score",
    "PTS_home": "home_score",
    "HOME_PTS": "home_score",
    "AwayPTS": "away_score",
    "PTS_away": "away_score",
    "AWAY_PTS": "away_score",
}


def upsert_fixture(db: Session, fixture: Fixture) -> None:
    """Insert or update a fixture using the natural uniqueness key.

    SQLAlchemy merge only works by primary key. Our downloaded CSV rows do not know
    primary keys, so we manually find existing rows to avoid duplicate-key errors.
    """

    existing = (
        db.query(Fixture)
        .filter(
            Fixture.sport == fixture.sport,
            Fixture.league == fixture.league,
            Fixture.match_date == fixture.match_date,
            Fixture.home_team == fixture.home_team,
            Fixture.away_team == fixture.away_team,
        )
        .first()
    )
    if existing:
        existing.season = fixture.season
        existing.home_score = fixture.home_score
        existing.away_score = fixture.away_score
        existing.home_odds = fixture.home_odds
        existing.draw_odds = fixture.draw_odds
        existing.away_odds = fixture.away_odds
        existing.source = fixture.source
        existing.extra = fixture.extra
    else:
        db.add(fixture)


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
            home_team=normalize_team_name(str(r["home_team"]), "soccer"),
            away_team=normalize_team_name(str(r["away_team"]), "soccer"),
            home_score=None if pd.isna(r.get("home_score")) else int(r.get("home_score")),
            away_score=None if pd.isna(r.get("away_score")) else int(r.get("away_score")),
            home_odds=None if pd.isna(r.get("home_odds")) else float(r.get("home_odds")),
            draw_odds=None if pd.isna(r.get("draw_odds")) else float(r.get("draw_odds")),
            away_odds=None if pd.isna(r.get("away_odds")) else float(r.get("away_odds")),
            source=Path(path).name,
        )
        upsert_fixture(db, fx)
        count += 1
    db.commit()
    return count


def load_basketball_csv(db: Session, path: str, league: str = "NBA", season: str = "Unknown") -> int:
    """Load basketball historical CSVs from Kaggle/GitHub-style datasets.

    Expected fields can be any of the common names in BASKETBALL_DATA_MAP.
    Rows are normalized into the same fixtures table with sport='basketball'.
    """

    header = pd.read_csv(path, nrows=0).columns
    df = pd.read_csv(path).rename(columns={k: v for k, v in BASKETBALL_DATA_MAP.items() if k in header})
    count = 0
    for _, r in df.iterrows():
        if not {"match_date", "home_team", "away_team"}.issubset(df.columns):
            continue
        parsed_date = pd.to_datetime(r["match_date"], errors="coerce")
        if pd.isna(parsed_date):
            continue
        fx = Fixture(
            sport="basketball",
            league=league,
            season=season,
            match_date=parsed_date.date(),
            home_team=normalize_team_name(str(r["home_team"]), "basketball"),
            away_team=normalize_team_name(str(r["away_team"]), "basketball"),
            home_score=None if pd.isna(r.get("home_score")) else int(r.get("home_score")),
            away_score=None if pd.isna(r.get("away_score")) else int(r.get("away_score")),
            source=Path(path).name,
        )
        upsert_fixture(db, fx)
        count += 1
    db.commit()
    return count
