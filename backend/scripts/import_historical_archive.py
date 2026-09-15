"""Import repository historical CSVs into CockroachDB fixtures_archive."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"


def connect():
    import psycopg
    from app.core.config import get_settings
    url = (get_settings().cockroach_database_url or os.environ.get("COCKROACH_DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("COCKROACH_DATABASE_URL is not configured")
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    cert = Path.home() / ".postgresql" / "root.crt"
    kwargs = {"sslrootcert": str(cert)} if cert.exists() else {}
    return psycopg.connect(url, **kwargs)


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


def pick(df: pd.DataFrame, candidates: list[str]):
    cols = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
    for candidate in candidates:
        key = candidate.strip().lower().replace(" ", "_")
        if key in cols:
            return cols[key]
    return None


def infer_sport(path: Path) -> str:
    return "basketball" if "basketball" in str(path).lower() else "soccer"


def infer_league(path: Path) -> str:
    s = str(path).lower()
    names = {"epl":"EPL","la_liga":"LA_LIGA","serie_a":"SERIE_A","bundesliga":"BUNDESLIGA","ligue_1":"LIGUE_1","championship":"CHAMPIONSHIP","eredivisie":"EREDIVISIE","portugal":"PORTUGAL","belgium":"BELGIUM","scotland":"SCOTLAND","turkey":"TURKEY","nba":"NBA"}
    for key, value in names.items():
        if key in s:
            return value
    return "Basketball" if "basketball" in s else "Football"


def stable_id(sport: str, league: str, match_date, home: str, away: str) -> int:
    key = f"{sport}|{league}|{match_date}|{home}|{away}".lower().encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") & ((1 << 63) - 1)


def parse_file(path: Path) -> list[dict]:
    df = read_csv(path)
    sport = infer_sport(path)
    league = infer_league(path)
    if sport == "soccer":
        date_col = pick(df, ["Date"]); home_col = pick(df, ["HomeTeam"]); away_col = pick(df, ["AwayTeam"])
        hs_col = pick(df, ["FTHG", "HomeGoals", "home_score"]); as_col = pick(df, ["FTAG", "AwayGoals", "away_score"])
        ho_col = pick(df, ["B365H", "HomeOdds", "home_odds"]); do_col = pick(df, ["B365D", "DrawOdds", "draw_odds"]); ao_col = pick(df, ["B365A", "AwayOdds", "away_odds"])
    else:
        date_col = pick(df, ["GAME_DATE_EST", "GAME_DATE", "Date", "date"])
        home_col = pick(df, ["HOME_TEAM_NAME", "HOME_TEAM", "HomeTeam", "home_team"]); away_col = pick(df, ["VISITOR_TEAM_NAME", "AWAY_TEAM", "AwayTeam", "away_team"])
        hs_col = pick(df, ["PTS_home", "HOME_PTS", "HomePTS", "home_score", "home_points"]); as_col = pick(df, ["PTS_away", "AWAY_PTS", "AwayPTS", "away_score", "away_points"])
        ho_col = do_col = ao_col = None
    if not all([date_col, home_col, away_col, hs_col, as_col]):
        return []
    out = []
    for _, row in df.iterrows():
        try:
            dt = pd.to_datetime(row[date_col], errors="coerce", dayfirst=(sport == "soccer"))
            hs = pd.to_numeric(row[hs_col], errors="coerce"); aas = pd.to_numeric(row[as_col], errors="coerce")
            home = str(row[home_col]).strip(); away = str(row[away_col]).strip()
            if pd.isna(dt) or pd.isna(hs) or pd.isna(aas) or not home or not away or home.lower() == "nan" or away.lower() == "nan":
                continue
            def num(col):
                if not col: return None
                v = pd.to_numeric(row[col], errors="coerce")
                return None if pd.isna(v) else float(v)
            out.append({
                "id": stable_id(sport, league, dt.date(), home, away), "sport": sport, "league": league,
                "season": path.stem[-6:] or "Historical", "match_date": dt.date(), "home_team": home, "away_team": away,
                "home_score": int(hs), "away_score": int(aas), "home_odds": num(ho_col), "draw_odds": num(do_col), "away_odds": num(ao_col),
                "source": f"raw:{path.relative_to(ROOT)}", "created_at": None, "extra": json.dumps({"historical_import": True, "source_file": str(path.relative_to(ROOT))}),
            })
        except Exception:
            continue
    return out


def main():
    conn = connect()
    files = sorted(RAW.rglob("*.csv")); rows = []; skipped = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS fixtures_archive (id INT8 PRIMARY KEY, sport STRING, league STRING, season STRING, match_date DATE, home_team STRING, away_team STRING, home_score INT8, away_score INT8, home_odds FLOAT8, draw_odds FLOAT8, away_odds FLOAT8, source STRING, extra JSONB, created_at TIMESTAMP DEFAULT now())""")
        conn.commit()
        for path in files:
            parsed = parse_file(path)
            if not parsed: skipped += 1
            rows.extend(parsed)
            print(f"{path.relative_to(ROOT)} -> {len(parsed)} completed rows")
        print(f"Prepared {len(rows):,} rows from {len(files)} CSVs; skipped {skipped} files")
        sql = """UPSERT INTO fixtures_archive (id,sport,league,season,match_date,home_team,away_team,home_score,away_score,home_odds,draw_odds,away_odds,source,created_at,extra) VALUES (%(id)s,%(sport)s,%(league)s,%(season)s,%(match_date)s,%(home_team)s,%(away_team)s,%(home_score)s,%(away_score)s,%(home_odds)s,%(draw_odds)s,%(away_odds)s,%(source)s,now(),%(extra)s::JSONB)"""
        for start in range(0, len(rows), 250):
            with conn.cursor() as cur:
                cur.executemany(sql, rows[start:start+250])
            conn.commit()
            print(f"loaded {min(start+250,len(rows)):,}/{len(rows):,}")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), min(match_date), max(match_date) FROM fixtures_archive")
            count, first_date, last_date = cur.fetchone()
        print(f"DONE: {count:,} archive rows; range={first_date} -> {last_date}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
