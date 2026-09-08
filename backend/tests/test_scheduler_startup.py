"""Regression test for the scheduler import/function-name mismatch.

backend/app/services/scheduler_leader.py defines ``schedule_via_cron`` but
``scheduler.py`` once imported/called the non-existent ``scheduler_via_cron``,
which broke the scheduler startup path. These tests lock the correct name in
place and verify the startup/leader-election contract with mocks (no DB).
"""

import importlib
import os
from unittest import mock


EXPECTED_JOB_IDS = {
    "lightweight_refresh",
    "public_football_coverage",
    "score_sync",
    "live_events",
    "live_prediction_refresh",
    "learning_watch",
    "value_scan",
    "startup_refresh",
}


def _reload_scheduler(monkeypatch, **env):
    for key in ("SCHEDULE_VIA_CRON",):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import app.services.scheduler as scheduler_mod
    import app.services.scheduler_leader as leader_mod
    importlib.reload(leader_mod)
    importlib.reload(scheduler_mod)
    return scheduler_mod, leader_mod


def test_leader_module_exposes_schedule_via_cron_only():
    import app.services.scheduler_leader as leader_mod

    assert callable(leader_mod.schedule_via_cron)
    assert not hasattr(leader_mod, "scheduler_via_cron")


def test_scheduler_imports_exact_leader_function_name():
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "app" / "services" / "scheduler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.services.scheduler_leader":
            imported.update(a.asname or a.name for a in node.names)
    assert "schedule_via_cron" in imported
    assert "scheduler_via_cron" not in imported


def test_start_scheduler_registers_all_jobs_and_starts_as_leader(monkeypatch):
    scheduler_mod, leader_mod = _reload_scheduler(monkeypatch)
    with (
        mock.patch.object(leader_mod, "acquire_scheduler_leadership", return_value=True) as leadership,
        mock.patch("app.db.session.engine", new=mock.Mock()),
        mock.patch("apscheduler.schedulers.background.BackgroundScheduler.start") as start,
    ):
        sched = scheduler_mod.start_scheduler()
        assert sched is not None
        assert EXPECTED_JOB_IDS <= {job.id for job in sched.get_jobs()}
        leadership.assert_called_once()
        start.assert_called_once()
        if sched.state != 0:
            sched.shutdown(wait=False)


def test_start_scheduler_respects_schedule_via_cron(monkeypatch):
    scheduler_mod, leader_mod = _reload_scheduler(monkeypatch, SCHEDULE_VIA_CRON="1")
    # Even a granted advisory lock must not start the scheduler on a cron instance.
    with (
        mock.patch.object(leader_mod, "acquire_scheduler_leadership", return_value=True) as leadership,
        mock.patch("app.db.session.engine", new=mock.Mock()),
        mock.patch("apscheduler.schedulers.background.BackgroundScheduler.start") as start,
    ):
        assert scheduler_mod.start_scheduler() is None
        leadership.assert_not_called()
        start.assert_not_called()


def test_start_scheduler_non_leader_serves_api_without_starting(monkeypatch):
    scheduler_mod, leader_mod = _reload_scheduler(monkeypatch)
    with (
        mock.patch.object(leader_mod, "acquire_scheduler_leadership", return_value=False) as leadership,
        mock.patch("app.db.session.engine", new=mock.Mock()),
        mock.patch("apscheduler.schedulers.background.BackgroundScheduler.start") as start,
    ):
        assert scheduler_mod.start_scheduler() is None
        leadership.assert_called_once()
        start.assert_not_called()


def test_main_startup_gates_on_enable_scheduler_flag(monkeypatch):
    import app.main as main_mod

    assert hasattr(main_mod, "_finish_startup_once")

    # ENABLE_SCHEDULER=false (enable_scheduler=False): scheduler must not start,
    # but the API startup path still completes.
    monkeypatch.setattr(main_mod.settings, "enable_scheduler", False, raising=False)
    with mock.patch("app.services.scheduler.start_scheduler") as start:
        monkeypatch.setattr(main_mod, "_check_database_ready", lambda: True)
        monkeypatch.setattr(main_mod, "_startup_db_ready", False, raising=False)
        main_mod._finish_startup_once()
        start.assert_not_called()
    assert main_mod._startup_db_ready is True

    # ENABLE_SCHEDULER=true (enable_scheduler=True): the startup path must call
    # start_scheduler(). The scheduler body itself is mocked here; the real
    # start_scheduler() behaviour (import/jobs/leadership) is covered above.
    monkeypatch.setattr(main_mod.settings, "enable_scheduler", True, raising=False)
    with mock.patch("app.services.scheduler.start_scheduler") as start:
        monkeypatch.setattr(main_mod, "_check_database_ready", lambda: True)
        monkeypatch.setattr(main_mod, "_startup_db_ready", False, raising=False)
        main_mod._finish_startup_once()
        start.assert_called_once()

    # Production flag mapping check: pydantic-settings maps ENABLE_SCHEDULER env
    # to Settings.enable_scheduler.
    from app.core.config import Settings, get_settings

    monkeypatch.setenv("ENABLE_SCHEDULER", "true")
    get_settings.cache_clear()
    try:
        assert Settings().enable_scheduler is True
    finally:
        monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)
        get_settings.cache_clear()
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    get_settings.cache_clear()
    try:
        assert Settings().enable_scheduler is False
    finally:
        monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)
        get_settings.cache_clear()
