"""Pure-helper tests for the Phase 2 migration toolkit (no databases)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest


def _common():
    import migration_common as mc
    return mc


# --------------------------------------------------------------------------- #
# Batching
# --------------------------------------------------------------------------- #


def test_batched_yields_fixed_sizes():
    mc = _common()
    rows = [{"id": i} for i in range(7)]
    batches = list(mc.batched(rows, 3))
    assert [len(b) for b in batches] == [3, 3, 1]
    assert [b[0]["id"] for b in batches] == [0, 3, 6]


def test_batched_empty():
    mc = _common()
    assert list(mc.batched([], 5)) == []


def test_batched_invalid_size_raises():
    mc = _common()
    with pytest.raises(ValueError):
        list(mc.batched([{"id": 1}], 0))


# --------------------------------------------------------------------------- #
# Fixture cutoff / destination
# --------------------------------------------------------------------------- #


def test_fixture_cutoff_is_deterministic():
    mc = _common()
    ref = date(2026, 9, 12)
    cutoff = mc.fixture_cutoff(ref)
    assert cutoff == ref - timedelta(days=mc.FIXTURES_HOT_WINDOW_DAYS)
    assert mc.FIXTURES_HOT_WINDOW_DAYS == 730


def test_fixture_destination_boundary_inclusive():
    mc = _common()
    cutoff = date(2026, 1, 1)
    # On the cutoff -> Aiven (hot, inclusive boundary: no row is lost)
    assert mc.fixture_destination(cutoff, cutoff) == "aiven"
    # One day older -> Cockroach
    assert mc.fixture_destination(date(2025, 12, 31), cutoff) == "cockroach"
    # Far in the future -> Aiven
    assert mc.fixture_destination(date(2030, 1, 1), cutoff) == "aiven"


def test_fixture_destination_accepts_strings_and_none():
    mc = _common()
    cutoff = date(2026, 1, 1)
    assert mc.fixture_destination("2026-01-01", cutoff) == "aiven"
    assert mc.fixture_destination("2025-12-31T23:59:59", cutoff) == "cockroach"
    assert mc.fixture_destination("not-a-date", cutoff) == "aiven"
    # NULL match_date -> Aiven (live board is the safe side), flagged by verifier
    assert mc.fixture_destination(None, cutoff) == "aiven"
    assert mc.fixture_destination(datetime(2025, 12, 31, 12, 0), cutoff) == "cockroach"


# --------------------------------------------------------------------------- #
# Sanitization / redaction
# --------------------------------------------------------------------------- #


def test_sanitize_row_forces_model_artifacts_data_empty():
    mc = _common()
    row = {"id": 1, "sport": "soccer", "filename": "a.pkl", "data": b"x" * 100}
    out = mc.sanitize_row("model_artifacts", row)
    assert out["data"] == b""
    # other columns untouched
    assert out["id"] == 1 and out["filename"] == "a.pkl"


def test_sanitize_row_other_tables_untouched():
    mc = _common()
    row = {"id": 1, "data": b"keep-me"}
    assert mc.sanitize_row("predictions", row) == row


def test_redact_url_never_leaks_credentials():
    mc = _common()
    url = "postgresql://leek-user:SUPER-SECRET-PASSWORD@db.example.com:5432/reeds?sslmode=require"
    redacted = mc.redact_url(url)
    assert "SUPER-SECRET-PASSWORD" not in redacted
    assert "leek-user" not in redacted
    assert "db.example.com" in redacted
    assert redacted.startswith("postgresql://***@")


def test_redact_url_missing_and_broken():
    mc = _common()
    assert mc.redact_url("") == "<missing>"
    assert mc.redact_url(None) == "<missing>"
    assert "<unparseable>" in mc.redact_url("postgresql://user:pass@[broken")


def test_redact_mapping_covers_secret_key_names():
    mc = _common()
    data = {
        "username": "alice",
        "password": "hunter2",
        "DATABASE_URL": "postgresql://u:p@h/db",
        "api_key": "sk-123",
        "note": "hello",
    }
    out = mc.redact_mapping(data)
    assert out["username"] == "alice"
    assert out["note"] == "hello"
    assert out["password"] == "***REDACTED***"
    assert out["DATABASE_URL"] == "***REDACTED***"
    assert out["api_key"] == "***REDACTED***"
    # originals untouched
    assert data["password"] == "hunter2"


# --------------------------------------------------------------------------- #
# Registry drift vs roles.py (single source of truth)
# --------------------------------------------------------------------------- #


def test_table_registry_matches_roles_phase1():
    """AIVEN_TABLES + (COCKROACH_TABLES minus fixtures_archive) must equal
    roles.TABLE_ROLES keys. A new ORM table without an owner fails this."""
    mc = _common()
    from app.db import roles as db_roles

    expected_aiven = sorted(t for t, r in db_roles.TABLE_ROLES.items() if r == db_roles.AIVEN)
    expected_cockroach = sorted(t for t, r in db_roles.TABLE_ROLES.items() if r == db_roles.COCKROACH)

    assert sorted(mc.AIVEN_TABLES) == expected_aiven
    assert sorted(t for t in mc.COCKROACH_TABLES if t != "fixtures_archive") == expected_cockroach


def test_fixtures_hot_window_matches_roles_constant():
    mc = _common()
    from app.db import roles as db_roles

    assert mc.FIXTURES_HOT_WINDOW_DAYS == db_roles.FIXTURES_HOT_WINDOW_DAYS
    assert mc.FIXTURES_HOT_WINDOW_DAYS == 730


def test_natural_keys_cover_every_registered_table():
    mc = _common()
    for table in mc.AIVEN_TABLES:
        assert table in mc.NATURAL_KEYS, f"{table} missing from NATURAL_KEYS"
    for table in mc.COCKROACH_TABLES:
        assert table in mc.NATURAL_KEYS, f"{table} missing from NATURAL_KEYS"


def test_logical_refs_cover_required_relationships():
    mc = _common()
    pairs = {(ref[0], ref[1]) for ref in mc.LOGICAL_REFS}
    required = {
        ("predictions", "fixture_id"),
        ("odds_snapshots", "fixture_id"),
        ("odds_snapshots", "prediction_id"),
        ("model_feedback", "prediction_id"),
        ("model_feedback", "fixture_id"),
        ("historical_evaluation", "fixture_id"),
        ("user_predictions", "fixture_id"),
        ("match_events", "fixture_id"),
        ("match_lineups", "fixture_id"),
        ("insider_signals", "fixture_id"),
        ("team_aliases", "team_id"),
        ("community_comments", "prediction_id"),
        ("community_reactions", "prediction_id"),
        ("community_plays", "prediction_id"),
        ("win_slips", "prediction_id"),
    }
    assert required <= pairs