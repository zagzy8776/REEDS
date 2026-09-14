"""Import repository historical CSVs into CockroachDB fixtures_archive.

This deliberately bypasses the Aiven Fixture ORM path. It is safe to rerun:
rows use a deterministic primary key derived from sport/league/date/teams and
Cockroach UPSERT updates the historical row in place.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from app.db import roles as db_roles
from app.db.session import get_role_engine

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"

FOOTBALL_MAP = {
    "date": "match_date", "Date": "match_date", "DATE": "match_date",
    "hometeam": "home_team", "HomeTeam": "home_team",
    "awayteam": "away_team", "AwayTeam": "away_team",
    "fthg": "home_score", "FTHG": "home_score",
    "ftag": "away_score", "FTAG": "away_score",
    "b365h": "home_odds", "B365H": "home_odds",
    "b365d": "draw_odds", "B365D": "draw_odds",
    "b365a": "away_odds", "B365A": "away_odds",
}

BASKETBALL_KEYS = {
    "date": "match_date", "Date": "match_date", "GAME_DATE": "match_date",
    "GAME_DATE_EST": "match_date", "game_date": "match_date",
    "HomeTeam": "home_team", "HOME_TEAM": "home_team", "HOME_TEAM_NAME": "home_team",
    "home_team": "home_team", "home_team_name": "home_team",
    "VisitorTeam": "away_team", "AwayTeam": "away_team", "AWAY_TEAM": "away_team",
    "VISITOR_TEAM_NAME": "away_team", "away_team": "away_team", "away_team_name": "away_team",
    "HomePTS": "home_score", "PTS_home": "home_score", "HOME_PTS": "home_score",
    "PTS_HOME": "home_score", "home_score": "home_score", "home_points": "home_score",
    "AwayPTS": "away_score", "PTS_away": "away_score", "AWAY_PTS": "away_score",
    "PTS_AWAY": "away_score", "away_score": "away_score", "away_points": "away_score",
}


def read_csv(path: Path) -> pd.DataFrame:
    last = None
    for encoding in ("utf-8", "latin1", "cp1252"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last = exc
    if last:
        raise last
    return pd.read_csv(path)


def norm_columns(df: pd.DataFrame) -> dict[str, str]:
    return {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}


def pick(df: pd.DataFrame, candidates: list[str]):
    cols = norm_columns(df)
    for candidate in candidates:
        key = candidate.strip().lower().replace(" ", "_")
        if key in cols:
            return cols[key]
    return None


def infer_sport(path: Path) -> str:
    return "basketball" if "basketball" in str(path).lower() else "soccer"


def infer_league(path: Path) -> str:
    s = str(path).lower()
    names = {
        "epl": "EPL", "la_liga": "LA_LIGA", "serie_a": "SERIE_A",
        "bundesliga": "BUNDESLIGA", "ligue_1": "LIGUE_1", "championship": "CHAMPIONSHIP",
        "eredivisie": "EREDIVISIE", "portugal": "PORTUGAL", "belgium": "BELGIUM",
        "scotland": "SCOTLAND", "turkey": "TURKEY", "nba": "NBA",
    }
    for key, value in names.items():
        if key in s:
            return value
    return "Basketball" if "basketball" in s else "Football"


def stable_id(sport: str, league: str, match_date, home: str, away: str) -> int:
    key = f"{sport}|{league}|{match_date}|{home}|{away}".lower().encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") & ((1 << 63) - 1)


def parse_file(path: Path) -> list[dict]:
    df = read_csv(path)
    sport = infer_sport(path)
    league = infer_league(path)
    if sport == "soccer":
        date_col = pick(df, ["Date"])
        home_col = pick(df, ["HomeTeam"])
        away_col = pick(df, ["AwayTeam"])
        hs_col = pick(df, ["FTHG", "HomeGoals", "home_score"])
        as_col = pick(df, ["FTAG", "AwayGoals", "away_score"])
        ho_col = pick(df, ["B365H", "HomeOdds", "home_odds"])
        do_col = pick(df, ["B365D", "DrawOdds", "draw_odds"])
        ao_col = pick(df, ["B365A", "AwayOdds", "away_odds"])
    else:
        date_col = pick(df, ["GAME_DATE_EST", "GAME_DATE", "Date", "date"])
        home_col = pick(df, ["HOME_TEAM_NAME", "HOME_TEAM", "HomeTeam", "home_team"])
        away_col = pick(df, ["VISITOR_TEAM_NAME", "AWAY_TEAM", "AwayTeam", "away_team"])
        hs_col = pick(df, ["PTS_home", "HOME_PTS", "HomePTS", "home_score", "home_points"])
        as_col = pick(df, ["PTS_away", "AWAY_PTS", "AwayPTS", "away_score", "away_points"])
        ho_col = do_col = ao_col = None
    if not all([date_col, home_col, away_col, hs_col, as_col]):
        return []
    out = []
    for _, row in df.iterrows():
        try:
            match_date = pd.to_datetime(row[date_col], errors="coerce", dayfirst=(sport == "soccer"))
            hs = pd.to_numeric(row[hs_col], errors="coerce")
            aas = pd.to_numeric(row[as_col], errors="coerce")
            home = str(row[home_col]).strip()
            away = str(row[away_col]).strip()
            if pd.isna(match_date) or pd.isna(hs) or pd.isna(aas) or not home or not away or home.lower() == "nan" or away.lower() == "nan":
                continue
            def num(col):
                if not col:
                    return None
                v = pd.to_numeric(row[col], errors="coerce")
                return None if pd.isna(v) else float(v)
            out.append({
                "id": stable_id(sport, league, match_date.date(), home, away),
                "sport": sport, "league": league, "season": path.stem[-6:] or "Historical",
                "match_date": match_date.date(), "home_team": home, "away_team": away,
                "home_score": int(hs), "away_score": int(aas),
                "home_odds": num(ho_col), "draw_odds": num(do_col), "away_odds": num(ao_col),
                "source": f"raw:{path.relative_to(ROOT)}",
                "extra": json.dumps({"historical_import": True, "source_file": str(path.relative_to(ROOT))}),
            })
        except Exception:
            continue
    return out


def main() -> None:
    engine = get_role_engine(db_roles.COCKROACH)
    if engine is None:
        raise RuntimeError("Cockroach role is not configured")
    files = sorted(RAW.rglob("*.csv"))
    rows: list[dict] = []
    skipped = 0
    for path in files:
        parsed = parse_file(path)
        if not parsed:
            skipped += 1
        rows.extend(parsed)
        print(f"{path.relative_to(ROOT)} -> {len(parsed)} completed rows")
    print(f"Prepared {len(rows):,} historical rows from {len(files)} CSVs; skipped {skipped} files")
    create_sql = text("""
        CREATE TABLE IF NOT EXISTS fixtures_archive (
            id INT8 PRIMARY KEY,
            sport STRING,
            league STRING,
            season STRING,
            match_date DATE,
            home_team STRING,
            away_team STRING,
            home_score INT8,
            away_score INT8,
            home_odds FLOAT8,
            draw_odds FLOAT8,
            away_odds FLOAT8,
            source STRING,
            extra JSONB,
            created_at TIMESTAMP DEFAULT now()
        )
    """)
    upsert = text("""
        UPSERT INTO fixtures_archive
        (id, sport, league, season, match_date, home_team, away_team,
         home_score, away_score, home_odds, draw_odds, away_odds, source, extra)
        VALUES
        (:id, :sport, :league, :season, :match_date, :home_team, :away_team,
         :home_score, :away_score, :home_odds, :draw_odds, :away_odds, :source,
         CAST(:extra AS JSONB))
    """)
    with engine.begin() as conn:
        conn.execute(create_sql)
    batch = 250
    for start in range(0, len(rows), batch):
        with engine.begin() as conn:
            conn.execute(upsert, rows[start:start + batch])
        print(f"loaded {min(start + batch, len(rows)):,}/{len(rows):,}")
    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM fixtures_archive")).scalar_one()
        rng = conn.execute(text("SELECT min(match_date), max(match_date) FROM fixtures_archive")).one()
    print(f"DONE: {count:,} archive rows; range={rng[0]} -> {rng[1]}")


if __name__ == "__main__":
    main()
