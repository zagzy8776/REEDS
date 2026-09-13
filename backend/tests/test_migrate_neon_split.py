"""End-to-end scratch tests for scripts/migrate_neon_split.py.

Uses scratch SQLite databases built from the REAL ORM metadata
(migration_scratch) — no network, no real credentials. Covers batching,
fixture 730-day split, idempotent resume, dry-run, PK/natural-key integrity,
and the fail-closed rules (missing URLs, missing destination tables, schema
mismatch).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))

import migrate_neon_split as mig  # noqa: E402
from migration_common import fixture_cutoff  # noqa: E402
from tests import migration_scratch as scratch  # noqa: E402


def _args(**overrides):
    base = {
        "dry_run": False,
        "dest": "both",
        "tables": "",
        "batch_size": 3,
        "limit_rows": 0,
        "start_after": "",
        "reference_date": "",
    }
    base.update(overrides)
    return type("Args", (), base)()


@pytest.fixture()
def scratch_dbs(tmp_path):
    """source(neon) + aiven + cockroach scratch SQLite databases."""
    src = scratch.make_engine(str(tmp_path / "src.db"))
    aiven = scratch.make_engine(str(tmp_path / "aiven.db"))
    cock = scratch.make_engine(str(tmp_path / "cock.db"))
    scratch.create_source_schema(src)
    scratch.create_aiven_schema(aiven)
    scratch.create_cockroach_schema(cock)
    yield src, aiven, cock
    src.dispose()
    aiven.dispose()
    cock.dispose()


@pytest.fixture()
def populated_source(scratch_dbs):
    """Neon source with 3 hot fixtures, 2 cold fixtures, predictions, odds,
    feedback, historical eval rows, backtests."""
    src, aiven, cock = scratch_dbs
    # Hot window (>= cutoff): fixtures 1-3
    scratch.seed_fixture(src, 1, days_from_today=10)
    scratch.seed_fixture(src, 2, days_from_today=-30)
    scratch.seed_fixture(src, 3, days_from_today=0)
    # Cold (< cutoff): fixtures 4-5
    scratch.seed_fixture(src, 4, days_from_today=-800)
    scratch.seed_fixture(src, 5, days_from_today=-900)
    # predictions
    scratch.seed_prediction(src, 101, 1)
    scratch.seed_prediction(src, 102, 2, is_published=False)
    scratch.seed_prediction(src, 103, 4)  # cold fixture id preserved as-is
    # odds / feedback
    scratch.seed_odds(src, 201, 1, prediction_id=101)
    scratch.seed_feedback(src, 301, 101)
    # cockroach role tables
    scratch.seed_hist_eval(src, 401, 4)
    scratch.seed_hist_eval(src, 402, 5)
    scratch.seed_backtest(src, 501)
    return src, aiven, cock


def _migrate_all(src, aiven, cock, **overrides):
    args = _args(**overrides)
    cutoff = fixture_cutoff()
    # Aiven tables
    for table in mig.MIGRATION_ORDER_AIVEN:
        if table == "fixtures":
            mig.migrate_table(src, aiven, "fixtures", "fixtures", args, cutoff, {})
        else:
            mig.migrate_table(src, aiven, table, table, args, cutoff, {})
    # Cockroach tables (fixtures_archive + cold/historical)
    mig.migrate_table(src, cock, "fixtures", "fixtures_archive", args, cutoff, {})
    mig.migrate_table(src, cock, "historical_evaluation", "historical_evaluation", args, cutoff, {})
    mig.migrate_table(src, cock, "backtest_runs", "backtest_runs", args, cutoff, {})


# --------------------------------------------------------------------------- #


def test_full_split_and_counts(populated_source):
    src, aiven, cock = populated_source
    _migrate_all(src, aiven, cock, batch_size=2)
    from sqlalchemy import text

    with aiven.connect() as conn:
        hot = conn.execute(text('SELECT COUNT(*) FROM "fixtures"')).scalar()
        preds = conn.execute(text('SELECT COUNT(*) FROM "predictions"')).scalar()
        odds = conn.execute(text('SELECT COUNT(*) FROM "odds_snapshots"')).scalar()
        fb = conn.execute(text('SELECT COUNT(*) FROM "model_feedback"')).scalar()
    with cock.connect() as conn:
        cold = conn.execute(text('SELECT COUNT(*) FROM "fixtures_archive"')).scalar()
        he = conn.execute(text('SELECT COUNT(*) FROM "historical_evaluation"')).scalar()
        bt = conn.execute(text('SELECT COUNT(*) FROM "backtest_runs"')).scalar()

    assert hot == 3
    assert cold == 2
    assert preds == 3
    assert odds == 1
    assert fb == 1
    assert he == 2
    assert bt == 1


def test_fixture_boundary_respected(scratch_dbs):
    src, aiven, cock = scratch_dbs
    scratch.seed_fixture(src, 1, days_from_today=10)    # hot
    scratch.seed_fixture(src, 2, days_from_today=-800)  # cold
    _migrate_all(src, aiven, cock)
    from sqlalchemy import text

    with aiven.connect() as conn:
        ids_hot = {r[0] for r in conn.execute(text('SELECT "id" FROM "fixtures"'))}
    with cock.connect() as conn:
        ids_cold = {r[0] for r in conn.execute(text('SELECT "id" FROM "fixtures_archive"'))}
    assert ids_hot == {1}
    assert ids_cold == {2}


def test_idempotent_rerun_no_duplicates(populated_source):
    src, aiven, cock = populated_source
    _migrate_all(src, aiven, cock)
    _migrate_all(src, aiven, cock)  # second run must not duplicate
    from sqlalchemy import text

    with aiven.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM "predictions"')).scalar() == 3
        assert conn.execute(text(
            'SELECT COUNT(*) FROM "predictions" GROUP BY id HAVING COUNT(*) > 1'
        )).scalar() is None
        assert conn.execute(text('SELECT COUNT(*) FROM "fixtures"')).scalar() == 3
    with cock.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM "fixtures_archive"')).scalar() == 2
        assert conn.execute(text('SELECT COUNT(*) FROM "historical_evaluation"')).scalar() == 2


def test_dry_run_performs_zero_writes(populated_source):
    src, aiven, cock = populated_source
    _migrate_all(src, aiven, cock, dry_run=True)
    from sqlalchemy import text

    with aiven.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM "predictions"')).scalar() == 0
        assert conn.execute(text('SELECT COUNT(*) FROM "fixtures"')).scalar() == 0
    with cock.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM "fixtures_archive"')).scalar() == 0


def test_resume_start_after_ignores_earlier_rows(scratch_dbs):
    src, aiven, cock = scratch_dbs
    for i in range(1, 8):
        # all within hot window: days -100..-700 are all >= -730
        scratch.seed_fixture(src, i, days_from_today=-i * 100)
    args = _args(start_after="fixtures:4")
    cutoff = fixture_cutoff()
    mig.migrate_table(src, aiven, "fixtures", "fixtures", args, cutoff, {})
    from sqlalchemy import text

    with aiven.connect() as conn:
        # resume starts after id 4: ids 5,6,7 (all hot) => 3 fixtures
        assert conn.execute(text('SELECT COUNT(*) FROM "fixtures"')).scalar() == 3
        ids = {r[0] for r in conn.execute(text('SELECT "id" FROM "fixtures"'))}
    assert ids == {5, 6, 7}
    # full run picks everything up
    _migrate_all(src, aiven, cock)
    with aiven.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM "fixtures"')).scalar() == 7


def test_limit_rows_caps_work(scratch_dbs):
    src, aiven, cock = scratch_dbs
    for i in range(1, 6):
        scratch.seed_fixture(src, i, days_from_today=-i * 100)
    args = _args(limit_rows=2)
    cutoff = fixture_cutoff()
    n = mig.migrate_table(src, aiven, "fixtures", "fixtures", args, cutoff, {})
    assert n == 2


def test_sanitize_row_applied_during_migrate(scratch_dbs):
    """model_artifacts rows land with data=b'' (metadata-only rule)."""
    from datetime import datetime

    src, aiven, cock = scratch_dbs
    with src.begin() as conn:
        conn.execute(
            scratch.Base.metadata.tables["model_artifacts"].insert().values(
                id=1, sport="soccer", filename="m.pkl", model_type="uploaded",
                accuracy=0.9, sample_size=100, data=b"THE-WHOLE-BUNDLE",
                created_at=datetime.utcnow()))
    args = _args()
    cutoff = fixture_cutoff()
    mig.migrate_table(src, aiven, "model_artifacts", "model_artifacts", args, cutoff, {})
    from sqlalchemy import text

    with aiven.connect() as conn:
        blob = conn.execute(text('SELECT "data" FROM "model_artifacts" WHERE "id" = 1')).scalar()
    assert blob == b""  # b"" exactly: metadata-only

# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


def test_missing_urls_fail_closed(monkeypatch):
    monkeypatch.delenv("SOURCE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AIVEN_DATABASE_URL", raising=False)
    monkeypatch.delenv("COCKROACH_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        mig.main(["--dry-run"])
    assert "FAIL-CLOSED" in str(exc.value)


def test_missing_cockroach_url_fails_closed_even_with_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///legacy-neon.db")
    monkeypatch.setenv("AIVEN_DATABASE_URL", "sqlite:///a.db")
    monkeypatch.delenv("SOURCE_DATABASE_URL", raising=False)
    monkeypatch.delenv("COCKROACH_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        mig.main(["--dest", "both", "--dry-run"])
    assert "COCKROACH_DATABASE_URL" in str(exc.value)


def test_dest_validation_fails_unknown_tables(scratch_dbs, monkeypatch):
    src, aiven, cock = scratch_dbs
    monkeypatch.setenv("SOURCE_DATABASE_URL", f"sqlite:///{src.url.database}")
    monkeypatch.setenv("AIVEN_DATABASE_URL", f"sqlite:///{aiven.url.database}")
    monkeypatch.setenv("COCKROACH_DATABASE_URL", f"sqlite:///{cock.url.database}")
    with pytest.raises(SystemExit) as exc:
        mig.main(["--tables", "definitely_not_a_table", "--dry-run"])
    assert "FAIL-CLOSED" in str(exc.value)


def test_missing_destination_table_fails_closed(scratch_dbs):
    src, aiven, cock = scratch_dbs
    scratch.seed_fixture(src, 1, days_from_today=10)
    # Drop destination predictions table to simulate un-applied baseline.
    from sqlalchemy import text

    with aiven.begin() as conn:
        conn.execute(text('DROP TABLE "predictions"'))
    with pytest.raises(SystemExit) as exc:
        mig.migrate_table(src, aiven, "predictions", "predictions",
                          _args(), fixture_cutoff(), {})
    assert "FAIL-CLOSED" in str(exc.value)


def test_batch_size_must_be_positive():
    with pytest.raises(SystemExit) as exc:
        mig.main(["--batch-size", "0", "--dry-run"])
    assert "FAIL-CLOSED" in str(exc.value)