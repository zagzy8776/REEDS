import json
import logging
from datetime import date, datetime

import requests

from app.scraper.providers import (
    SportsDataProvider,
    StandingsRow,
    FixtureInfo,
    MatchStats,
    TeamStats,
)
from app.utils.team_names import normalize_team_name

log = logging.getLogger(__name__)


class AfriScoresProvider(SportsDataProvider):
    """Provider for AfriScores standings and match data via GraphQL.

    AfriScores covers 333+ leagues worldwide including African, Asian,
    and European leagues (including small ones like Welsh leagues).
    Uses the public GraphQL endpoint at soccer-graph.com/graphql.

    Site: https://www.afriscores.com/
    GraphQL: https://soccer-graph.com/graphql
    """

    provider_name = "afriscores"

    GRAPHQL_URL = "https://soccer-graph.com/graphql"

    ESPN_LEAGUE_TO_AFISCores = {
        "EPL": "8",
        "Championship": "9",
        "La Liga": "564",
        "Serie A": "384",
        "Bundesliga": "82",
        "Ligue 1": "301",
        "Eredivisie": "72",
        "Primeira Liga": "462",
        "Champions League": "2",
        "Europa League": "5",
        "Bundesliga": "82",
        "Scottish Premiership": "12",
        "MLS": "253",
    }

    _STANDINGS_QUERY = """
    query Standings($leagueId: String!) {
      tables(filters: {league_id: $leagueId}) {
        id
        name
        league_id
        season_id
        standings {
          position
          team_id
          team_name
          points
          result
          recent_form
          overall {
            games_played
            won
            draw
            lost
            goals_scored
            goals_against
            points
          }
          home {
            games_played
            won
            draw
            lost
            goals_scored
            goals_against
            points
          }
          away {
            games_played
            won
            draw
            lost
            goals_scored
            goals_against
            points
          }
        }
      }
    }
    """

    _LEAGUES_QUERY = """
    query Leagues {
      leagues {
        id
        name
        alt_name
        country_id
      }
    }
    """

    _TEAMS_BY_LEAGUE_QUERY = """
    query Teams($leagueId: String!) {
      teams(filters: {league_id: $leagueId}, perPage: 200) {
        id
        name
        short_code
      }
    }
    """

    def __init__(self, http_client=None, timeout: int = 30):
        self.http = http_client or requests.Session()
        self.timeout = timeout
        self._league_cache: dict[str, list[dict]] = {}
        self._cached_league_names: dict[str, str] = {}

    def get_supported_leagues(self, sport: str = "soccer") -> list[str]:
        if sport != "soccer":
            return []
        leagues = self._fetch_leagues()
        names = []
        for l in leagues:
            name = l.get("name") or l.get("alt_name") or ""
            if name:
                names.append(name)
        return list(dict.fromkeys(names))

    def _fetch_leagues(self) -> list[dict]:
        if self._league_cache:
            return self._league_cache.get("all", [])

        resp = self.http.post(
            self.GRAPHQL_URL,
            json={"query": self._LEAGUES_QUERY},
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
            timeout=self.timeout,
        )
        data = resp.json()
        leagues = data.get("data", {}).get("leagues", [])

        for l in leagues:
            league_id = str(l.get("id", ""))
            name = l.get("name") or l.get("alt_name") or ""
            self._cached_league_names[league_id] = name

        self._league_cache["all"] = leagues
        return leagues

    def _resolve_league_id(self, league: str) -> str | None:
        if league in self.ESPN_LEAGUE_TO_AFISCores:
            return self.ESPN_LEAGUE_TO_AFISCores[league]

        lower = str(league).lower().strip()
        for l in self._fetch_leagues():
            name = l.get("name") or l.get("alt_name") or ""
            if name.lower() == lower or (l.get("alt_name") and l.get("alt_name", "").lower() == lower):
                return str(l.get("id"))

        for league_id, name in self._cached_league_names.items():
            if name.lower() == lower:
                return league_id

        return None

    def get_standings(
        self,
        sport: str,
        league: str,
        season: str,
        standings_date: date | str,
    ) -> list[StandingsRow]:
        if sport != "soccer":
            return []

        league_id = self._resolve_league_id(league)
        if not league_id:
            return []

        if isinstance(standings_date, str):
            standings_date = datetime.strptime(standings_date, "%Y-%m-%d").date()

        try:
            resp = self.http.post(
                self.GRAPHQL_URL,
                json={"query": self._STANDINGS_QUERY, "variables": {"leagueId": league_id}},
                headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            data = resp.json()
        except Exception:
            return []

        tables = data.get("data", {}).get("tables", [])
        rows: list[StandingsRow] = []

        # The soccer-graph standings payload carries team_id but often returns
        # an empty team_name string. The authoritative names live on the
        # league's team list, so resolve them there before parsing.
        team_name_map = self._league_team_names(league_id)

        for table in tables:
            if self._season_matches(table.get("season_id"), season):
                rows.extend(self._parse_table_standings(table, league, season, standings_date, team_name_map))

        return rows

    def _league_team_names(self, league_id: str) -> dict[str, str]:
        """Return {team_id: team_name} for a league, cached per league id.

        Falls back to short_code when name is missing. Never raises — an
        unresolvable name simply leaves the standings row unresolved.
        """
        cache_key = f"teams:{league_id}"
        cached = self._league_cache.get(cache_key)
        if cached is not None:
            return cached

        mapping: dict[str, str] = {}
        try:
            resp = self.http.post(
                self.GRAPHQL_URL,
                json={"query": self._TEAMS_BY_LEAGUE_QUERY, "variables": {"leagueId": league_id}},
                headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            data = resp.json()
            for team in (data.get("data", {}) or {}).get("teams", []) or []:
                tid = str(team.get("id") or "")
                name = (team.get("name") or team.get("short_code") or "").strip()
                if tid and name:
                    mapping[tid] = name
        except Exception:
            log.exception("AfriScores: could not fetch team names for league_id=%s", league_id)

        self._league_cache[cache_key] = mapping
        return mapping

    def _season_matches(self, table_season_id: str, target_season: str) -> bool:
        if not target_season:
            return True
        if str(table_season_id) == str(target_season):
            return True
        if target_season in str(table_season_id):
            return True
        return False

    def _parse_table_standings(
        self,
        table: dict,
        league: str,
        season: str,
        standings_date: date,
        team_name_map: dict[str, str] | None = None,
    ) -> list[StandingsRow]:
        rows = []
        standings = table.get("standings", []) or []
        name_map = team_name_map or {}
        for s in standings:
            team_id = str(s.get("team_id") or "")
            # Prefer the payload name, then the league team list, then skip.
            raw_name = (s.get("team_name") or "").strip() or name_map.get(team_id, "")
            team = normalize_team_name(raw_name, "soccer") if raw_name else ""
            if not team:
                log.warning(
                    "AfriScores: unresolved team_id=%s in %s standings (position=%s); skipping row",
                    team_id, league, s.get("position"),
                )
                continue

            overall = s.get("overall", {}) or {}
            home_stats = s.get("home", {}) or {}
            away_stats = s.get("away", {}) or {}

            rows.append(StandingsRow(
                provider=self.provider_name,
                sport="soccer",
                league=league,
                season=season,
                team=team,
                team_source_id=team_id or None,
                position=s.get("position"),
                points=s.get("points"),
                games_played=overall.get("games_played"),
                wins=overall.get("won"),
                draws=overall.get("draw"),
                losses=overall.get("lost"),
                goals_for=overall.get("goals_scored"),
                goals_against=overall.get("goals_against"),
                goal_difference=(overall.get("goals_scored") - overall.get("goals_against"))
                    if overall.get("goals_scored") is not None and overall.get("goals_against") is not None
                    else None,
                home_position=None,
                home_points=home_stats.get("points"),
                home_games_played=home_stats.get("games_played"),
                home_wins=home_stats.get("won"),
                home_draws=home_stats.get("draw"),
                home_losses=home_stats.get("lost"),
                home_goals_for=home_stats.get("goals_scored"),
                home_goals_against=home_stats.get("goals_against"),
                away_position=None,
                away_points=away_stats.get("points"),
                away_games_played=away_stats.get("games_played"),
                away_wins=away_stats.get("won"),
                away_draws=away_stats.get("draw"),
                away_losses=away_stats.get("lost"),
                away_goals_for=away_stats.get("goals_scored"),
                away_goals_against=away_stats.get("goals_against"),
                recent_form=s.get("recent_form"),
                updated_timestamp=None,
                scraped_at=datetime.utcnow().timestamp(),
            ))

        return rows

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
        return []

    def get_match_stats(
        self,
        sport: str,
        home_team: str,
        away_team: str,
        match_date: date | str,
    ) -> MatchStats | None:
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
