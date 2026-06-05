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
