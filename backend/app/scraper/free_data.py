"""Free historical data ingestion from public sources.

Sources (no API key required):
  1. football-data.co.uk — 25+ seasons, 30+ leagues, CSV format with odds
  2. OpenFootball (GitHub raw CSVs) — international results since 1872
  3. StatsBomb open data — already handled by import_statsbomb_open_data.py

These are called from the daily scheduler and admin /ingest-free endpoint.
They are completely free and vastly increase model training data.
"""

import logging
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.scraper.loaders import upsert_fixture, _to_int_or_none, _to_float_or_none
from app.services.data_quality import resolve_team_name

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# football-data.co.uk — free historical odds + results, no key needed
# ---------------------------------------------------------------------------

# Each tuple: (league_code_on_site, our_league_name, division_path)
FOOTBALL_DATA_CO_UK_LEAGUES = [
    # England
    ("E0", "Premier League", "E0"),
    ("E1", "Championship", "E1"),
    ("E2", "League One", "E2"),
    ("E3", "League Two", "E3"),
    # Spain
    ("SP1", "La Liga", "SP1"),
    ("SP2", "La Liga 2", "SP2"),
    # Germany
    ("D1", "Bundesliga", "D1"),
    ("D2", "Bundesliga 2", "D2"),
    # Italy
    ("I1", "Serie A", "I1"),
    ("I2", "Serie B", "I2"),
    # France
    ("F1", "Ligue 1", "F1"),
    ("F2", "Ligue 2", "F2"),
    # Netherlands
    ("N1", "Eredivisie", "N1"),
    # Portugal
    ("P1", "Primeira Liga", "P1"),
    # Turkey
    ("T1", "Super Lig", "T1"),
    # Greece
    ("G1", "Super League", "G1"),
    # Scotland
    ("SC0", "Scottish Premiership", "SC0"),
    # Belgium
    ("B1", "First Division A", "B1"),
]

# Seasons to download — last 5 seasons is plenty for model training
SEASONS = ["2324", "2223", "2122", "2021", "1920"]


def _fdco_url(division: str, season: str) -> str:
    return f"https://www.football-data.co.uk/mmz4281/{season}/{division}.csv"


def _col(df: pd.DataFrame, *names: str):
    """Return first matching column value, handling different column naming."""
    for name in names:
        if name in df.columns:
            return name
    return None


def ingest_football_data_co_uk(
    db: Session,
    leagues: list[tuple] | None = None,
    seasons: list[str] | None = None,
    max_leagues: int = 10,
) -> dict:
    """Download and ingest free historical data from football-data.co.uk.

    Returns counts per league. Safe to call repeatedly — upsert_fixture
    handles duplicates gracefully.
    """
    target_leagues = (leagues or FOOTBALL_DATA_CO_UK_LEAGUES)[:max_leagues]
    target_seasons = seasons or SEASONS
    results: dict[str, int] = {}
    errors: list[str] = []

    for league_code, league_name, division in target_leagues:
        count = 0
        for season in target_seasons:
            url = _fdco_url(division, season)
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                df = pd.read_csv(StringIO(resp.text), on_bad_lines="skip")

                date_col = _col(df, "Date")
                home_col = _col(df, "HomeTeam", "Home")
                away_col = _col(df, "AwayTeam", "Away")
                hg_col = _col(df, "FTHG", "HG")
                ag_col = _col(df, "FTAG", "AG")
                ho_col = _col(df, "B365H", "WHH", "PSH", "BbAvH")
                do_col = _col(df, "B365D", "WHD", "PSD", "BbAvD")
                ao_col = _col(df, "B365A", "WHA", "PSA", "BbAvA")

                if not all([date_col, home_col, away_col]):
                    continue

                # Parse season from filename e.g. "2324" → "2023/24"
                season_label = f"20{season[:2]}/{season[2:]}"

                for _, row in df.iterrows():
                    try:
                        parsed_date = pd.to_datetime(row[date_col], dayfirst=True, errors="coerce")
                        if pd.isna(parsed_date):
                            continue
                        home = str(row[home_col]) if home_col else ""
                        away = str(row[away_col]) if away_col else ""
                        if not home or not away or home == "nan":
                            continue
                        fx = Fixture(
                            sport="soccer",
                            league=league_name,
                            season=season_label,
                            match_date=parsed_date.date(),
                            home_team=resolve_team_name(db, home, "soccer", "fdco"),
                            away_team=resolve_team_name(db, away, "soccer", "fdco"),
                            home_score=_to_int_or_none(row.get(hg_col)) if hg_col else None,
                            away_score=_to_int_or_none(row.get(ag_col)) if ag_col else None,
                            home_odds=_to_float_or_none(row.get(ho_col)) if ho_col else None,
                            draw_odds=_to_float_or_none(row.get(do_col)) if do_col else None,
                            away_odds=_to_float_or_none(row.get(ao_col)) if ao_col else None,
                            source="fdco",
                            extra={"season_code": season, "league_code": league_code},
                        )
                        upsert_fixture(db, fx)
                        count += 1
                    except Exception:
                        continue
                db.commit()
            except Exception as exc:
                errors.append(f"{league_code}/{season}: {exc}")
                continue

        results[league_name] = count

    return {"loaded": results, "errors": errors}


