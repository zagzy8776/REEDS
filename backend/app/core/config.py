from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/local.db"
    app_env: str = "development"
    admin_api_key: str = "change-me"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    enable_scheduler: bool = False
    min_training_rows: int = 20
    model_dir: str = "data/models"
    public_brand_name: str = "LOYAL EDGE"
    api_football_key: str = ""
    api_football_com_key: str = ""
    api_basketball_key: str = ""
    api_sports_key: str = ""
    sportmonks_api_key: str = ""
    football_data_api_key: str = ""
    the_odds_api_key: str = ""
    the_odds_api_sport_keys: str = "soccer_epl,soccer_spain_la_liga,soccer_italy_serie_a,soccer_germany_bundesliga,soccer_france_ligue_one,soccer_uefa_champs_league,soccer_fifa_world_cup_qualifier,soccer_uefa_european_championship_qualifier,soccer_conmebol_world_cup_qualifier,soccer_concacaf_world_cup_qualifier,soccer_afc_asian_cup_qualifier,soccer_caf_africa_cup_of_nations_qualifier,soccer_international_friendly"
    live_ingest_days: int = 7

    @property
    def allowed_cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def odds_api_sport_keys(self) -> list[str]:
        return [sport.strip() for sport in self.the_odds_api_sport_keys.split(",") if sport.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=("settings_",),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
