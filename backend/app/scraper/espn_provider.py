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


class ESPNProvider(SportsDataProvider):
    """Provider for ESPN standings and fixtures via the public ESPN API.

    Uses the ``site.api.espn.com/apis/v2/sports/soccer/{league}/standings``
    endpoint (note: ``/apis/site/v2/`` returns an empty stub for soccer;
    ``/apis/v2/`` returns the full table).

    ESPN covers 24 soccer leagues globally, including major European
    leagues, CONCACAF, CONMEBOL, and AFC competitions.
    """

    provider_name = "espn"

    STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports/soccer"

    LEAGUE_SLUGS = {
        "EPL": "eng.1",
        "Championship": "eng.2",
        "League One": "eng.3",
        "La Liga": "esp.1",
        "Segunda Division": "esp.2",
        "Serie A": "ita.1",
        "Serie B": "ita.2",
        "Bundesliga": "ger.1",
        "2. Bundesliga": "ger.2",
        "Ligue 1": "fra.1",
        "Ligue 2": "fra.2",
        "Eredivisie": "nld.1",
        "Primeira Liga": "por.1",
        "Scottish Premiership": "sco.1",
        "MLS": "usa.1",
        "Champions League": "uefa.champions",
        "Europa League": "uefa.europa",
        "Nations League": "uefa.league",
        "Copa America": "conmebol.copa",
        "FA Cup": "eng.fa",
        "League Cup": "eng.league_cup",
        "FA Community Shield": "eng.charity",
        "World Cup": "fifa.world",
        "Women's Super League": "eng.w.1",
        "Women's Premier League": "usa.nwsl",
        "Bundesliga Women": "ger.w.1",
        "Division 1 Féminine": "fra.w.1",
        "Liga F": "esp.w.1",
        "Serie A Women": "ita.w.1",
    }

    def __init__(self, http_client=None, timeout: int = 20):
        self.http = http_client or requests.Session()
        self.timeout = timeout

    def get_supported_leagues(self, sport: str = "soccer") -> list[str]:
        if sport != "soccer":
            return []
        return list(self.LEAGUE_SLUGS.keys())

    def _resolve_league_slug(self, league: str) -> str | None:
        slug = self.LEAGUE_SLUGS.get(league)
        if slug:
            return slug
        lower = league.lower().strip()
        for name, s in self.LEAGUE_SLUGS.items():
            if name.lower() == lower:
                return s
        return None

    @staticmethod
    def _normalize_season(season: str | None) -> str | None:
        """Reduce season labels like '2025-2026'/'2025/26' to the ESPN year param.

        ESPN accepts a single year (e.g. ``?season=2025``); anything else
        yields HTTP 400 and an empty table. We take the leading 4-digit
        year so callers can keep REEDS-style season labels.
        """
        if not season:
            return None
        import re

        m = re.search(r"(19|20)\d{2}", str(season))
        return m.group(0) if m else None

    def get_standings(
        self,
        sport: str,
        league: str,
        season: str,
        standings_date: date | str,
    ) -> list[StandingsRow]:
        if sport != "soccer":
            return []

        slug = self._resolve_league_slug(league)
        if not slug:
            return []

        if isinstance(standings_date, str):
            standings_date = datetime.strptime(standings_date, "%Y-%m-%d").date()

        url = f"{self.STANDINGS_BASE}/{slug}/standings"
        params: dict = {}
        season_param = self._normalize_season(season)
        if season_param:
            params["season"] = season_param

        try:
            resp = self.http.get(url, params=params, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        return self._parse_standings(data, league, season, standings_date)

    def _parse_standings(
        self,
        data: dict,
        league: str,
        season: str,
        standings_date: date,
    ) -> list[StandingsRow]:
        """Parse ESPN standings JSON.

        ESPN returns standings in two possible shapes:
          (a) data["children"][0]["standings"]["entries"]  — current API shape
          (b) data["standings"] as a list of groups          — legacy shape

        We handle both so the parser is forward-compatible.
        """
        rows: list[StandingsRow] = []

        # Shape (a): current API — data["children"][0]["standings"]["entries"]
        children = data.get("children", [])
        if children:
            child = children[0]
            standings = child.get("standings", {})
            if isinstance(standings, dict):
                entries = standings.get("entries", [])
                for idx, entry in enumerate(entries):
                    row = self._make_standing_row(entry, idx, league, season)
                    if row:
                        rows.append(row)

        # Shape (b): legacy API — data["standings"] is a list of groups
        if not rows:
            standings_list = data.get("standings", [])
            for group in standings_list:
                entries = group.get("entries", [])
                for idx, entry in enumerate(entries):
                    row = self._make_standing_row(entry, idx, league, season)
                    if row:
                        rows.append(row)

        return rows

    def _make_standing_row(
        self,
        entry: dict,
        idx: int,
        league: str,
        season: str,
    ) -> StandingsRow | None:
        """Build a StandingsRow from a single ESPN entry.

        ESPN stat types are lowercase: gamesplayed, wins, ties, losses,
        pointsFor, pointsAgainst, pointdifferential, points, rank.
        """
        team_info = entry.get("team", {})
        team_name = normalize_team_name(team_info.get("displayName", ""), "soccer")
        if not team_name:
            return None

        stats_dict: dict = {}
        for stat in entry.get("stats", []):
            stats_dict[stat.get("type", "")] = stat.get("value")

        gf = stats_dict.get("pointsFor")
        ga = stats_dict.get("pointsAgainst")
        gd = stats_dict.get("pointdifferential")
        if gd is None and gf is not None and ga is not None:
            gd = gf - ga

        points = stats_dict.get("points")
        if points is None:
            wins = stats_dict.get("wins", 0) or 0
            draws = stats_dict.get("ties", 0) or 0
            points = wins * 3 + draws

        return StandingsRow(
            provider=self.provider_name,
            sport="soccer",
            league=league,
            season=season,
            team=team_name,
            team_source_id=str(team_info.get("id")) if team_info.get("id") else None,
            position=idx + 1,
            points=points,
            games_played=stats_dict.get("gamesplayed") or stats_dict.get("gamesPlayed"),
            wins=stats_dict.get("wins"),
            draws=stats_dict.get("ties"),
            losses=stats_dict.get("losses"),
            goals_for=gf,
            goals_against=ga,
            goal_difference=gd,
            recent_form=None,
            updated_timestamp=None,
            scraped_at=datetime.utcnow().timestamp(),
        )

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
