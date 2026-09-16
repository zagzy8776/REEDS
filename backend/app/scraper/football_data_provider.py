import csv
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from app.scraper.providers import (
    SportsDataProvider,
    StandingsRow,
    FixtureInfo,
    MatchStats,
    TeamStats,
)


class FootballDataCsvProvider(SportsDataProvider):
    """Provider that reads Football-Data.co.uk CSV files.

    Football-Data provides historical match results and bookmaker odds in CSV
    format, one file per league-season. Each row contains match details:
    Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR, plus odds and extra stats
    (corners, cards, xG where available).

    Standings are derived by aggregating match results up to a given date.

    CSV format reference: https://football-data.co.uk/
    """

    provider_name = "football_data_csv"

    DEFAULT_CSV_DIR = "/home/ubuntu/REEDS/data/raw/football"

    # Mapping from Football-Data league codes to human-friendly names
    LEAGUE_CODE_MAP = {
        "E0": "EPL",
        "E1": "Championship",
        "E2": "League One",
        "E3": "League Two",
        "EC": "Champions League",
        "EL": "Europa League",
        "SC0": "Scottish Premiership",
        "SC1": "Scottish Championship",
        "D1": "Bundesliga",
        "D2": "2. Bundesliga",
        "SP1": "La Liga",
        "SP2": "Segunda Division",
        "I1": "Serie A",
        "I2": "Serie B",
        "N1": "Eredivisie",
        "P1": "Primeira Liga",
        "F1": "Ligue 1",
        "F2": "Ligue 2",
        "B1": "Belgian Pro League",
        "T1": "Turkish Super Lig",
    }

    def __init__(self, csv_dir: str | None = None):
        self.csv_dir = csv_dir or self.DEFAULT_CSV_DIR

    def get_supported_leagues(self, sport: str = "soccer") -> list[str]:
        if sport != "soccer":
            return []
        return list(dict.fromkeys(self.LEAGUE_CODE_MAP.values()))

    def get_standings(
        self,
        sport: str,
        league: str,
        season: str,
        standings_date: date | str,
    ) -> list[StandingsRow]:
        if sport != "soccer":
            return []

        if isinstance(standings_date, str):
            standings_date = datetime.strptime(standings_date, "%Y-%m-%d").date()

        csv_files = self._find_csv_files(league, season)
        if not csv_files:
            return []

        # Aggregate match results up to standings_date
        table: dict[str, dict] = {}

        for csv_file in csv_files:
            with open(csv_file, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    row_date = self._parse_date(row.get("Date", ""))
                    if row_date is None or row_date > standings_date:
                        continue

                    home = row.get("HomeTeam", "").strip()
                    away = row.get("AwayTeam", "").strip()
                    fthg_raw = row.get("FTHG", "")
                    ftag_raw = row.get("FTAG", "")
                    ftr = row.get("FTR", "")

                    if not home or not away:
                        continue

                    try:
                        fthg = int(fthg_raw) if fthg_raw else None
                        ftag = int(ftag_raw) if ftag_raw else None
                    except ValueError:
                        continue

                    if fthg is None or ftag is None:
                        continue

                    self._update_table(table, home, fthg, ftag, ftr)
                    self._update_table(table, away, ftag, fthg, "A" if ftr == "H" else ("H" if ftr == "A" else "D"))

        # Build final standings
        rows: list[StandingsRow] = []
        sorted_teams = sorted(
            table.items(),
            key=lambda x: (-x[1]["points"], -x[1]["goal_diff"], -x[1]["goals_for"]),
        )
        for idx, (team, stats) in enumerate(sorted_teams):
            rows.append(StandingsRow(
                provider=self.provider_name,
                sport="soccer",
                league=league,
                season=season,
                team=team,
                team_source_id=None,
                position=idx + 1,
                points=stats["points"],
                games_played=stats["played"],
                wins=stats["wins"],
                draws=stats["draws"],
                losses=stats["losses"],
                goals_for=stats["goals_for"],
                goals_against=stats["goals_against"],
                goal_difference=stats["goal_diff"],
                recent_form=None,
                updated_timestamp=None,
                scraped_at=datetime.utcnow().timestamp(),
            ))

        return rows

    def _update_table(
        self,
        table: dict,
        team: str,
        goals_for: int,
        goals_against: int,
        result: str,
    ) -> None:
        """result is 'H' (home win), 'A' (away win), 'D' (draw), or '' (unknown)."""
        if team not in table:
            table[team] = {
                "points": 0, "played": 0, "wins": 0, "draws": 0, "losses": 0,
                "goals_for": 0, "goals_against": 0, "goal_diff": 0,
            }
        stats = table[team]
        stats["played"] += 1
        stats["goals_for"] += goals_for
        stats["goals_against"] += goals_against
        stats["goal_diff"] += goals_for - goals_against

        if result == "H":
            stats["wins"] += 1
            stats["points"] += 3
        elif result == "A":
            stats["wins"] += 1
            stats["points"] += 3
        elif result == "D":
            stats["draws"] += 1
            stats["points"] += 1
        else:
            stats["losses"] += 1

    def _parse_date(self, date_str: str) -> date | None:
        """Parse various Football-Data date formats."""
        for fmt in ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%m/%d/%Y"]:
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _find_csv_files(self, league: str, season: str) -> list[Path]:
        """Find CSV files matching the league and season.

        Football-Data filenames are like: E0_2023.csv, E0_2022-23.csv
        """
        if not os.path.isdir(self.csv_dir):
            return []

        league_code = self._league_to_code(league)
        matching: list[Path] = []

        for csv_path in Path(self.csv_dir).glob("*.csv"):
            filename = csv_path.name
            # Extract league code from filename (first part before _)
            parts = filename.split("_")
            if not parts:
                continue
            file_league = parts[0]
            file_season = parts[1] if len(parts) > 1 else ""
            # Strip extension: "2024-2025.csv" -> "2024-2025"
            if file_season.lower().endswith(".csv"):
                file_season = file_season[: -len(".csv")]

            if league_code and file_league == league_code:
                if self._season_matches(file_season, season):
                    matching.append(csv_path)
            elif league_code is None:
                # Try matching by league name
                if league.lower() in filename.lower():
                    matching.append(csv_path)

        return matching

    def _league_to_code(self, league: str) -> str | None:
        league_lower = league.lower().strip()
        for code, name in self.LEAGUE_CODE_MAP.items():
            if name.lower() == league_lower or code.lower() == league_lower:
                return code
        return None

    def _season_matches(self, file_season: str, target_season: str) -> bool:
        if file_season == target_season:
            return True
        # Handle formats like "2023-24" vs "2023"
        target_part = target_season.replace("/", "-")
        if file_season in target_part or target_part in file_season:
            return True
        # Try stripping suffixes
        target_year = target_season.split("-")[0].split("/")[0]
        if file_season.startswith(target_year):
            return True
        # Football-Data 4-digit season codes: "2425" == "2024-2025",
        # "2526" == "2025-2026". Compare implied start years.
        import re

        def _start_year(s: str) -> str | None:
            s = s.strip()
            if re.fullmatch(r"\d{4}", s):
                return "20" + s[:2]
            m = re.search(r"(19|20)\d{2}", s)
            return m.group(0) if m else None

        fsy, tsy = _start_year(file_season), _start_year(target_part)
        if fsy and tsy and fsy == tsy:
            return True
        return False

    def get_fixtures(
        self,
        sport: str,
        league: str | None = None,
        target_date: date | str | None = None,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
    ) -> list[FixtureInfo]:
        if sport != "soccer":
            return []

        csv_files = self._find_csv_files(league, "") if league else \
            list(Path(self.csv_dir).glob("*.csv"))

        fixtures: list[FixtureInfo] = []
        for csv_file in csv_files:
            with open(csv_file, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    row_date = self._parse_date(row.get("Date", ""))
                    if row_date is None:
                        continue

                    if target_date:
                        target = target_date if isinstance(target_date, date) else datetime.strptime(target_date, "%Y-%m-%d").date()
                        if row_date != target:
                            continue
                    elif date_from or date_to:
                        check_from = date_from if date_from else date.min
                        check_to = date_to if date_to else date.max
                        if isinstance(check_from, str):
                            check_from = datetime.strptime(check_from, "%Y-%m-%d").date()
                        if isinstance(check_to, str):
                            check_to = datetime.strptime(check_to, "%Y-%m-%d").date()
                        if row_date < check_from or row_date > check_to:
                            continue

                    home = row.get("HomeTeam", "").strip()
                    away = row.get("AwayTeam", "").strip()
                    if not home or not away:
                        continue

                    fixtures.append(FixtureInfo(
                        provider=self.provider_name,
                        sport="soccer",
                        league="",
                        season="",
                        home_team=home,
                        away_team=away,
                        match_date=row_date,
                        provider_fixture_id=f"{home}_{away}_{row_date.isoformat()}",
                        extra={
                            "fthg": int(row["FTHG"]) if row.get("FTHG", "").isdigit() else None,
                            "ftag": int(row["FTAG"]) if row.get("FTAG", "").isdigit() else None,
                            "ftr": row.get("FTR", ""),
                            "b365h": float(row["B365H"]) if row.get("B365H") else None,
                            "b365d": float(row["B365D"]) if row.get("B365D") else None,
                            "b365a": float(row["B365A"]) if row.get("B365A") else None,
                        },
                    ))

        return fixtures

    def get_match_stats(
        self,
        sport: str,
        home_team: str,
        away_team: str,
        match_date: date | str,
    ) -> MatchStats | None:
        fixtures = self.get_fixtures(sport, target_date=match_date)
        for f in fixtures:
            if (
                f.home_team.lower().strip() == str(home_team).lower().strip()
                and f.away_team.lower().strip() == str(away_team).lower().strip()
            ):
                return MatchStats(
                    provider=self.provider_name,
                    sport=sport,
                    match_date=match_date,
                    home_team=f.home_team,
                    away_team=f.away_team,
                    provider_fixture_id=f.provider_fixture_id,
                    statistics={
                        "fthg": f.extra.get("fthg"),
                        "ftag": f.extra.get("ftag"),
                        "ftr": f.extra.get("ftr"),
                        "b365h": f.extra.get("b365h"),
                        "b365d": f.extra.get("b365d"),
                        "b365a": f.extra.get("b365a"),
                    },
                    effective_date=match_date,
                )
        return None

    def get_team_stats(
        self,
        sport: str,
        team: str,
        stat_type: str,
        window: int | None = None,
        effective_before: date | str | None = None,
    ) -> list[TeamStats]:
        return []
