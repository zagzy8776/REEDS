"""Phase 3 cutover: Aiven becomes the production primary database.

Locks in config.primary_database_url precedence (AIVEN_DATABASE_URL first,
legacy DATABASE_URL as the transition fallback) and the production guard that
validates the resolved primary URL instead of the legacy URL alone.
"""

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("DATABASE_URL", "AIVEN_DATABASE_URL", "APP_ENV"):
        monkeypatch.delenv(var, raising=False)
    yield


def _settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app.core.config import Settings

    return Settings()


def test_aiven_url_is_primary_when_configured(monkeypatch):
    s = _settings(
        monkeypatch,
        AIVEN_DATABASE_URL="postgresql://u:p@aiven.example/db",
        DATABASE_URL="postgresql://u:p@legacy.example/db",
    )
    assert s.primary_database_url.startswith("postgresql://u:p@aiven.example/db")
    assert "legacy.example" not in s.primary_database_url


def test_legacy_url_is_fallback_when_aiven_absent(monkeypatch):
    s = _settings(
        monkeypatch,
        DATABASE_URL="postgresql://u:p@legacy.example/db",
    )
    assert s.primary_database_url.startswith("postgresql://u:p@legacy.example/db")


def test_primary_falls_back_to_sqlite_when_nothing_set(monkeypatch):
    s = _settings(monkeypatch)
    assert s.primary_database_url.startswith("sqlite:///")


def test_production_guard_accepts_aiven_postgres_without_legacy_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "AIVEN_DATABASE_URL", "postgresql://u:p@aiven.example/db"
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.primary_database_url.startswith("postgresql://")
    finally:
        get_settings.cache_clear()


def test_production_guard_rejects_sqlite_aiven(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AIVEN_DATABASE_URL", "sqlite:///./local.db")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="AIVEN_DATABASE_URL"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_production_guard_rejects_sqlite_legacy_when_aiven_absent(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./local.db")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="AIVEN_DATABASE_URL"):
            get_settings()
    finally:
        get_settings.cache_clear()