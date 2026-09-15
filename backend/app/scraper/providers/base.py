from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass
class StandingsRow:
    provider: str
    sport: str
    league: str
    season: str
    team: str
    team_source_id: str | None
    position: int
    points: int
    games_played: int
    wins: int | None = None
    draws: int | None = None
    losses: int | None = None
    goals_for: int | None = None
    goals_against: int | None = None
    goal_difference: int | None = None
    home_position: int | None = None
    home_points: int | None = None
    home_games_played: int | None = None
    home_wins: int | None = None
    home_draws: int | None = None
    home_losses: int | None = None
    home_goals_for: int | None = None
    home_goals_against: int | None = None
    away_position: int | None = None
    away_points: int | None = None
    away_games_played: int | None = None
    away_wins: int | None = None
    away_draws: int | None = None
    away_losses: int | None = None
    away_goals_for: int | None = None
    away_goals_against: int | None = None
    recent_form: str | None = None
    updated_timestamp: int | None = None
    scraped_at: float | None = None


@dataclass
class FixtureInfo:
    provider: str
    sport: str
    league: str
    season: str
    home_team: str
    away_team: str
    match_date: date | str
    match_timestamp: int | None = None
    status: str | None = None
    provider_fixture_id: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    extra: dict | None = None


@dataclass
class MatchStats:
    provider: str
    sport: str
    match_date: date | str
    home_team: str
    away_team: str
    provider_fixture_id: str | None
    statistics: dict
    effective_date: date | str | None = None
    scraped_at: float | None = None


@dataclass
class TeamStats:
    provider: str
    sport: str
    team: str
    metric_name: str
    metric_value: float
    window: int | None = None
    effective_date: date | str | None = None


class SportsDataProvider(ABC):
    """Abstract provider — all data sources implement this interface.

    The prediction system must not depend directly on one scraper.
    Providers are swappable; the standings service calls them polymorphically.
    """

    provider_name: str

    @abstractmethod
    def get_standings(
        self,
        sport: str,
        league: str,
        season: str,
        standings_date: date | str,
    ) -> list[StandingsRow]:
        """Return standings effective BEFORE standings_date (no leakage).

        standings_date is the fixture date; the snapshot reflects the
        table as it was before that fixture was played.
        """
        raise NotImplementedError

    @abstractmethod
    def get_fixtures(
        self,
        sport: str,
        league: str | None = None,
        target_date: date | str | None = None,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
    ) -> list[FixtureInfo]:
        raise NotImplementedError

    @abstractmethod
    def get_match_stats(
        self,
        sport: str,
        home_team: str,
        away_team: str,
        match_date: date | str,
    ) -> MatchStats | None:
        raise NotImplementedError

    @abstractmethod
    def get_team_stats(
        self,
        sport: str,
        team: str,
        stat_type: str,
        window: int | None = None,
        effective_before: date | str | None = None,
    ) -> list[TeamStats]:
        raise NotImplementedError

    def get_supported_leagues(self, sport: str) -> list[str]:
        return []

    def get_supported_seasons(self, sport: str, league: str) -> list[str]:
        return []


class StandingsProvider(SportsDataProvider):
    """Standings-specific provider interface with competition/league abstraction.

    Extends SportsDataProvider with a competition-based lookup pattern:
    ``get_standings(competition, season, view)`` where ``view`` is
    ``'overall'`` (total), ``'home'``, or ``'away'``.
    """

    provider_name: str

    @abstractmethod
    def get_standings(
        self,
        sport: str,
        league: str,
        season: str,
        standings_date: date | str,
    ) -> list[StandingsRow]:
        raise NotImplementedError

    def get_standings_by_competition(
        self,
        competition: str,
        season: str,
        view: str = "overall",
        as_of_date: date | str | None = None,
    ) -> list[StandingsRow]:
        """Fetch standings for a competition on a given date.

        Args:
            competition: league identifier (e.g. 'EPL', 'Premier League', 'eng.1')
            season: season string (e.g. '2024/2025', '2024')
            view: 'overall', 'home', or 'away'
            as_of_date: date the table should reflect (must be before fixtures)

        Returns:
            List of StandingsRow sorted by position.
        """
        import datetime as _dt

        if as_of_date is None:
            as_of_date = _dt.date.today()
        elif isinstance(as_of_date, str):
            as_of_date = _dt.datetime.strptime(as_of_date, "%Y-%m-%d").date()

        rows = self.get_standings("soccer", competition, season, as_of_date)

        if view == "home":
            for r in rows:
                if r.home_position is not None:
                    r.position = r.home_position
                    if r.home_points is not None:
                        r.points = r.home_points
        elif view == "away":
            for r in rows:
                if r.away_position is not None:
                    r.position = r.away_position
                    if r.away_points is not None:
                        r.points = r.away_points

        if view == "home":
            rows.sort(key=lambda r: r.position if r.position is not None else 999)
        elif view == "away":
            rows.sort(key=lambda r: r.position if r.position is not None else 999)
        else:
            rows.sort(key=lambda r: r.position if r.position is not None else 999)

        return rows
