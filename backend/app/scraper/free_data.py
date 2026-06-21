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
    """Run all free data sources — soccer AND all other sports.
    Called from scheduler (weekly) and admin endpoint.
    """
    report: dict = {}

    # --- Soccer ---
    try:
        report["football_data_co_uk"] = ingest_football_data_co_uk(db, max_leagues=max_leagues)
    except Exception as exc:
        report["football_data_co_uk"] = {"error": str(exc)}
    try:
        report["openfootball"] = ingest_openfootball(db)
    except Exception as exc:
        report["openfootball"] = {"error": str(exc)}

    # --- Tennis ---
    try:
        report["tennis_atp"] = ingest_tennis_atp(db)
    except Exception as exc:
        report["tennis_atp"] = {"error": str(exc)}
    try:
        report["tennis_wta"] = ingest_tennis_wta(db)
    except Exception as exc:
        report["tennis_wta"] = {"error": str(exc)}
    try:
        report["tennis_data_co_uk"] = ingest_tennis_data_co_uk(db)
    except Exception as exc:
        report["tennis_data_co_uk"] = {"error": str(exc)}

    # --- Basketball ---
    try:
        report["nba"] = ingest_nba_github(db)
    except Exception as exc:
        report["nba"] = {"error": str(exc)}

    # --- American Football ---
    try:
        report["nfl"] = ingest_nfl_spreadspoke(db)
    except Exception as exc:
        report["nfl"] = {"error": str(exc)}

    # --- Hockey ---
    try:
        report["nhl"] = ingest_nhl_api(db)
    except Exception as exc:
        report["nhl"] = {"error": str(exc)}

    # --- Cricket ---
    try:
        report["ipl"] = ingest_ipl_github(db)
    except Exception as exc:
        report["ipl"] = {"error": str(exc)}

    return report


# ===========================================================================
# TENNIS — JeffSackmann ATP/WTA GitHub + tennis-data.co.uk (with B365 odds)
# ===========================================================================

ATP_YEARS = list(range(2010, 2026))
WTA_YEARS = list(range(2010, 2026))


def _atp_url(year: int) -> str:
    return f"https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"


def _wta_url(year: int) -> str:
    return f"https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{year}.csv"


def _parse_tennis_sackmann(db: Session, df: pd.DataFrame, tour: str) -> int:
    """Parse JeffSackmann ATP/WTA CSV format into fixtures."""
    count = 0
    for _, row in df.iterrows():
        try:
            date_str = str(row.get("tourney_date", ""))
            if len(date_str) != 8:
                continue
            parsed_date = pd.to_datetime(date_str, format="%Y%m%d", errors="coerce")
            if pd.isna(parsed_date):
                continue

            winner = str(row.get("winner_name", "")).strip()
            loser = str(row.get("loser_name", "")).strip()
            if not winner or not loser or winner == "nan":
                continue

            # Winner = "home" for our model (always won, score = sets won)
            w_sets = _to_int_or_none(row.get("score", "").split("-")[0].split()[0] if row.get("score") else None)
            l_sets = _to_int_or_none(row.get("score", "").split("-")[-1].split()[0] if row.get("score") else None)

            league = str(row.get("tourney_name", tour))
            surface = str(row.get("surface", "")).lower()
            if surface:
                league = f"{league} ({surface})"

            fx = Fixture(
                sport="tennis",
                league=league[:80],
                season=str(parsed_date.year),
                match_date=parsed_date.date(),
                home_team=resolve_team_name(db, winner, "tennis", f"sackmann_{tour.lower()}"),
                away_team=resolve_team_name(db, loser, "tennis", f"sackmann_{tour.lower()}"),
                home_score=w_sets,   # sets won by winner
                away_score=l_sets,
                source=f"sackmann_{tour.lower()}",
                extra={
                    "surface": str(row.get("surface", "")),
                    "round": str(row.get("round", "")),
                    "tourney_level": str(row.get("tourney_level", "")),
                    "winner_rank": _to_int_or_none(row.get("winner_rank")),
                    "loser_rank": _to_int_or_none(row.get("loser_rank")),
                },
            )
            upsert_fixture(db, fx)
            count += 1
        except Exception:
            continue
    return count


