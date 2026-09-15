"""Providers package — data source abstractions and registry.

Public API re-exports from base.py for backward compatibility:
    from app.scraper.providers import SportsDataProvider, StandingsRow, FixtureInfo, MatchStats, TeamStats, StandingsProvider

Provider registry is config-driven with graceful fallback chains.
"""
from app.scraper.providers.base import (
    SportsDataProvider,
    StandingsProvider,
    StandingsRow,
    FixtureInfo,
    MatchStats,
    TeamStats,
)

__all__ = [
    "SportsDataProvider",
    "StandingsProvider",
    "StandingsRow",
    "FixtureInfo",
    "MatchStats",
    "TeamStats",
    "get_provider",
    "get_provider_chain",
    "PROVIDER_REGISTRY",
]


# ---------------------------------------------------------------------------
# Provider registry — lazily instantiate providers on demand.
#
# Fallback chain for standings (per the config-driven selection pattern):
#   football_data_csv -> afriscores -> espn -> flashscore
#
# Each provider is instantiated only when requested, avoiding import cost
# for unused sources. Providers that fail import are silently skipped so the
# system degrades gracefully (e.g. requests not installed).
# ---------------------------------------------------------------------------

_PROVIDER_FACTORY = {
    "football_data_csv": lambda: __import__(
        "app.scraper.football_data_provider", fromlist=["FootballDataCsvProvider"]
    ).FootballDataCsvProvider(),
    "afriscores": lambda: __import__(
        "app.scraper.afriscores_provider", fromlist=["AfriScoresProvider"]
    ).AfriScoresProvider(),
    "espn": lambda: __import__(
        "app.scraper.espn_provider", fromlist=["ESPNProvider"]
    ).ESPNProvider(),
    "flashscore": lambda: __import__(
        "app.scraper.flashscore_client", fromlist=["FlashscoreProvider"]
    ).FlashscoreProvider(),
}

_PROVIDER_INSTANCES: dict[str, SportsDataProvider] = {}


def get_provider(name: str) -> SportsDataProvider | None:
    """Get a singleton provider instance by name."""
    if name in _PROVIDER_INSTANCES:
        return _PROVIDER_INSTANCES[name]
    factory = _PROVIDER_FACTORY.get(name)
    if factory is None:
        return None
    try:
        instance = factory()
        _PROVIDER_INSTANCES[name] = instance
        return instance
    except Exception:
        return None


def get_provider_chain(sport: str = "soccer") -> list[SportsDataProvider]:
    """Return a list of available providers in fallback priority order.

    The chain order reflects data quality: Football-Data CSV (historical +
    odds), then AfriScores (GraphQL, broad coverage), then ESPN (hidden API,
    major leagues), then Flashscore (broad but JS-heavy).
    """
    chain = []
    for name in ("football_data_csv", "afriscores", "espn", "flashscore"):
        provider = get_provider(name)
        if provider is not None:
            chain.append(provider)
    return chain


PROVIDER_REGISTRY = {
    "football_data_csv": "app.scraper.football_data_provider.FootballDataCsvProvider",
    "afriscores": "app.scraper.afriscores_provider.AfriScoresProvider",
    "espn": "app.scraper.espn_provider.ESPNProvider",
    "flashscore": "app.scraper.flashscore_client.FlashscoreProvider",
}