# ---------------------------------------------------------------------------
# OpenFootball — international + club results from GitHub (free, no key)
# ---------------------------------------------------------------------------

OPENFOOTBALL_URLS = [
    (
        "https://raw.githubusercontent.com/openfootball/world-cup/master/2022/world-cup.json",
        "FIFA World Cup", "2022",
    ),
    (
        "https://raw.githubusercontent.com/openfootball/euro-cup/master/2020/euro-cup.json",
        "UEFA European Championship", "2020",
    ),
    (
        "https://raw.githubusercontent.com/openfootball/world-cup/master/2018/world-cup.json",
        "FIFA World Cup", "2018",
    ),
]


def ingest_openfootball(db: Session, urls: list[tuple] | None = None) -> dict:
    """Ingest OpenFootball JSON tournament data (free, GitHub-hosted)."""
    import json as _json

    targets = urls or OPENFOOTBALL_URLS
    total = 0
    errors = []

    for url, league_name, season in targets:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            rounds = data.get("rounds", []) or []
            for rnd in rounds:
                for match in rnd.get("matches", []) or []:
                    try:
                        date_str = match.get("date") or match.get("day")
                        parsed_date = pd.to_datetime(date_str, errors="coerce")
                        if pd.isna(parsed_date):
                            continue
                        team1 = match.get("team1", {})
                        team2 = match.get("team2", {})
                        home = team1.get("name") or team1.get("code", "")
                        away = team2.get("name") or team2.get("code", "")
                        if not home or not away:
                            continue
                        score = match.get("score", {})
                        ft = score.get("ft", [None, None])
                        fx = Fixture(
                            sport="soccer",
                            league=league_name,
                            season=season,
                            match_date=parsed_date.date(),
                            home_team=resolve_team_name(db, home, "soccer", "openfootball"),
                            away_team=resolve_team_name(db, away, "soccer", "openfootball"),
                            home_score=_to_int_or_none(ft[0]) if ft and len(ft) > 0 else None,
                            away_score=_to_int_or_none(ft[1]) if ft and len(ft) > 1 else None,
                            source="openfootball",
                            extra={"round": rnd.get("name")},
                        )
                        upsert_fixture(db, fx)
                        total += 1
                    except Exception:
                        continue
            db.commit()
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    return {"loaded": total, "errors": errors}


def ingest_all_free_sources(db: Session, max_leagues: int = 10) -> dict:
    """Run all free data sources in sequence. Called from scheduler + admin endpoint."""
    report = {}
    try:
        report["football_data_co_uk"] = ingest_football_data_co_uk(db, max_leagues=max_leagues)
    except Exception as exc:
        report["football_data_co_uk"] = {"error": str(exc)}
    try:
        report["openfootball"] = ingest_openfootball(db)
    except Exception as exc:
        report["openfootball"] = {"error": str(exc)}
    return report
