"""Scratch tests for scripts/verify_migration.py — mismatch detection.

Builds source(neon)/aiven/cockroach scratch SQLite DBs from real ORM
metadata, runs the migration to populate them, then verifies pass/fail
outcomes, including missing destination rows, duplicate destination rows,
fixture boundary violations, and logical-reference orphans.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))

import verify_migration as vm  # noqa: E402
from migration_common import fixture_cutoff  # noqa: E402
from tests import migration_scratch as scratch  # noqa: E402
from tests.test_migrate_neon_split import _migrate_all  # noqa: E402


@pytest.fixture()
def verified_world(tmp_path):
    """A fully migrated scratch world (source -> aiven + cockroach)."""
    src = scratch.make_engine(str(tmp_path / "src.db"))
    aiven = scratch.make_engine(str(tmp_path / "aiven.db"))
    cock = scratch.make_engine(str(tmp_path / "cock.db"))
    scratch.create_source_schema(src)
    scratch.create_aiven_schema(aiven)
    scratch.create_cockroach_schema(cock)

    scratch.seed_fixture(src, 1, days_from_today=10)
    scratch.seed_fixture(src, 2, days_from_today=-30)
    scratch.seed_fixture(src, 3, days_from_today=0)
    scratch.seed_fixture(src, 4, days_from_today=-800)
    scratch.seed_fixture(src, 5, days_from_today=-900)
    scratch.seed_prediction(src, 101, 1)
    scratch.seed_prediction(src, 102, 2, is_published=False)
    scratch.seed_prediction(src, 103, 4)
    scratch.seed_odds(src, 201, 1, prediction_id=101)
    scratch.seed_feedback(src, 301, 101)
    scratch.seed_hist_eval(src, 401, 4)
    scratch.seed_hist_eval(src, 402, 5)
    scratch.seed_backtest(src, 501)

    _migrate_all(src, aiven, cock)
    yield src, aiven, cock
    src.dispose()
    aiven.dispose()
    cock.dispose()


def _new_report():
    return vm._empty_report()


# --------------------------------------------------------------------------- #


def test_clean_migration_passes_all_checks(verified_world):
    src, aiven, cock = verified_world
    report = _new_report()
    vm.run_all_checks(report, src, aiven, cock, fixture_cutoff(), set())
    assert report["mismatches"] == []


def test_missing_destination_rows_reported(verified_world):
    from sqlalchemy import text

    src, aiven, cock = verified_world
    with aiven.begin() as conn:
        conn.execute(text('DELETE FROM "predictions" WHERE "id" = 101'))
    report = _new_report()
    vm.run_all_checks(report, src, aiven, cock, fixture_cutoff(), set())
    assert any("predictions" in m and "missing" in m for m in report["mismatches"])
    assert report["pk"]["predictions"]["missing_total"] >= 1


def test_duplicate_destination_rows_reported(verified_world):
    from sqlalchemy import text

    src, aiven, cock = verified_world
    with aiven.begin() as conn:
        # force an extra row the source does not have (destination-only id)
        conn.execute(text(
            'INSERT INTO "predictions" (id, fixture_id, version, status, market, pick,'
            ' confidence, edge_score, risk_level, reasoning, is_premium, is_published,'
            ' created_at) SELECT 90999, fixture_id, version, status, market, pick,'
            ' confidence, edge_score, risk_level, reasoning, is_premium, is_published,'
            ' created_at FROM "predictions" WHERE id = 101'))
    report = _new_report()
    vm.run_all_checks(report, src, aiven, cock, fixture_cutoff(), set())
    # counts + pk both notice the extra row
    assert report["pk"]["predictions"]["extra_total"] >= 1
    assert any("predictions" in m and "no source" in m for m in report["mismatches"])


def test_fixture_boundary_violation_reported(verified_world):
    from datetime import date, timedelta

    from sqlalchemy import text

    src, aiven, cock = verified_world
    hot_date = (date.today() - timedelta(days=10)).isoformat()
    with cock.begin() as conn:
        # Double-home a hot fixture into the archive (boundary violation).
        conn.execute(text(
            'INSERT INTO "fixtures_archive" (id, sport, league, season, match_date,'
            " home_team, away_team, source, created_at) "
            "VALUES (1, 'soccer', 'E0', '2425', :d, 'Home FC', 'Away FC',"
            " 'test', :now)"), {"d": hot_date, "now": "2026-01-01 00:00:00"})
    report = _new_report()
    vm.run_all_checks(report, src, aiven, cock, fixture_cutoff(), set())
    assert any("double" in m or "BOTH" in m or "boundary" in m
               for m in report["mismatches"])


def test_logical_orphan_reported(verified_world):
    from sqlalchemy import text

    src, aiven, cock = verified_world
    with aiven.begin() as conn:
        conn.execute(text(
            'INSERT INTO "predictions" (id, fixture_id, version, status, market, pick,'
            ' confidence, edge_score, risk_level, reasoning, is_premium, is_published,'
            ' created_at) '
            "VALUES (555, 424242, 1, 'active', '1X2', 'X', 60, 0.1, 'Low',"
            " 'orphan', 0, 0, '2026-01-01 00:00:00')"))
    report = _new_report()
    vm.run_all_checks(report, src, aiven, cock, fixture_cutoff(), set())
    assert any("orphan" in m and "predictions.fixture_id" in m
               for m in report["mismatches"])


def test_report_and_exit_code_contract(verified_world):
    src, aiven, cock = verified_world
    report = _new_report()
    vm.run_all_checks(report, src, aiven, cock, fixture_cutoff(), set())
    text_report = vm.print_report(report)
    assert "MIGRATION VERIFICATION REPORT" in text_report
    assert "TOTAL MISMATCHES: 0" in text_report
    # contract mirroring main(): 0 mismatches -> exit 0
    assert (0 if not report["mismatches"] else 1) == 0


def test_check_smoke_returns_rows(verified_world):
    src, aiven, cock = verified_world
    report = _new_report()
    vm.check_smoke(report, aiven, fixture_cutoff())
    for name in ("dataframe_from_db", "records_map", "market_gate",
                 "active_model", "prediction_lookup"):
        assert report["smoke"][name]["ok"] is True
        assert report["smoke"][name]["rows_returned"] >= 0


def test_missing_destination_table_surfaces_as_mismatch(verified_world):
    from sqlalchemy import text

    src, aiven, cock = verified_world
    with aiven.begin() as conn:
        conn.execute(text('DROP TABLE "market_evidence"'))
    report = _new_report()
    vm.run_all_checks(report, src, aiven, cock, fixture_cutoff(), set())
    # some check failed instead of crashing the whole verifier
    assert report["mismatches"]