import re
from datetime import date, datetime, timedelta

import requests

from app.scraper.providers import (
    SportsDataProvider,
    StandingsRow,
    FixtureInfo,
    MatchStats,
    TeamStats,
)


class FlashscoreProvider(SportsDataProvider):
    """Provider for Flashscore.com standings and match data.

    Flashscore does not expose a public REST API. Data is embedded in the
    HTML page inside ``<script>`` tags using a ``¬``-delimited feed format.
    This provider parses that feed to extract standings, fixtures, and
    match statistics.

    Site: https://www.flashscore.com/
    """

    provider_name = "flashscore"

    SOCCER_LEAGUE_URLS = {
        "EPL": "https://www.flashscore.com/football/england/premier-league/",
        "La Liga": "https://www.flashscore.com/football/spain/laliga/",
        "Serie A": "https://www.flashscore.com/football/italy/serie-a/",
        "Bundesliga": "https://www.flashscore.com/football/germany/bundesliga/",
        "Ligue 1": "https://www.flashscore.com/football/france/ligue-1/",
        "Eredivisie": "https://www.flashscore.com/football/netherlands/eredivisie/",
        "Primeira Liga": "https://www.flashscore.com/football/portugal/primeira-liga/",
        "Championship": "https://www.flashscore.com/football/england/championship/",
    }

    def __init__(self, http_client=None):
        self.http = http_client or _FlashscoreHttpClient()

    def get_supported_leagues(self, sport: str = "soccer") -> list[str]:
        if sport == "soccer":
            return list(self.SOCCER_LEAGUE_URLS.keys())
        return []

    def get_standings(
        self,
        sport: str,
        league: str,
        season: str,
        standings_date: date | str,
    ) -> list[StandingsRow]:
        """Fetch standings from Flashscore for a league.

        standings_date is the fixture date; we fetch the table as it was
        known before that date. Flashscore's current-table page reflects
        the latest state, so we backfill using historical match data when
        standings_date is in the past.
        """
        if sport != "soccer":
            return []

        league_url = self._find_league_url(league)
        if not league_url:
            return []

        try:
            html = self.http.get_html(league_url)
        except Exception:
            return []

        rows = self._parse_standings_feed(html, league, season, standings_date)
        return rows

    def _find_league_url(self, league: str) -> str | None:
        league_lower = str(league).lower().strip()
        for key, url in self.SOCCER_LEAGUE_URLS.items():
            if key.lower() in league_lower or league_lower in key.lower():
                return url
        return None

    def _parse_standings_feed(
        self,
        html: str,
        league: str,
        season: str,
        standings_date: date | str,
    ) -> list[StandingsRow]:
        """Parse the Flashscore ``¬``-delimited standings feed from the HTML.

        The feed format is:
            team_name¬position¬points¬played¬wins¬draws¬losses¬gf¬ga¬gd¬...

        Rows are delimited by ``|`` within a larger data blob.
        """
        if isinstance(standings_date, str):
            standings_date = datetime.strptime(standings_date, "%Y-%m-%d").date()

        rows: list[StandingsRow] = []

        # Flashscore embeds standings in script tags with specific patterns
        # Pattern 1: ¬-delimited table data
        table_patterns = re.findall(
            r'<table[^>]*class="[^"]*standing[^"]*"[^>]*>(.*?)</table>',
            html,
            re.DOTALL | re.IGNORECASE,
        )

        # Pattern 2: JS data feed
        feed_matches = re.findall(
            r'(\d+¬[^|<]*\|[^|<]*\|[^|<]*\|[^|<]*\|[^|<]*\|[^|<]*\|[^|<]*\|[^|<]*\|[^|<]*\|[^|<]*\|[^|<]*(?:¬\|)*)',
            html,
        )

        # Pattern 3: data in JSON-like arrays within script tags
        script_dumps = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        for script in script_dumps:
            if "standings" not in script.lower() and "standing" not in script.lower():
                continue
            # Look for team position patterns
            team_data = re.findall(
                r'"(?:team|name)"[^}]*?"name"\s*:\s*"([^"]+)"[^}]*?'
                r'"(?:position|pos)"\s*:\s*(\d+)[^}]*?'
                r'"(?:points|pts)"\s*:\s*(\d+)',
                script,
            )
            for team_name, pos, pts in team_data:
                rows.append(StandingsRow(
                    provider=self.provider_name,
                    sport="soccer",
                    league=league,
                    season=season,
                    team=team_name,
                    team_source_id=None,
                    position=int(pos),
                    points=int(pts),
                    games_played=None,
                    recent_form=None,
                    updated_timestamp=None,
                    scraped_at=datetime.utcnow().timestamp(),
                ))

        # If JSON parsing didn't yield results, try ¬-delimited format
        if not rows:
            rows = self._parse_delimited_feed(html, league, season, standings_date)

        return rows

    def _parse_delimited_feed(
        self,
        html: str,
        league: str,
        season: str,
        standings_date: date,
    ) -> list[StandingsRow]:
        """Parse the ¬-delimited Flashscore standings feed."""
        rows: list[StandingsRow] = []

        # Flashscore embed format: rows separated by |, fields by ¬
        # Example: "1¬Manchester City¬87¬38¬24¬9¬5¬89¬21¬68¬..."
        feed = re.search(r'([\d¬|]+[^\d¬|]*¬[^\d¬|]+[^\d¬|]*¬[\d¬|]+)', html)
        if not feed:
            return rows

        raw = feed.group(1)
        for line in raw.split("|"):
            parts = line.split("¬")
            if len(parts) < 12:
                continue
            try:
                pos = int(parts[0])
                team = parts[1].strip()
                pts = int(parts[2])
                played = int(parts[3])
                wins = int(parts[4])
                draws = int(parts[5])
                losses = int(parts[6])
                gf = int(parts[7]) if parts[7] else None
                ga = int(parts[8]) if parts[8] else None
                gd = int(parts[9]) if parts[9] else None

                rows.append(StandingsRow(
                    provider=self.provider_name,
                    sport="soccer",
                    league=league,
                    season=season,
                    team=team,
                    team_source_id=None,
                    position=pos,
                    points=pts,
                    games_played=played,
                    wins=wins,
                    draws=draws,
                    losses=losses,
                    goals_for=gf,
                    goals_against=ga,
                    goal_difference=gd,
                    recent_form=None,
                    updated_timestamp=None,
                    scraped_at=datetime.utcnow().timestamp(),
                ))
            except (ValueError, IndexError):
                continue

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

        urls_to_check = []
        if league:
            url = self._find_league_url(league)
            if url:
                urls_to_check.append(url)
        else:
            urls_to_check.extend(self.SOCCER_LEAGUE_URLS.values())

        fixtures: list[FixtureInfo] = []
        for url in urls_to_check:
            try:
                html = self.http.get_html(url)
            except Exception:
                continue
            fixtures.extend(self._parse_fixtures(html, url))

        if target_date:
            target = target_date if isinstance(target_date, date) else datetime.strptime(target_date, "%Y-%m-%d").date()
            fixtures = [f for f in fixtures if f.match_date == target]

        return fixtures

    def _parse_fixtures(self, html: str, url: str) -> list[FixtureInfo]:
        """Extract fixture links and team names from a Flashscore league page."""
        fixtures: list[FixtureInfo] = []
        # Flashscore fixture links are like /football/.../match/abc123/
        match_urls = re.findall(r'href="(/[^"]*match/[^"]+)"', html)
        seen = set()
        for match_url in match_urls:
            if match_url in seen:
                continue
            seen.add(match_url)

            home_team, away_team = self._extract_match_teams(html, match_url)
            fixtures.append(FixtureInfo(
                provider=self.provider_name,
                sport="soccer",
                league="",
                season="",
                home_team=home_team or "",
                away_team=away_team or "",
                match_date=None,
                provider_fixture_id=match_url.split("/")[-2] if "/" in match_url else None,
                extra={"match_url": f"https://www.flashscore.com{match_url}"},
            ))
        return fixtures

    def _extract_match_teams(self, html: str, match_url: str) -> tuple[str, str]:
        """Extract home and away team names near a match URL in the HTML."""
        idx = html.find(f'href="{match_url}"')
        if idx == -1:
            idx = html.find(match_url)
        if idx == -1:
            return "", ""
        context = html[idx:idx + 2000]
        team_spans = re.findall(r'<span[^>]*>([^<]+)</span>', context, re.DOTALL)
        team_spans = [s.strip() for s in team_spans if s.strip() and not s.strip().startswith(("0-", "1-", "2-", "3-", "4-", "5-", "6-", "7-", "8-", "9-"))]
        home = team_spans[0] if len(team_spans) > 0 else ""
        away = team_spans[1] if len(team_spans) > 1 else ""
        return home, away

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
                    statistics={},
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


class _FlashscoreHttpClient:
    """Minimal HTTP client for Flashscore — uses the existing HttpClient pattern."""

    def __init__(self, timeout: int = 20, proxies: list[str] | None = None):
        from app.scraper.http_client import HttpClient

        self._client = HttpClient(proxies=proxies) if proxies else HttpClient()
        self.timeout = timeout

    def get_html(self, url: str) -> str:
        resp = self._client.get(url)
        return resp.text
