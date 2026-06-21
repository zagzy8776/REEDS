"""Free historical data ingestion from public sources.

Sources (no API key required):
  1. football-data.co.uk — 25+ seasons, 30+ leagues, CSV format with real bookmaker odds
  2. OpenFootball (GitHub raw CSVs) — international results
  3. worldfootball.net via rsssf fallback

These vastly increase model training data with zero API cost.
Call POST /api/admin/ingest-free then POST /api/admin/train to retrain.
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
# football-data.co.uk — free, 25 years, real bookmaker odds, no key needed
# ---------------------------------------------------------------------------

FOOTBALL_DATA_CO_UK_LEAGUES = [
    # Premier League of England — highest quality, most data
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
    # Argentina
    ("ARG", "Primera Division Argentina", "ARG"),
    # Brazil
    ("BRA", "Serie A Brazil", "BRA"),
]

# Seasons available on football-data.co.uk
ALL_SEASONS = [
    "2425", "2324", "2223", "2122", "2021",
    "1920", "1819", "1718", "1617", "1516",
]

# Default: last 8 seasons (good balance of recency vs. volume)
DEFAULT_SEASONS = ["2425", "2324", "2223", "2122", "2021", "1920", "1819", "1718"]


def _fdco_url(division: str, season: str) -> str:
    return f"https://www.football-data.co.uk/mmz4281/{season}/{division}.csv"


def _best_odds_cols(df: pd.DataFrame) -> tuple:
    """Pick the best available odds columns in priority order."""
    home_candidates = ["B365H", "PSH", "WHH", "BbAvH", "MaxH", "AvgH"]
    draw_candidates = ["B365D", "PSD", "WHD", "BbAvD", "MaxD", "AvgD"]
    away_candidates = ["B365A", "PSA", "WHA", "BbAvA", "MaxA", "AvgA"]
    ho = next((c for c in home_candidates if c in df.columns), None)
    do = next((c for c in draw_candidates if c in df.columns), None)
    ao = next((c for c in away_candidates if c in df.columns), None)
    return ho, do, ao


def ingest_football_data_co_uk(
    db: Session,
    leagues: list[tuple] | None = None,
    seasons: list[str] | None = None,
    max_leagues: int = 20,
) -> dict:
    """Download and ingest free historical data from football-data.co.uk.

    Each CSV has ~380 matches (one full season). 8 seasons × 10 leagues = ~30,000
    rows with real Betfair/B365/Pinnacle odds — enough for a well-calibrated model.
    Safe to call repeatedly — upsert_fixture handles duplicates.
    """
    target_leagues = (leagues or FOOTBALL_DATA_CO_UK_LEAGUES)[:max_leagues]
    target_seasons = seasons or DEFAULT_SEASONS
    total_loaded = 0
    results: dict[str, int] = {}
    errors: list[str] = []

    for league_code, league_name, division in target_leagues:
        count = 0
        for season in target_seasons:
            url = _fdco_url(division, season)
            try:
                resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()

                # Some files have trailing commas that confuse parsers
                df = pd.read_csv(StringIO(resp.text), on_bad_lines="skip")
                df = df.dropna(how="all")  # drop blank rows

                date_col = next((c for c in ["Date"] if c in df.columns), None)
                home_col = next((c for c in ["HomeTeam", "Home"] if c in df.columns), None)
                away_col = next((c for c in ["AwayTeam", "Away"] if c in df.columns), None)
                hg_col = next((c for c in ["FTHG", "HG"] if c in df.columns), None)
                ag_col = next((c for c in ["FTAG", "AG"] if c in df.columns), None)
                ho_col, do_col, ao_col = _best_odds_cols(df)

                if not all([date_col, home_col, away_col]):
                    log.warning("FDCO %s/%s missing required columns", division, season)
                    continue

                season_label = f"20{season[:2]}/{season[2:]}"

                for _, row in df.iterrows():
                    try:
                        parsed_date = pd.to_datetime(row[date_col], dayfirst=True, errors="coerce")
                        if pd.isna(parsed_date):
                            continue
                        home = str(row[home_col]).strip()
                        away = str(row[away_col]).strip()
                        if not home or not away or home in ("nan", ""):
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
                log.info("FDCO %s/%s: loaded %d rows", league_name, season, count)

            except Exception as exc:
                errors.append(f"{league_code}/{season}: {exc}")
                continue

        results[league_name] = count
        total_loaded += count

    return {"total": total_loaded, "by_league": results, "errors": errors[:10]}


# ---------------------------------------------------------------------------
# OpenFootball — international + major tournaments (GitHub JSON, free)
# ---------------------------------------------------------------------------

OPENFOOTBALL_URLS = [
    ("https://raw.githubusercontent.com/openfootball/world-cup/master/2022/world-cup.json", "FIFA World Cup", "2022"),
    ("https://raw.githubusercontent.com/openfootball/world-cup/master/2018/world-cup.json", "FIFA World Cup", "2018"),
    ("https://raw.githubusercontent.com/openfootball/euro-cup/master/2020/euro-cup.json", "UEFA European Championship", "2020"),
    ("https://raw.githubusercontent.com/openfootball/euro-cup/master/2016/euro-cup.json", "UEFA European Championship", "2016"),
    ("https://raw.githubusercontent.com/openfootball/copa-america/master/2021/copa-america.json", "Copa America", "2021"),
    ("https://raw.githubusercontent.com/openfootball/champions-league/master/2022-23/cl.json", "UEFA Champions League", "2022/23"),
    ("https://raw.githubusercontent.com/openfootball/champions-league/master/2021-22/cl.json", "UEFA Champions League", "2021/22"),
    ("https://raw.githubusercontent.com/openfootball/champions-league/master/2020-21/cl.json", "UEFA Champions League", "2020/21"),
]


def ingest_openfootball(db: Session, urls: list[tuple] | None = None) -> dict:
    """Ingest OpenFootball JSON tournament data (free, GitHub-hosted)."""
    targets = urls or OPENFOOTBALL_URLS
    total = 0
    errors: list[str] = []

    for url, league_name, season in targets:
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json()

            # Handle both {"rounds": [...]} and direct {"matches": [...]} formats
            rounds = data.get("rounds", []) or [{"matches": data.get("matches", [])}]

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
                        ht = score.get("ht")

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
                            extra={
                                "round": rnd.get("name"),
                                "ht_score": f"{ht[0]}-{ht[1]}" if ht and len(ht) >= 2 else None,
                            },
                        )
                        upsert_fixture(db, fx)
                        total += 1
                    except Exception:
                        continue
            db.commit()
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    return {"total": total, "errors": errors}


def ingest_all_free_sources(db: Session, max_leagues: int = 20) -> dict:
    """Run all free data sources. Called from scheduler (weekly) and admin endpoint."""
    report: dict = {}
    try:
        report["football_data_co_uk"] = ingest_football_data_co_uk(db, max_leagues=max_leagues)
    except Exception as exc:
        report["football_data_co_uk"] = {"error": str(exc)}
    try:
        report["openfootball"] = ingest_openfootball(db)
    except Exception as exc:
        report["openfootball"] = {"error": str(exc)}
    return report
