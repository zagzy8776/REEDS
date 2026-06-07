from app.scraper.http_client import HttpClient


class ApiFootballClient:
    """API-Football-style adapter. Keep keys in env; never hardcode secrets."""

    def __init__(self, api_key: str | None, base_url: str = "https://v3.football.api-sports.io"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = HttpClient()

    def fixtures(self, league_id: int, season: int) -> dict:
        if not self.api_key:
            return {"response": [], "note": "API_FOOTBALL_KEY not configured"}
        return self.http.get(
            f"{self.base_url}/fixtures",
            params={"league": league_id, "season": season},
            headers={"x-apisports-key": self.api_key},
        ).json()

    def fixtures_by_date(self, target_date: str) -> dict:
        if not self.api_key:
            return {"response": [], "note": "API_FOOTBALL_KEY not configured"}
        return self.http.get(
            f"{self.base_url}/fixtures",
            params={"date": target_date},
            headers={"x-apisports-key": self.api_key},
        ).json()

    def odds_by_date(self, target_date: str, bookmaker: int | None = None) -> dict:
        if not self.api_key:
            return {"response": [], "note": "API_FOOTBALL_KEY not configured"}
        params = {"date": target_date}
        if bookmaker:
            params["bookmaker"] = bookmaker
        return self.http.get(
            f"{self.base_url}/odds",
            params=params,
            headers={"x-apisports-key": self.api_key},
        ).json()


class ApiBasketballClient:
    """API-Basketball-style adapter from API-SPORTS."""

    def __init__(self, api_key: str | None, base_url: str = "https://v1.basketball.api-sports.io"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = HttpClient()

    def games_by_date(self, target_date: str) -> dict:
        if not self.api_key:
            return {"response": [], "note": "API_BASKETBALL_KEY/API_SPORTS_KEY not configured"}
        return self.http.get(
            f"{self.base_url}/games",
            params={"date": target_date},
            headers={"x-apisports-key": self.api_key},
        ).json()


class TheOddsApiClient:
    """The Odds API adapter. Keys come from https://dash.the-odds-api.com/."""

    def __init__(self, api_key: str | None, base_url: str = "https://api.the-odds-api.com/v4"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = HttpClient()

    def h2h_odds(self, sport_key: str, regions: str = "uk,eu,us", bookmakers: str | None = None) -> dict | list:
        if not self.api_key:
            return {"response": [], "note": "THE_ODDS_API_KEY not configured"}
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": "h2h",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers
        return self.http.get(f"{self.base_url}/sports/{sport_key}/odds", params=params).json()


class ApiFootballComClient:
    """apifootball.com adapter. This is different from API-SPORTS API-Football."""

    def __init__(self, api_key: str | None, base_url: str = "https://apiv3.apifootball.com"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = HttpClient()

    def fixtures_by_date(self, target_date: str) -> dict | list:
        if not self.api_key:
            return []
        return self.fixtures_by_range(target_date, target_date)

    def fixtures_by_range(self, from_date: str, to_date: str) -> dict | list:
        if not self.api_key:
            return []
        return self.http.get(
            self.base_url,
            params={"action": "get_events", "from": from_date, "to": to_date, "APIkey": self.api_key},
        ).json()


class SportMonksFootballClient:
    """SportMonks Football v3 adapter."""

    def __init__(self, api_key: str | None, base_url: str = "https://api.sportmonks.com/v3/football"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = HttpClient()

    def fixtures_by_date(self, target_date: str) -> dict:
        if not self.api_key:
            return {"data": []}
        return self.http.get(
            f"{self.base_url}/fixtures/date/{target_date}",
            params={"api_token": self.api_key, "include": "participants;scores;league"},
        ).json()


class FootballDataOrgClient:
    """football-data.org v4 adapter."""

    def __init__(self, api_key: str | None, base_url: str = "https://api.football-data.org/v4"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = HttpClient()

    def matches_by_date(self, target_date: str) -> dict:
        if not self.api_key:
            return {"matches": []}
        return self.matches_by_range(target_date, target_date)

    def matches_by_range(self, from_date: str, to_date: str) -> dict:
        if not self.api_key:
            return {"matches": []}
        return self.http.get(
            f"{self.base_url}/matches",
            params={"dateFrom": from_date, "dateTo": to_date},
            headers={"X-Auth-Token": self.api_key},
        ).json()
