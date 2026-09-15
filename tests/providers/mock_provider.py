"""Mock provider for testing the standings pipeline without network calls."""
from datetime import date, datetime

from app.scraper.providers.base import SportsDataProvider, StandingsRow


class MockStandingsProvider(SportsDataProvider):
    """Deterministic standings provider for tests.

    Stores standings snapshots keyed by (sport, league, season) and date.
    Returns the most recent snapshot effective before or on the requested date.
    """

    provider_name = "mock_test"

    def __init__(self, standings: dict[tuple, dict[str, list[dict]]] | None = None):
        self._standings = standings or {}

    def set_standings(self, effective_date: str, rows: list[dict]) -> None:
        key = ("soccer", "EPL", "2024")
        self._standings.setdefault(key, {})[effective_date] = rows

    def get_standings(
        self,
        sport: str,
        league: str,
        season: str,
        standings_date: date | str,
    ) -> list[StandingsRow]:
        key = (sport, league, season)
        by_date = self._standings.get(key, {})

        if isinstance(standings_date, str):
            sd = datetime.strptime(standings_date, "%Y-%m-%d").date()
        else:
            sd = standings_date

        best_date = None
        for d in sorted(by_date.keys()):
            try:
                parsed = datetime.strptime(d, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if parsed <= sd:
                best_date = d

        if best_date is None:
            return []

        rows = by_date[best_date]
        return [
            StandingsRow(
                provider=self.provider_name,
                sport=sport,
                league=league,
                season=season,
                team=r["team"],
                team_source_id=r.get("team_source_id"),
                position=r["position"],
                points=r["points"],
                games_played=r["games_played"],
                wins=r.get("wins"),
                draws=r.get("draws"),
                losses=r.get("losses"),
                goals_for=r.get("goals_for"),
                goals_against=r.get("goals_against"),
                goal_difference=r.get("goal_difference"),
                recent_form=r.get("recent_form"),
            )
            for r in rows
        ]

    def get_fixtures(self, sport, league=None, target_date=None, date_from=None, date_to=None):
        return []

    def get_match_stats(self, sport, home_team, away_team, match_date):
        return None

    def get_team_stats(self, sport, team, stat_type, window=None, effective_before=None):
        return []

    def get_supported_leagues(self, sport: str) -> list[str]:
        if sport == "soccer":
            return ["EPL"]
        return []
