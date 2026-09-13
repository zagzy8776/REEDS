"""Phase 1 tests: database role registry (no live database connections).

Covers:
- env resolution precedence (role var > DATABASE_URL fallback > None)
- DATABASE_URL fallback resolves to the Aiven role only
- invalid roles raise instead of resolving
- the table-role registry covers every SQLAlchemy-mapped table exactly once
- Turso owns no ORM tables
- the redacted diagnostics never leak URL values
"""

import pytest


@pytest.fixture(autouse=True)
def _clean_role_env(monkeypatch):
    """Isolate every test from the ambient environment."""
    for var in ("AIVEN_DATABASE_URL", "COCKROACH_DATABASE_URL", "TURSO_DATABASE_URL", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_aiven_role_resolves_from_own_var():
    from app.db import roles

    urls = roles.resolve_role_urls({"AIVEN_DATABASE_URL": "postgresql://u:p@aiven-host/db"})
    assert urls[roles.AIVEN] == "postgresql://u:p@aiven-host/db"
    assert roles.active_roles({"AIVEN_DATABASE_URL": "postgresql://u:p@aiven/db"}) == [roles.AIVEN]


def test_aiven_falls_back_to_legacy_database_url():
    from app.db import roles

    env = {"DATABASE_URL": "postgresql://u:p@neon-host/db"}
    assert roles.resolve_role_url(roles.AIVEN, env) == "postgresql://u:p@neon-host/db"
    # Missing legacy var leaves the role unconfigured (no crash, no invented URL).
    assert roles.resolve_role_urls({}) == {
        roles.AIVEN: None,
        roles.COCKROACH: None,
        roles.TURSO: None,
    }


def test_cockroach_and_turso_never_fall_back_to_database_url():
    from app.db import roles

    legacy = {"DATABASE_URL": "postgresql://u:p@legacy/db"}
    assert roles.resolve_role_url(roles.COCKROACH, legacy) is None
    assert roles.resolve_role_url(roles.TURSO, legacy) is None

    env = {
        "DATABASE_URL": "postgresql://u:p@legacy/db",
        "COCKROACH_DATABASE_URL": "postgresql://u:p@crdb/db",
        "TURSO_DATABASE_URL": "libsql://db.turso.io",
    }
    assert roles.resolve_role_url(roles.COCKROACH, env) == "postgresql://u:p@crdb/db"
    assert roles.resolve_role_url(roles.TURSO, env) == "libsql://db.turso.io"


def test_blank_env_values_are_treated_as_unset():
    from app.db import roles

    env = {"AIVEN_DATABASE_URL": "   ", "DATABASE_URL": "postgresql://u:p@legacy/db"}
    assert roles.resolve_role_url(roles.AIVEN, env) == "postgresql://u:p@legacy/db"


@pytest.mark.parametrize("role", ["mysql", "", "Aiven", "aiven "])
def test_unknown_role_raises(role):
    from app.db import roles

    with pytest.raises(ValueError):
        roles.resolve_role_url(role, {})
    with pytest.raises(ValueError):
        roles.env_var_for_role(role)


def test_describe_resolution_reports_fallback_without_values():
    from app.db import roles

    env = {"DATABASE_URL": "postgresql://u:p@secret-host/db"}
    report = roles.describe_resolution(env)
    assert report[roles.AIVEN] == "fallback to DATABASE_URL"
    assert report[roles.COCKROACH] == "not configured"
    assert "secret-host" not in str(report)

    env = {"AIVEN_DATABASE_URL": "postgresql://u:p@secret-host/db"}
    assert roles.describe_resolution(env)[roles.AIVEN] == "configured via AIVEN_DATABASE_URL"


def test_table_role_registry_matches_orm_metadata():
    from app.db import models, rejected_fixture, roles  # noqa: F401

    mapped = set(models.Base.metadata.tables.keys())
    registered = set(roles.TABLE_ROLES)
    assert registered == mapped, (
        f"registry drift: unregistered={sorted(mapped - registered)} unknown={sorted(registered - mapped)}"
    )


def test_every_table_has_exactly_one_relational_owner():
    from app.db import roles

    for table, role in roles.TABLE_ROLES.items():
        assert role in roles.RELATIONAL_ROLES, f"{table} assigned to non-relational role {role}"


def test_turso_owns_no_orm_tables():
    from app.db import roles

    assert roles.TURSO not in set(roles.TABLE_ROLES.values())
    assert set(roles.tables_for_role(roles.TURSO)) <= set(roles.TURSO_KEYS)


def test_hot_window_constant_matches_request_time_history():
    from app.db import roles
    from app.services.predictions import PREDICTION_HISTORY_DAYS

    assert roles.FIXTURES_HOT_WINDOW_DAYS == PREDICTION_HISTORY_DAYS == 730