def ingest_tennis_atp(db: Session, years: list[int] | None = None) -> dict:
    """Download ATP match results from JeffSackmann's GitHub (free, no key)."""
    target_years = years or ATP_YEARS
    total = 0
    errors: list[str] = []
    for year in target_years:
        try:
            resp = requests.get(_atp_url(year), timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            df = pd.read_csv(StringIO(resp.text), on_bad_lines="skip")
            n = _parse_tennis_sackmann(db, df, "ATP")
            total += n
            db.commit()
        except Exception as exc:
            errors.append(f"ATP {year}: {exc}")
    return {"total": total, "errors": errors[:5]}


def ingest_tennis_wta(db: Session, years: list[int] | None = None) -> dict:
    """Download WTA match results from JeffSackmann's GitHub (free, no key)."""
    target_years = years or WTA_YEARS
    total = 0
    errors: list[str] = []
    for year in target_years:
        try:
            resp = requests.get(_wta_url(year), timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            df = pd.read_csv(StringIO(resp.text), on_bad_lines="skip")
            n = _parse_tennis_sackmann(db, df, "WTA")
            total += n
            db.commit()
        except Exception as exc:
            errors.append(f"WTA {year}: {exc}")
    return {"total": total, "errors": errors[:5]}


# tennis-data.co.uk — ATP/WTA with B365 odds
_TENNIS_DATA_YEARS = list(range(2010, 2026))


def ingest_tennis_data_co_uk(db: Session, years: list[int] | None = None) -> dict:
    """tennis-data.co.uk — free ATP/WTA results with B365/PS odds."""
    target_years = years or _TENNIS_DATA_YEARS
    total = 0
    errors: list[str] = []
    for year in target_years:
        # ATP
        for tour, league in (("atp", "ATP Tour"), ("wta", "WTA Tour")):
            url = f"http://tennis-data.co.uk/{year}/{tour}.csv"
            try:
                resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                df = pd.read_csv(StringIO(resp.text), on_bad_lines="skip")

                # Column names vary by year
                date_col = next((c for c in ["Date"] if c in df.columns), None)
                winner_col = next((c for c in ["Winner"] if c in df.columns), None)
                loser_col = next((c for c in ["Loser"] if c in df.columns), None)
                wsets_col = next((c for c in ["WRank", "W1"] if c in df.columns), None)
                # odds
                wo_col = next((c for c in ["B365W", "PSW", "AvgW"] if c in df.columns), None)
                lo_col = next((c for c in ["B365L", "PSL", "AvgL"] if c in df.columns), None)

                if not all([date_col, winner_col, loser_col]):
                    continue

                for _, row in df.iterrows():
                    try:
                        parsed_date = pd.to_datetime(row[date_col], dayfirst=True, errors="coerce")
                        if pd.isna(parsed_date):
                            continue
                        winner = str(row[winner_col]).strip()
                        loser = str(row[loser_col]).strip()
                        if not winner or winner == "nan":
                            continue
                        tournament = str(row.get("Tournament", row.get("Location", league)))[:80]
                        fx = Fixture(
                            sport="tennis",
                            league=tournament,
                            season=str(parsed_date.year),
                            match_date=parsed_date.date(),
                            home_team=resolve_team_name(db, winner, "tennis", "tennis_data_co_uk"),
                            away_team=resolve_team_name(db, loser, "tennis", "tennis_data_co_uk"),
                            home_score=1,   # winner
                            away_score=0,
                            home_odds=_to_float_or_none(row.get(wo_col)) if wo_col else None,
                            away_odds=_to_float_or_none(row.get(lo_col)) if lo_col else None,
                            source="tennis_data_co_uk",
                            extra={
                                "surface": str(row.get("Surface", "")),
                                "round": str(row.get("Round", "")),
                                "tour": tour.upper(),
                            },
                        )
                        upsert_fixture(db, fx)
                        total += 1
                    except Exception:
                        continue
                db.commit()
            except Exception as exc:
                errors.append(f"tennis_data.co.uk {tour}/{year}: {exc}")
    return {"total": total, "errors": errors[:5]}


# ===========================================================================
# BASKETBALL — NBA GitHub CSVs (NocturneBear/NBA-Data-2010-2024)
# ===========================================================================

_NBA_GAME_URLS = [
    # game-level results with home/away teams and final scores
    "https://raw.githubusercontent.com/NocturneBear/NBA-Data-2010-2024/main/Regular_Season/regular_season_game_logs.csv",
    # fallback: the CSVs we already have locally
]

_NBA_GITHUB_SEASONS = [
    ("regular", f"https://raw.githubusercontent.com/Brescou/NBA-dataset-stats-player-team/main/team_stats/team_stats_{y}_{y+1}.csv", f"{y}/{y+1-2000:02d}")
    for y in range(2010, 2024)
]


def ingest_nba_github(db: Session) -> dict:
    """Pull NBA team game results from GitHub CSVs (free, no key)."""
    total = 0
    errors: list[str] = []

    # Primary: NocturneBear game-level data
    for url in _NBA_GAME_URLS:
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            df = pd.read_csv(StringIO(resp.text), on_bad_lines="skip")

            # Detect column layout
            date_col = next((c for c in ["GAME_DATE", "game_date", "Date", "date"] if c in df.columns), None)
            home_col = next((c for c in ["HOME_TEAM", "home_team", "HomeTeam"] if c in df.columns), None)
            away_col = next((c for c in ["AWAY_TEAM", "away_team", "VisitorTeam", "AwayTeam"] if c in df.columns), None)
            hs_col = next((c for c in ["HOME_PTS", "home_pts", "PTS_home", "HomePTS"] if c in df.columns), None)
            as_col = next((c for c in ["AWAY_PTS", "away_pts", "PTS_away", "AwayPTS", "VisitorPTS"] if c in df.columns), None)

            if not all([date_col, home_col, away_col]):
                continue

            for _, row in df.iterrows():
                try:
                    parsed_date = pd.to_datetime(row[date_col], errors="coerce")
                    if pd.isna(parsed_date):
                        continue
                    home = str(row[home_col]).strip()
                    away = str(row[away_col]).strip()
                    if not home or home == "nan":
                        continue
                    fx = Fixture(
                        sport="basketball",
                        league="NBA",
                        season=str(parsed_date.year),
                        match_date=parsed_date.date(),
                        home_team=resolve_team_name(db, home, "basketball", "nba_github"),
                        away_team=resolve_team_name(db, away, "basketball", "nba_github"),
                        home_score=_to_int_or_none(row.get(hs_col)) if hs_col else None,
                        away_score=_to_int_or_none(row.get(as_col)) if as_col else None,
                        source="nba_github",
                        extra={"season": str(parsed_date.year)},
                    )
                    upsert_fixture(db, fx)
                    total += 1
                except Exception:
                    continue
            db.commit()
        except Exception as exc:
            errors.append(f"nba_github {url}: {exc}")

    return {"total": total, "errors": errors[:5]}


# ===========================================================================
# NFL — Spreadspoke historical scores + spreads/totals (free, no key)
# ===========================================================================

_NFL_CSV_URL = "https://raw.githubusercontent.com/slieb74/NFL-Betting-Data/master/spreadspoke_scores.csv"
# Mirror in case primary fails
_NFL_CSV_MIRROR = "https://raw.githubusercontent.com/devstopfix/nfl_results/master/prolog/nfl_results.csv"


def ingest_nfl_spreadspoke(db: Session) -> dict:
    """Download NFL historical scores + spreads from Spreadspoke (free CSV)."""
    total = 0
    errors: list[str] = []

    for url in [_NFL_CSV_URL]:
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            df = pd.read_csv(StringIO(resp.text), on_bad_lines="skip")

            # Spreadspoke columns
            date_col = next((c for c in ["schedule_date", "date", "Date"] if c in df.columns), None)
            home_col = next((c for c in ["team_home", "home_team", "HomeTeam"] if c in df.columns), None)
            away_col = next((c for c in ["team_away", "away_team", "AwayTeam"] if c in df.columns), None)
            hs_col = next((c for c in ["score_home", "home_score", "HomePTS"] if c in df.columns), None)
            as_col = next((c for c in ["score_away", "away_score", "AwayPTS"] if c in df.columns), None)
            ou_col = next((c for c in ["over_under_line", "over_under"] if c in df.columns), None)
            spread_col = next((c for c in ["spread_favorite", "line"] if c in df.columns), None)
            season_col = next((c for c in ["schedule_season", "season"] if c in df.columns), None)

            if not all([date_col, home_col, away_col]):
                log.warning("NFL Spreadspoke: missing required columns in %s", url)
                continue

            for _, row in df.iterrows():
                try:
                    parsed_date = pd.to_datetime(row[date_col], errors="coerce")
                    if pd.isna(parsed_date):
                        continue
                    # Only last 15 seasons to keep data fresh and DB lean
                    if parsed_date.year < 2010:
                        continue
                    home = str(row[home_col]).strip()
                    away = str(row[away_col]).strip()
                    if not home or home == "nan":
                        continue

                    season = str(_to_int_or_none(row.get(season_col)) or parsed_date.year)
                    ou = _to_float_or_none(row.get(ou_col)) if ou_col else None
                    spread = _to_float_or_none(row.get(spread_col)) if spread_col else None

                    fx = Fixture(
                        sport="american_football",
                        league="NFL",
                        season=season,
                        match_date=parsed_date.date(),
                        home_team=resolve_team_name(db, home, "american_football", "nfl_spreadspoke"),
                        away_team=resolve_team_name(db, away, "american_football", "nfl_spreadspoke"),
                        home_score=_to_int_or_none(row.get(hs_col)) if hs_col else None,
                        away_score=_to_int_or_none(row.get(as_col)) if as_col else None,
                        source="nfl_spreadspoke",
                        extra={
                            "over_under": ou,
                            "spread": spread,
                            "playoff": str(row.get("schedule_playoff", "")).lower() == "true",
                        },
                    )
                    upsert_fixture(db, fx)
                    total += 1
                except Exception:
                    continue
            db.commit()
        except Exception as exc:
            errors.append(f"nfl_spreadspoke: {exc}")

    return {"total": total, "errors": errors[:5]}


# ===========================================================================
# NHL — Public NHL API (api.nhle.com, no key required)
# ===========================================================================

_NHL_SEASONS = [f"{y}{y+1}" for y in range(2014, 2025)]  # e.g. "20142015"
_NHL_API_BASE = "https://api-web.nhle.com/v1"


def ingest_nhl_api(db: Session, seasons: list[str] | None = None) -> dict:
    """Pull NHL game scores from the public NHL API (no key needed)."""
    target_seasons = seasons or _NHL_SEASONS[-6:]  # last 6 seasons by default
    total = 0
    errors: list[str] = []

    for season in target_seasons:
        try:
            # Get schedule for full season
            url = f"{_NHL_API_BASE}/schedule/{season[:4]}-{season[4:6]}-01"
            # NHL API: get standings gives us team info; use schedule endpoint
            sched_url = f"https://api.nhle.com/stats/rest/en/game?cayenneExp=season={season}&start=0&limit=2000"
            resp = requests.get(sched_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue
            data = resp.json()
            games = data.get("data", []) or []

            for game in games:
                try:
                    game_date = pd.to_datetime(str(game.get("gameDate", ""))[:10], errors="coerce")
                    if pd.isna(game_date):
                        continue

                    home_team = str(game.get("homeTeam", {}).get("abbrev", "") or game.get("homeTeamAbbrev", "")).strip()
                    away_team = str(game.get("awayTeam", {}).get("abbrev", "") or game.get("visitingTeamAbbrev", "")).strip()

                    # Try full name fallback
                    if not home_team:
                        home_team = str(game.get("homeTeamCity", "")).strip()
                    if not away_team:
                        away_team = str(game.get("visitingTeamCity", "")).strip()

                    if not home_team or not away_team:
                        continue

                    home_goals = _to_int_or_none(game.get("homeScore", game.get("homeTeamScore")))
                    away_goals = _to_int_or_none(game.get("awayScore", game.get("visitingTeamScore")))

                    game_type = str(game.get("gameType", "2"))
                    league = "NHL Playoffs" if game_type == "3" else "NHL"

                    fx = Fixture(
                        sport="hockey",
                        league=league,
                        season=f"{season[:4]}/{season[4:6]}",
                        match_date=game_date.date(),
                        home_team=resolve_team_name(db, home_team, "hockey", "nhl_api"),
                        away_team=resolve_team_name(db, away_team, "hockey", "nhl_api"),
                        home_score=home_goals,
                        away_score=away_goals,
                        source="nhl_api",
                        extra={"game_id": game.get("id"), "game_type": game_type},
                    )
                    upsert_fixture(db, fx)
                    total += 1
                except Exception:
                    continue
            db.commit()
        except Exception as exc:
            errors.append(f"NHL {season}: {exc}")

    return {"total": total, "errors": errors[:5]}


# ===========================================================================
# CRICKET — IPL matches from ritesh-ojha/IPL-DATASET (GitHub, free)
# ===========================================================================

_IPL_MATCHES_URL = "https://raw.githubusercontent.com/ritesh-ojha/IPL-DATASET/main/matches.csv"
_IPL_MATCHES_FALLBACK = "https://raw.githubusercontent.com/Utkarsh731/IPL/main/matches.csv"


def ingest_ipl_github(db: Session) -> dict:
    """Download IPL match results from GitHub (free, no key)."""
    total = 0
    errors: list[str] = []

    for url in [_IPL_MATCHES_URL, _IPL_MATCHES_FALLBACK]:
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            df = pd.read_csv(StringIO(resp.text), on_bad_lines="skip")

            # IPL CSV columns vary by source
            date_col = next((c for c in ["date", "Date"] if c in df.columns), None)
            team1_col = next((c for c in ["team1", "Team1", "home_team"] if c in df.columns), None)
            team2_col = next((c for c in ["team2", "Team2", "away_team"] if c in df.columns), None)
            winner_col = next((c for c in ["winner", "Winner"] if c in df.columns), None)
            season_col = next((c for c in ["season", "Season", "year"] if c in df.columns), None)
            venue_col = next((c for c in ["venue", "Venue", "city"] if c in df.columns), None)

            if not all([date_col, team1_col, team2_col]):
                continue

            for _, row in df.iterrows():
                try:
                    parsed_date = pd.to_datetime(row[date_col], errors="coerce")
                    if pd.isna(parsed_date):
                        continue
                    team1 = str(row[team1_col]).strip()
                    team2 = str(row[team2_col]).strip()
                    if not team1 or team1 == "nan":
                        continue

                    winner = str(row.get(winner_col, "")).strip() if winner_col else ""
                    # Winner = home score 1, loser = 0 (binary outcome)
                    home_score = 1 if winner == team1 else (0 if winner == team2 else None)
                    away_score = 0 if winner == team1 else (1 if winner == team2 else None)

                    season = str(row.get(season_col, parsed_date.year)) if season_col else str(parsed_date.year)

                    fx = Fixture(
                        sport="cricket",
                        league="IPL",
                        season=season,
                        match_date=parsed_date.date(),
                        home_team=resolve_team_name(db, team1, "cricket", "ipl_github"),
                        away_team=resolve_team_name(db, team2, "cricket", "ipl_github"),
                        home_score=home_score,
                        away_score=away_score,
                        source="ipl_github",
                        extra={
                            "venue": str(row.get(venue_col, "")) if venue_col else None,
                            "winner": winner or None,
                            "toss_winner": str(row.get("toss_winner", "")) if "toss_winner" in df.columns else None,
                        },
                    )
                    upsert_fixture(db, fx)
                    total += 1
                except Exception:
                    continue
            db.commit()
            if total > 0:
                break  # don't hit fallback if primary worked
        except Exception as exc:
            errors.append(f"ipl_github {url}: {exc}")

    return {"total": total, "errors": errors[:5]}
