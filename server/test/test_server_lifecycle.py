"""Regression tests for bounded backend shutdown."""

import json
import sqlite3
from unittest.mock import MagicMock

import server_lifecycle


def test_request_shutdown_cancels_work_and_schedules_one_bounded_exit(mocker):
    stop_queue = mocker.patch("services.index.stop_index_queue", return_value=4)
    write_state = mocker.patch.object(server_lifecycle, "_write_session_state")
    thread = MagicMock()
    thread_factory = mocker.patch.object(
        server_lifecycle.threading, "Thread", return_value=thread
    )

    server_lifecycle.request_shutdown()

    assert server_lifecycle.GLOBAL_SHUTDOWN_EVENT.is_set()
    assert server_lifecycle.GLOBAL_CANCEL_EVENT.is_set()
    stop_queue.assert_called_once()
    write_state.assert_called_once_with("interrupted", True)
    thread_factory.assert_called_once()
    assert thread_factory.call_args.kwargs["daemon"] is True
    thread.start.assert_called_once()


def test_request_shutdown_is_idempotent(mocker):
    stop_queue = mocker.patch("services.index.stop_index_queue")
    thread = mocker.patch.object(server_lifecycle.threading, "Thread")
    server_lifecycle.GLOBAL_SHUTDOWN_EVENT.set()

    server_lifecycle.request_shutdown()

    stop_queue.assert_not_called()
    thread.assert_not_called()


def test_recover_catalog_session_marks_derived_state_stale(mocker, tmp_path):
    db_path = tmp_path / "styleai.db"
    db_path.mkdir()
    marker_path = db_path / "styleai-session.json"
    marker_path.write_text(
        json.dumps({"state": "running", "active_work": True}), encoding="utf-8"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE grouping_rule_state ("
        "rule_key TEXT PRIMARY KEY, rule_value TEXT, updated_at TEXT)"
    )
    mocker.patch.object(server_lifecycle.config, "DB_PATH", str(db_path))
    mocker.patch("services.style_catalog._ensure_initialized", return_value=connection)
    invalidate = mocker.patch(
        "services.style_upgrades.invalidate_upgrade_recommendations_cache"
    )

    assert server_lifecycle.recover_catalog_session() is True

    value = connection.execute(
        "SELECT rule_value FROM grouping_rule_state "
        "WHERE rule_key = 'NEEDS_REDISCOVERY'"
    ).fetchone()
    assert value == ("1",)
    invalidate.assert_called_once()
    assert json.loads(marker_path.read_text(encoding="utf-8"))["state"] == "running"


def test_scheduled_shutdown_removes_markers_before_forced_exit(mocker):
    mocker.patch("services.index.stop_index_queue")
    captured_target = {}

    class CapturingThread:
        def __init__(self, *, target, **_kwargs):
            captured_target["target"] = target

        def start(self):
            return None

    mocker.patch.object(server_lifecycle.threading, "Thread", CapturingThread)
    mocker.patch.object(server_lifecycle.time, "sleep")
    remove_pid = mocker.patch.object(server_lifecycle, "remove_pid_file")
    remove_ok = mocker.patch.object(server_lifecycle, "remove_ok_file")
    exit_process = mocker.patch.object(server_lifecycle.os, "_exit")

    server_lifecycle.request_shutdown()
    captured_target["target"]()

    remove_pid.assert_called_once()
    remove_ok.assert_called_once()
    exit_process.assert_called_once_with(0)
