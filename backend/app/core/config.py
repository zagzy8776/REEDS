from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/local.db"
    app_env: str = "development"
    admin_api_key: str = "change-me"
    enable_scheduler: bool = False
    min_training_rows: int = 20
    model_dir: str = "data/models"
    public_brand_name: str = "LOYAL EDGE"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=("settings_",),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
