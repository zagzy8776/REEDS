from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.services.data_quality import resolve_team_name
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


def read_csv_flexible(path: str, **kwargs) -> pd.DataFrame:
    """Read public sports CSVs that may use UTF-8 or legacy European encodings."""

    last_error: Exception | None = None
    for encoding in ("utf-8", "latin1", "cp1252"):
        try:
            return pd.read_csv(path, encoding=encoding, **kwargs)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return pd.read_csv(path, **kwargs)

BASKETBALL_DATA_MAP = {
    "Date": "match_date",
    "GAME_DATE": "match_date",
    "game_date": "match_date",
    "GAME_DATE_EST": "match_date",
    "date": "match_date",
    "HomeTeam": "home_team",
    "HOME_TEAM": "home_team",
    "HOME_TEAM_NAME": "home_team",
    "TEAM_NAME_home": "home_team",
    "home_team_name": "home_team",
    "home_team": "home_team",
    "VisitorTeam": "away_team",
    "AwayTeam": "away_team",
    "AWAY_TEAM": "away_team",
    "VISITOR_TEAM_NAME": "away_team",
    "TEAM_NAME_away": "away_team",
    "away_team_name": "away_team",
    "away_team": "away_team",
    "HomePTS": "home_score",
    "PTS_home": "home_score",
    "HOME_PTS": "home_score",
    "PTS_HOME": "home_score",
    "home_score": "home_score",
    "home_points": "home_score",
    "AwayPTS": "away_score",
    "PTS_away": "away_score",
    "AWAY_PTS": "away_score",
    "PTS_AWAY": "away_score",
    "away_score": "away_score",
    "away_points": "away_score",
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
    header = read_csv_flexible(path, nrows=0).columns
    df = read_csv_flexible(path).rename(columns={k: v for k, v in FOOTBALL_DATA_MAP.items() if k in header})
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
            home_team=resolve_team_name(db, str(r["home_team"]), "soccer", Path(path).name),
            away_team=resolve_team_name(db, str(r["away_team"]), "soccer", Path(path).name),
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

    header = read_csv_flexible(path, nrows=0).columns
    df = read_csv_flexible(path).rename(columns={k: v for k, v in BASKETBALL_DATA_MAP.items() if k in header})

    # Kaggle/GitHub NBA files often store one row per team, not one row per game.
    # If a file has matchup strings like "LAL vs. BOS" / "BOS @ LAL", normalize
    # those rows into home/away fixtures before using the generic loader path.
    matchup_col = next((c for c in ["MATCHUP", "matchup"] if c in df.columns), None)
    points_col = next((c for c in ["PTS", "points", "TEAM_PTS"] if c in df.columns), None)
    team_col = next((c for c in ["TEAM_NAME", "team_name", "TEAM_ABBREVIATION"] if c in df.columns), None)
    game_col = next((c for c in ["GAME_ID", "game_id"] if c in df.columns), None)
    if matchup_col and points_col and team_col and game_col and "home_team" not in df.columns:
        rows = []
        for _, g in df.groupby(game_col):
            if len(g) < 2:
                continue
            home = away = None
            for _, r in g.iterrows():
                matchup = str(r.get(matchup_col, ""))
                if " vs. " in matchup or " vs " in matchup:
                    home = r
                elif " @ " in matchup:
                    away = r
            if home is None or away is None:
                continue
            rows.append({
                "match_date": home.get("match_date"),
                "home_team": home.get(team_col),
                "away_team": away.get(team_col),
                "home_score": home.get(points_col),
                "away_score": away.get(points_col),
            })
        df = pd.DataFrame(rows)
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
            home_team=resolve_team_name(db, str(r["home_team"]), "basketball", Path(path).name),
            away_team=resolve_team_name(db, str(r["away_team"]), "basketball", Path(path).name),
            home_score=None if pd.isna(r.get("home_score")) else int(r.get("home_score")),
            away_score=None if pd.isna(r.get("away_score")) else int(r.get("away_score")),
            source=Path(path).name,
        )
        upsert_fixture(db, fx)
        count += 1
    db.commit()
    return count
