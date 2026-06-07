from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.scraper.api_clients import ApiBasketballClient, ApiFootballClient
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


def _to_int_or_none(value) -> int | None:
    try:
        if value is None or pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float_or_none(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_api_football_1x2_odds(payload: dict) -> dict[int, dict[str, float | None]]:
    odds_by_fixture: dict[int, dict[str, float | None]] = {}
    for item in payload.get("response", []) or []:
        fixture_id = item.get("fixture", {}).get("id")
        if not fixture_id:
            continue
        for bookmaker in item.get("bookmakers", []) or []:
            for bet in bookmaker.get("bets", []) or []:
                name = str(bet.get("name", "")).lower()
                if name not in {"match winner", "1x2", "winner"}:
                    continue
                row = {"home_odds": None, "draw_odds": None, "away_odds": None}
                for value in bet.get("values", []) or []:
                    label = str(value.get("value", "")).lower()
                    odd = _to_float_or_none(value.get("odd"))
                    if label in {"home", "1"}:
                        row["home_odds"] = odd
                    elif label in {"draw", "x"}:
                        row["draw_odds"] = odd
                    elif label in {"away", "2"}:
                        row["away_odds"] = odd
                odds_by_fixture[int(fixture_id)] = row
                break
            if int(fixture_id) in odds_by_fixture:
                break
    return odds_by_fixture


def ingest_api_football_fixtures(db: Session, api_key: str, target_dates: list[str], include_odds: bool = True) -> int:
    client = ApiFootballClient(api_key)
    count = 0
    for target_date in target_dates:
        fixture_payload = client.fixtures_by_date(target_date)
        odds_by_fixture = _parse_api_football_1x2_odds(client.odds_by_date(target_date)) if include_odds else {}
        for item in fixture_payload.get("response", []) or []:
            fixture_data = item.get("fixture", {})
            league_data = item.get("league", {})
            teams = item.get("teams", {})
            goals = item.get("goals", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            fixture_id = fixture_data.get("id")
            match_date = pd.to_datetime(fixture_data.get("date"), errors="coerce")
            if pd.isna(match_date) or not home.get("name") or not away.get("name"):
                continue
            odds = odds_by_fixture.get(int(fixture_id or 0), {})
            fx = Fixture(
                sport="soccer",
                league=league_data.get("name") or "Football",
                season=str(league_data.get("season") or match_date.year),
                match_date=match_date.date(),
                home_team=resolve_team_name(db, str(home.get("name")), "soccer", "api_football"),
                away_team=resolve_team_name(db, str(away.get("name")), "soccer", "api_football"),
                home_score=_to_int_or_none(goals.get("home")),
                away_score=_to_int_or_none(goals.get("away")),
                home_odds=odds.get("home_odds"),
                draw_odds=odds.get("draw_odds"),
                away_odds=odds.get("away_odds"),
                source="api_football",
                extra={"api_fixture_id": fixture_id, "status": fixture_data.get("status"), "country": league_data.get("country")},
            )
            upsert_fixture(db, fx)
            count += 1
        db.commit()
    return count


def ingest_api_basketball_games(db: Session, api_key: str, target_dates: list[str]) -> int:
    client = ApiBasketballClient(api_key)
    count = 0
    for target_date in target_dates:
        payload = client.games_by_date(target_date)
        for item in payload.get("response", []) or []:
            teams = item.get("teams", {})
            scores = item.get("scores", {})
            league = item.get("league", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            home_scores = scores.get("home", {}) if isinstance(scores.get("home"), dict) else {}
            away_scores = scores.get("away", {}) if isinstance(scores.get("away"), dict) else {}
            match_date = pd.to_datetime(item.get("date"), errors="coerce")
            if pd.isna(match_date) or not home.get("name") or not away.get("name"):
                continue
            fx = Fixture(
                sport="basketball",
                league=league.get("name") or "Basketball",
                season=str(item.get("season") or match_date.year),
                match_date=match_date.date(),
                home_team=resolve_team_name(db, str(home.get("name")), "basketball", "api_basketball"),
                away_team=resolve_team_name(db, str(away.get("name")), "basketball", "api_basketball"),
                home_score=_to_int_or_none(home_scores.get("total")),
                away_score=_to_int_or_none(away_scores.get("total")),
                source="api_basketball",
                extra={"api_game_id": item.get("id"), "status": item.get("status"), "country": league.get("country")},
            )
            upsert_fixture(db, fx)
            count += 1
        db.commit()
    return count


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
