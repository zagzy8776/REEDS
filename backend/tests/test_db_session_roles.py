"""Phase 1 tests: lazy role engines/sessions — no network, no real DBMS.

Only SQLite/file URLs are ever created here. Unconfigured roles must return
None without raising, without importing drivers, and without touching the
legacy primary engine. The legacy engine/SessionLocal must stay untouched by
role-engine activity so existing behavior is provably unchanged.
"""

import sqlalchemy.exc
import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _scratch_data_dir(tmp_path):
    """Run role tests against a scratch dir so SQLite role DBs never hit data/."""
    import os

    old = os.getcwd()
    os.chdir(tmp_path)
    yield
    os.chdir(old)


@pytest.fixture(autouse=True)
def _clean_role_env(monkeypatch):
    for var in ("AIVEN_DATABASE_URL", "COCKROACH_DATABASE_URL", "TURSO_DATABASE_URL", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    yield
    from app.db.session import reset_role_engines

    reset_role_engines()


@pytest.fixture()
def aiven_sqlite(monkeypatch):
    """Point the aiven role at a scratch SQLite file DB."""
    url = "sqlite:///./test_aiven_role.db"
    monkeypatch.setenv("AIVEN_DATABASE_URL", url)
    return url


def test_unconfigured_roles_return_none_without_engines():
    from app.db import roles as db_roles
    from app.db.session import get_role_engine, get_role_sessionmaker

    for role in db_roles.ROLES:
        assert get_role_engine(role) is None
        assert get_role_sessionmaker(role) is None


def test_aiven_role_resolves_correctly(aiven_sqlite):
    from app.db import roles as db_roles
    from app.db.session import get_role_engine

    eng = get_role_engine(db_roles.AIVEN)
    assert eng is not None
    with eng.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


def test_cockroach_role_resolves_correctly(monkeypatch):
    from app.db import roles as db_roles
    from app.db.session import get_role_engine

    monkeypatch.setenv("COCKROACH_DATABASE_URL", "sqlite:///./test_cockroach_role.db")
    eng = get_role_engine(db_roles.COCKROACH)
    assert eng is not None
    with eng.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


def test_turso_role_resolves_correctly(monkeypatch):
    from app.db import roles as db_roles
    from app.db.session import get_role_engine

    monkeypatch.setenv("TURSO_DATABASE_URL", "sqlite:///./test_turso_role.db")
    eng = get_role_engine(db_roles.TURSO)
    assert eng is not None
    with eng.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


def test_database_url_fallback_resolves_to_aiven(monkeypatch):
    from app.db import roles as db_roles
    from app.db.session import get_role_engine

    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_legacy_fallback.db")
    eng = get_role_engine(db_roles.AIVEN)
    assert eng is not None
    with eng.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    # ...while the other roles stay unconfigured despite DATABASE_URL being set.
    assert get_role_engine(db_roles.COCKROACH) is None
    assert get_role_engine(db_roles.TURSO) is None


def test_role_engines_are_cached_and_isolated(monkeypatch):
    from app.db import roles as db_roles
    from app.db.session import get_role_engine, reset_role_engines

    monkeypatch.setenv("AIVEN_DATABASE_URL", "sqlite:///./test_cache_aiven.db")
    e1 = get_role_engine(db_roles.AIVEN)
    assert e1 is not None
    assert get_role_engine(db_roles.AIVEN) is e1
    reset_role_engines()
    e2 = get_role_engine(db_roles.AIVEN)
    assert e2 is not None and e2 is not e1


def test_role_sessions_do_not_touch_legacy_primary_engine(aiven_sqlite):
    from app.db.session import engine, get_role_engine

    legacy_url = str(engine.url)
    role_eng = get_role_engine("aiven")
    assert role_eng is not None
    assert role_eng is not engine
    assert str(engine.url) == legacy_url


def test_session_scope_requires_configuration_and_cleans_up(monkeypatch):
    from app.db.session import session_scope_for_role

    with pytest.raises(RuntimeError, match="not configured"):
        with session_scope_for_role("cockroach"):
            pass

    monkeypatch.setenv("COCKROACH_DATABASE_URL", "sqlite:///./test_scope.db")
    with session_scope_for_role("cockroach") as db:
        assert db.execute(text("SELECT 1")).scalar() == 1
        assert db.in_transaction()
    assert not db.in_transaction()


def test_turso_libsql_url_normalization():
    from app.db.session import normalize_database_url

    assert normalize_database_url("libsql://db.turso.io") == "sqlite+libsql://db.turso.io"
    assert normalize_database_url("turso://db.turso.io") == "sqlite+libsql://db.turso.io"
    assert normalize_database_url("postgresql://u:p@h/db").startswith("postgresql+psycopg://")
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"


def test_libsql_driver_optional(monkeypatch):
    """A configured Turso URL with the driver missing degrades to None, no crash."""
    from app.db import roles as db_roles
    from app.db.session import get_role_engine, reset_role_engines

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://db.turso.io")
    real_create = __import__("sqlalchemy").create_engine
    missing_driver = sqlalchemy.exc.NoSuchModuleError("sqlite+libsql not installed")

    def fake_create_engine(url, *a, **kw):
        if str(url).startswith("sqlite+libsql"):
            raise missing_driver
        return real_create(url, *a, **kw)

    reset_role_engines()
    import app.db.session as session_mod

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(session_mod, "create_engine", fake_create_engine)
        assert get_role_engine(db_roles.TURSO) is None
    # Negative cache: the failed role stays resolved-to-None until reset.
    assert get_role_engine(db_roles.TURSO) is None
    reset_role_engines()
