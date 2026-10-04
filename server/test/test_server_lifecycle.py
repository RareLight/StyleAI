"""Regression tests for bounded backend shutdown."""

import json
import subprocess
import sys
import threading
import time
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import server_lifecycle


def _capture_shutdown_threads(mocker):
    """Replace ``threading.Thread`` so shutdown daemons can be run in order."""
    captured = []

    class CapturingThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.started = False
            captured.append(self)

        def start(self):
            self.started = True

    mocker.patch.object(server_lifecycle.threading, "Thread", CapturingThread)
    return captured


def _run_captured(captured, name):
    for thread in captured:
        if thread.kwargs.get("name") == name:
            thread.kwargs["target"]()
            return
    raise AssertionError(f"shutdown thread {name} was never created")


def _stub_process(
    name="",
    executable="",
    username="testuser",
    pid=4242,
    running=True,
    status="running",
    create_time=1000.0,
):
    process = MagicMock()
    process.info = {"name": name}
    process.pid = pid
    process.exe.return_value = executable
    process.username.return_value = username
    process.create_time.return_value = create_time
    process.is_running.return_value = running
    process.status.return_value = status
    return process


def _patch_process_scan(mocker, processes, username="testuser"):
    self_process = MagicMock()
    self_process.username.return_value = username
    mocker.patch.object(server_lifecycle.psutil, "Process", return_value=self_process)
    return mocker.patch.object(
        server_lifecycle.psutil, "process_iter", return_value=list(processes)
    )


def test_request_shutdown_cancels_work_and_schedules_bounded_exit(mocker):
    stop_queue = mocker.patch("services.index.stop_index_queue", return_value=4)
    write_state = mocker.patch.object(server_lifecycle, "_write_session_state")
    captured = _capture_shutdown_threads(mocker)

    server_lifecycle.request_shutdown()

    assert server_lifecycle.GLOBAL_SHUTDOWN_EVENT.is_set()
    assert server_lifecycle.GLOBAL_CANCEL_EVENT.is_set()
    assert len(captured) == 2
    assert captured[0].kwargs["name"] == "styleai-shutdown-deadline"
    assert captured[1].kwargs["name"] == "styleai-shutdown-cleanup"
    for thread in captured:
        assert thread.kwargs["daemon"] is True
        assert thread.started is True
    stop_queue.assert_not_called()
    write_state.assert_not_called()

    _run_captured(captured, "styleai-shutdown-cleanup")

    stop_queue.assert_called_once()
    write_state.assert_called_once_with("interrupted", True)


def test_request_shutdown_is_idempotent(mocker):
    stop_queue = mocker.patch("services.index.stop_index_queue")
    thread = mocker.patch.object(server_lifecycle.threading, "Thread")
    server_lifecycle.GLOBAL_SHUTDOWN_EVENT.set()

    server_lifecycle.request_shutdown()

    stop_queue.assert_not_called()
    thread.assert_not_called()


def test_request_shutdown_claims_once_under_concurrent_callers(mocker):
    mocker.patch("services.index.stop_index_queue")
    mocker.patch.object(server_lifecycle, "_write_session_state")
    mocker.patch.object(server_lifecycle, "remove_pid_file")
    mocker.patch.object(server_lifecycle, "remove_ok_file")
    mocker.patch.object(server_lifecycle.time, "sleep")
    exit_process = mocker.patch.object(server_lifecycle.os, "_exit")

    callers = [
        threading.Thread(target=server_lifecycle.request_shutdown) for _ in range(8)
    ]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=5)
    assert all(not caller.is_alive() for caller in callers)

    idle = threading.Event()
    deadline = time.monotonic() + 5
    while not exit_process.called and time.monotonic() < deadline:
        idle.wait(0.01)
    exit_process.assert_called_once_with(0)


def test_idle_shutdown_requires_timeout_and_no_live_work(mocker):
    mocker.patch.object(server_lifecycle, "IDLE_SHUTDOWN_SECONDS", 600)
    mocker.patch.object(server_lifecycle, "_last_request_time", 1_000.0)
    has_live_work = mocker.patch.object(
        server_lifecycle, "_has_live_work", return_value=False
    )

    assert server_lifecycle._idle_shutdown_due(now=1_599.0) is False
    assert server_lifecycle._idle_shutdown_due(now=1_600.0) is True
    has_live_work.return_value = True
    assert server_lifecycle._idle_shutdown_due(now=2_000.0) is False


def test_active_client_request_blocks_idle_shutdown_and_refreshes_on_completion(
    mocker,
):
    mocker.patch.object(server_lifecycle, "IDLE_SHUTDOWN_SECONDS", 600)
    mocker.patch.object(server_lifecycle, "_last_request_time", 1_000.0)
    mocker.patch.object(server_lifecycle, "_active_client_requests", 0)
    clock = mocker.patch.object(server_lifecycle.time, "time", return_value=1_601.0)

    server_lifecycle.begin_client_request()

    assert server_lifecycle._idle_shutdown_due(now=10_000.0) is False
    clock.return_value = 2_000.0
    server_lifecycle.end_client_request()
    assert server_lifecycle._active_client_requests == 0
    assert server_lifecycle._idle_shutdown_due(now=2_599.0) is False
    assert server_lifecycle._idle_shutdown_due(now=2_600.0) is True


def test_idle_shutdown_can_be_disabled(mocker):
    mocker.patch.object(server_lifecycle, "IDLE_SHUTDOWN_SECONDS", 0)
    has_live_work = mocker.patch.object(server_lifecycle, "_has_live_work")

    assert server_lifecycle._idle_shutdown_due(now=10_000.0) is False
    has_live_work.assert_not_called()


def test_recover_catalog_session_recovers_incomplete_policy_builds(mocker, tmp_path):
    db_path = tmp_path / "styleai.db"
    db_path.mkdir()
    marker_path = db_path / "styleai-session.json"
    marker_path.write_text(
        json.dumps({"state": "running", "active_work": True}), encoding="utf-8"
    )
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = ("ok",)
    mocker.patch.object(server_lifecycle.config, "DB_PATH", str(db_path))
    mocker.patch(
        "services.policy_store.connect_policy_store",
        return_value=connection,
    )
    recover = mocker.patch("services.policy_store.recover_incomplete_generations")
    invalidate = mocker.patch("services.policy_runtime.invalidate_runtime_cache")
    recover_operations = mocker.patch(
        "services.operations.recover_interrupted_jobs", return_value=1
    )

    assert server_lifecycle.recover_catalog_session() is True

    recover.assert_called_once_with(connection)
    invalidate.assert_called_once()
    recover_operations.assert_called_once_with(str(db_path))
    connection.close.assert_called_once()
    assert json.loads(marker_path.read_text(encoding="utf-8"))["state"] == "running"


def test_recover_catalog_session_is_idempotent_for_current_process(mocker, tmp_path):
    db_path = tmp_path / "styleai.db"
    db_path.mkdir()
    marker_path = db_path / "styleai-session.json"
    marker_path.write_text(
        json.dumps(
            {
                "state": "running",
                "active_work": False,
                "pid": server_lifecycle.os.getpid(),
                "process_token": server_lifecycle._PROCESS_TOKEN,
            }
        ),
        encoding="utf-8",
    )
    mocker.patch.object(server_lifecycle.config, "DB_PATH", str(db_path))
    connect = mocker.patch("services.policy_store.connect_policy_store")
    invalidate = mocker.patch("services.policy_runtime.invalidate_runtime_cache")

    assert server_lifecycle.recover_catalog_session() is False

    connect.assert_not_called()
    invalidate.assert_not_called()
    assert json.loads(marker_path.read_text(encoding="utf-8"))["state"] == "running"


def test_marker_cleanup_does_not_remove_newer_process_ownership(mocker, tmp_path):
    db_path = tmp_path / "styleai.db"
    db_path.mkdir()
    mocker.patch.object(server_lifecycle.config, "DB_PATH", str(db_path))
    pid_path = tmp_path / "styleai-server.pid"
    ok_path = tmp_path / "styleai-server.OK"
    pid_path.write_text("999999\n", encoding="utf-8")
    ok_path.write_text("OK 999999 newer-token\n", encoding="utf-8")

    server_lifecycle.remove_pid_file()
    server_lifecycle.remove_ok_file()

    assert pid_path.exists()
    assert ok_path.exists()


def test_marker_cleanup_removes_files_owned_by_current_process(mocker, tmp_path):
    db_path = tmp_path / "styleai.db"
    db_path.mkdir()
    mocker.patch.object(server_lifecycle.config, "DB_PATH", str(db_path))
    server_lifecycle.write_pid_file()
    server_lifecycle.write_ok_file()

    server_lifecycle.remove_pid_file()
    server_lifecycle.remove_ok_file()

    assert (tmp_path / "styleai-server.pid").exists() is False
    assert (tmp_path / "styleai-server.OK").exists() is False


def test_tokenizer_fallback_does_not_reload_vision_model(mocker, tmp_path):
    cached_dir = tmp_path / "model"
    cached_dir.mkdir()
    weights = cached_dir / "open_clip_model.safetensors"
    config_file = cached_dir / "open_clip_config.json"
    weights.write_bytes(b"weights")
    config_file.write_text("{}", encoding="utf-8")

    model_obj = MagicMock()
    processor = MagicMock()
    fallback_tokenizer = MagicMock()
    create_model = mocker.patch(
        "open_clip.create_model_and_transforms",
        return_value=(model_obj, None, processor),
    )
    mocker.patch.object(server_lifecycle, "hf_hub_download", return_value=str(weights))
    mocker.patch.object(
        server_lifecycle,
        "_get_open_clip_tokenizer",
        side_effect=RuntimeError("built-in tokenizer lookup failed"),
    )
    fallback = mocker.patch("open_clip.get_tokenizer", return_value=fallback_tokenizer)
    mocker.patch(
        "utils.open_clip_compat.wrap_tokenizer", side_effect=lambda value: value
    )
    mocker.patch.object(server_lifecycle, "get_torch_device", return_value="cpu")
    server_lifecycle.model = None
    server_lifecycle.processor = None
    server_lifecycle.tokenizer = None
    server_lifecycle._model_load_error = "earlier transient failure"

    server_lifecycle.load_model()

    create_model.assert_called_once()
    fallback.assert_called_once()
    assert server_lifecycle.model is model_obj
    assert server_lifecycle.processor is processor
    assert server_lifecycle.tokenizer is fallback_tokenizer
    assert server_lifecycle._model_load_error is None

    server_lifecycle.model = None
    server_lifecycle.processor = None
    server_lifecycle.tokenizer = None


def test_cached_model_check_does_not_wait_for_model_load_lock(mocker, tmp_path):
    cached_dir = tmp_path / "model"
    cached_dir.mkdir()
    weights = cached_dir / "open_clip_model.safetensors"
    weights.write_bytes(b"weights")
    (cached_dir / "open_clip_config.json").write_text("{}", encoding="utf-8")
    model_lock = MagicMock()
    mocker.patch.object(server_lifecycle, "_model_lock", model_lock)
    mocker.patch.object(server_lifecycle, "model", None)
    mocker.patch.object(server_lifecycle, "hf_hub_download", return_value=str(weights))

    assert server_lifecycle.is_model_cached() is True
    model_lock.__enter__.assert_not_called()


def test_scheduled_shutdown_removes_markers_before_forced_exit(mocker):
    mocker.patch("services.index.stop_index_queue")
    captured = _capture_shutdown_threads(mocker)
    mocker.patch.object(server_lifecycle.time, "sleep")
    remove_pid = mocker.patch.object(server_lifecycle, "remove_pid_file")
    remove_ok = mocker.patch.object(server_lifecycle, "remove_ok_file")
    exit_process = mocker.patch.object(server_lifecycle.os, "_exit")

    server_lifecycle.request_shutdown()
    _run_captured(captured, "styleai-shutdown-cleanup")
    _run_captured(captured, "styleai-shutdown-deadline")

    remove_pid.assert_called_once()
    remove_ok.assert_called_once()
    exit_process.assert_called_once_with(0)


def test_sigterm_requests_graceful_shutdown(mocker):
    register = mocker.patch.object(server_lifecycle.signal, "signal")
    shutdown = mocker.patch.object(server_lifecycle, "request_shutdown")

    server_lifecycle.install_signal_handlers()

    register.assert_called_once()
    registered_signal, handler = register.call_args.args
    assert registered_signal == server_lifecycle.signal.SIGTERM
    handler(registered_signal, None)
    shutdown.assert_called_once_with()


def test_is_lightroom_process_matches_macos_main_binary(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    assert server_lifecycle._is_lightroom_process(
        "Adobe Lightroom Classic",
        "/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/MacOS/Adobe Lightroom Classic",
    )
    assert server_lifecycle._is_lightroom_process(
        "Adobe Lightroom Classic",
        "/Applications/Adobe Lightroom Classic 14/Adobe Lightroom Classic.app/Contents/MacOS/Adobe Lightroom Classic",
    )
    assert not server_lifecycle._is_lightroom_process(
        "Adobe Lightroom Classic Helper",
        "/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/Helpers/Adobe Lightroom Classic Helper",
    )
    assert not server_lifecycle._is_lightroom_process(
        "Adobe Crash Reporter",
        "/Applications/Adobe Lightroom Classic/Adobe Crash Reporter.app/Contents/MacOS/Adobe Crash Reporter",
    )
    assert not server_lifecycle._is_lightroom_process(
        "Adobe Lightroom Classic",
        "/Applications/Adobe Lightroom/Adobe Lightroom.app/Contents/MacOS/Adobe Lightroom",
    )
    assert not server_lifecycle._is_lightroom_process(
        "Adobe Lightroom Classic", "Adobe Lightroom Classic"
    )
    assert not server_lifecycle._is_lightroom_process("node", "/usr/bin/node")


def test_is_lightroom_process_matches_windows_classic_only(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "win32")
    assert server_lifecycle._is_lightroom_process(
        "Lightroom.exe",
        "C:\\Program Files\\Adobe\\Adobe Lightroom Classic\\Lightroom.exe",
    )
    assert not server_lifecycle._is_lightroom_process(
        "Lightroom.exe",
        "C:\\Program Files\\Adobe\\Adobe Lightroom CC\\Lightroom.exe",
    )
    assert not server_lifecycle._is_lightroom_process(
        "Lightroom.exe",
        "C:\\Program Files\\Adobe\\Adobe Lightroom\\Lightroom.exe",
    )
    assert not server_lifecycle._is_lightroom_process(
        "Adobe Lightroom Classic Helper.exe",
        "C:\\Program Files\\Adobe\\Adobe Lightroom Classic\\Helper.exe",
    )
    assert not server_lifecycle._is_lightroom_process("Lightroom.exe", "Lightroom.exe")


def test_is_lightroom_process_ignores_unsupported_platforms(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "linux")
    assert not server_lifecycle._is_lightroom_process(
        "Adobe Lightroom Classic",
        "/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/MacOS/Adobe Lightroom Classic",
    )


def test_is_lightroom_managed_via_flag(mocker, monkeypatch):
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    mocker.patch.object(server_lifecycle.config.args, "lightroom_managed", False)
    assert server_lifecycle.is_lightroom_managed() is False
    mocker.patch.object(server_lifecycle.config.args, "lightroom_managed", True)
    assert server_lifecycle.is_lightroom_managed() is True


def test_is_lightroom_managed_via_exact_legacy_launchd_label(mocker, monkeypatch):
    mocker.patch.object(server_lifecycle.config.args, "lightroom_managed", False)
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.styleai.server")
    assert server_lifecycle.is_lightroom_managed() is True
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.styleai.server.dev.1791036054")
    assert server_lifecycle.is_lightroom_managed() is True
    for label in (
        "com.styleai.server.dev.",
        "com.styleai.server.dev.abc",
        "xcom.styleai.server.dev.123",
        "com.styleai.server.session.123",
        "com.styleai.other",
        "unrelated",
        "",
    ):
        monkeypatch.setenv("XPC_SERVICE_NAME", label)
        assert server_lifecycle.is_lightroom_managed() is False, label


def test_lightroom_scan_pins_only_same_user_main_process(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle, "_lightroom_owner", None)
    other_user = _stub_process(
        name="Adobe Lightroom Classic",
        executable="/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/MacOS/Adobe Lightroom Classic",
        username="someoneelse",
        pid=7777,
    )
    helper = _stub_process(
        name="Adobe Lightroom Classic Helper",
        executable="/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/Helpers/Adobe Lightroom Classic Helper",
        pid=7778,
    )
    owner = _stub_process(
        name="Adobe Lightroom Classic",
        executable="/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/MacOS/Adobe Lightroom Classic",
        pid=4242,
    )
    _patch_process_scan(mocker, [other_user, helper, owner])

    assert server_lifecycle._lightroom_exit_due() is False

    assert server_lifecycle._lightroom_owner is owner
    owner.create_time.assert_called_once()


def test_managed_startup_without_lightroom_is_exit_due(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle.config.args, "lightroom_managed", True)
    mocker.patch.object(server_lifecycle, "_lightroom_owner", None)
    _patch_process_scan(mocker, [_stub_process(name="Safari", pid=1)])

    assert server_lifecycle.is_lightroom_managed() is True
    assert server_lifecycle._lightroom_exit_due() is True


def test_pinned_live_owner_does_not_trigger_exit(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    owner = _stub_process(
        name="Adobe Lightroom Classic", running=True, status="running"
    )
    mocker.patch.object(server_lifecycle, "_lightroom_owner", owner)

    assert server_lifecycle._lightroom_exit_due() is False


def test_pinned_owner_loss_exits_even_while_busy(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle, "IDLE_SHUTDOWN_SECONDS", 0)
    mocker.patch.object(server_lifecycle, "_active_client_requests", 1)
    mocker.patch.object(server_lifecycle, "_has_live_work", return_value=True)
    owner = _stub_process(name="Adobe Lightroom Classic", running=False)
    mocker.patch.object(server_lifecycle, "_lightroom_owner", owner)
    _patch_process_scan(
        mocker,
        [
            _stub_process(
                name="Adobe Lightroom Classic",
                executable="/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/MacOS/Adobe Lightroom Classic",
                pid=9999,
            )
        ],
    )

    assert server_lifecycle._lightroom_exit_due() is True
    assert server_lifecycle._lightroom_owner is owner


def test_pinned_owner_zombie_triggers_exit(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    owner = _stub_process(
        name="Adobe Lightroom Classic",
        running=True,
        status=server_lifecycle.psutil.STATUS_ZOMBIE,
    )
    mocker.patch.object(server_lifecycle, "_lightroom_owner", owner)

    assert server_lifecycle._lightroom_exit_due() is True


def test_pinned_owner_pid_reuse_triggers_exit(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    owner = _stub_process(name="Adobe Lightroom Classic", running=False)
    mocker.patch.object(server_lifecycle, "_lightroom_owner", owner)
    _patch_process_scan(mocker, [])

    assert server_lifecycle._lightroom_exit_due() is True


def test_owner_inspection_errors_retry_without_exiting(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle, "_lightroom_owner", None)
    denied = _stub_process(name="Adobe Lightroom Classic")
    denied.exe.side_effect = server_lifecycle.psutil.AccessDenied(pid=4242)
    _patch_process_scan(mocker, [denied])

    assert server_lifecycle._lightroom_exit_due() is False
    assert server_lifecycle._lightroom_owner is None


def test_owner_recheck_errors_retry_without_exiting(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    owner = _stub_process(name="Adobe Lightroom Classic")
    owner.is_running.side_effect = server_lifecycle.psutil.AccessDenied(pid=4242)
    mocker.patch.object(server_lifecycle, "_lightroom_owner", owner)

    assert server_lifecycle._lightroom_exit_due() is False


def test_denied_unrelated_processes_do_not_hide_absence(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle, "_lightroom_owner", None)
    unrelated = _stub_process(name="Some Other App", pid=55)
    unrelated.exe.side_effect = server_lifecycle.psutil.AccessDenied(pid=55)
    unrelated.username.side_effect = server_lifecycle.psutil.AccessDenied(pid=55)
    _patch_process_scan(mocker, [unrelated])

    assert server_lifecycle._lightroom_exit_due() is True
    unrelated.exe.assert_not_called()
    unrelated.username.assert_not_called()


def test_standalone_backend_never_starts_lightroom_monitor(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle, "is_lightroom_managed", return_value=False)
    mocker.patch.object(server_lifecycle, "_lightroom_monitor_thread", None)
    thread = mocker.patch.object(server_lifecycle.threading, "Thread")

    server_lifecycle._ensure_lightroom_monitor_thread()

    thread.assert_not_called()
    assert server_lifecycle._lightroom_monitor_thread is None


def test_managed_backend_starts_independent_lightroom_monitor(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle, "is_lightroom_managed", return_value=True)
    mocker.patch.object(server_lifecycle, "_lightroom_monitor_thread", None)
    thread = mocker.patch.object(server_lifecycle.threading, "Thread")

    server_lifecycle._ensure_lightroom_monitor_thread()

    thread.assert_called_once()
    assert thread.call_args.kwargs["daemon"] is True
    assert thread.call_args.kwargs["target"] is server_lifecycle._lightroom_monitor_loop
    thread.return_value.start.assert_called_once()


def test_lightroom_monitor_not_started_on_unsupported_platform(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "linux")
    mocker.patch.object(server_lifecycle, "is_lightroom_managed", return_value=True)
    mocker.patch.object(server_lifecycle, "_lightroom_monitor_thread", None)
    thread = mocker.patch.object(server_lifecycle.threading, "Thread")

    server_lifecycle._ensure_lightroom_monitor_thread()

    thread.assert_not_called()


def test_lightroom_monitor_requests_shutdown_once_on_owner_loss(mocker):
    wait = mocker.patch.object(
        server_lifecycle.GLOBAL_SHUTDOWN_EVENT,
        "wait",
        side_effect=[False, False],
    )
    mocker.patch.object(
        server_lifecycle, "_lightroom_exit_due", side_effect=[False, True]
    )
    shutdown = mocker.patch.object(server_lifecycle, "request_shutdown")

    server_lifecycle._lightroom_monitor_loop()

    assert wait.call_count == 2
    wait.assert_called_with(1)
    shutdown.assert_called_once_with()


def test_lightroom_monitor_exits_when_shutdown_already_pending(mocker):
    mocker.patch.object(
        server_lifecycle.GLOBAL_SHUTDOWN_EVENT, "wait", return_value=True
    )
    due = mocker.patch.object(server_lifecycle, "_lightroom_exit_due")
    shutdown = mocker.patch.object(server_lifecycle, "request_shutdown")

    server_lifecycle._lightroom_monitor_loop()

    due.assert_not_called()
    shutdown.assert_not_called()


def test_lightroom_monitor_never_unloads_or_waits_on_model_lock(mocker):
    mocker.patch.object(
        server_lifecycle.GLOBAL_SHUTDOWN_EVENT, "wait", side_effect=[False, True]
    )
    mocker.patch.object(server_lifecycle, "_lightroom_exit_due", return_value=False)
    unload = mocker.patch.object(
        server_lifecycle,
        "unload_all_resources",
        side_effect=AssertionError("monitor must not unload resources"),
    )

    server_lifecycle._lightroom_monitor_loop()

    unload.assert_not_called()


def test_cleanup_errors_and_stalls_cannot_prevent_bounded_exit(mocker):
    mocker.patch("services.index.stop_index_queue", side_effect=RuntimeError("stuck"))
    runtime = mocker.patch("services.axengine_runtime.get_axengine_runtime")
    runtime.return_value.request_owned_stop.side_effect = RuntimeError("stuck")
    mocker.patch(
        "services.operations.list_active_jobs",
        side_effect=RuntimeError("stuck"),
    )
    write_state = mocker.patch.object(
        server_lifecycle,
        "_write_session_state",
        side_effect=RuntimeError("marker io error"),
    )
    remove_pid = mocker.patch.object(
        server_lifecycle, "remove_pid_file", side_effect=RuntimeError("io")
    )
    remove_ok = mocker.patch.object(
        server_lifecycle, "remove_ok_file", side_effect=RuntimeError("io")
    )
    mocker.patch.object(server_lifecycle.time, "sleep")
    exit_process = mocker.patch.object(server_lifecycle.os, "_exit")
    captured = _capture_shutdown_threads(mocker)

    server_lifecycle.request_shutdown()
    _run_captured(captured, "styleai-shutdown-cleanup")
    _run_captured(captured, "styleai-shutdown-deadline")

    write_state.assert_called_once()
    remove_pid.assert_called_once()
    remove_ok.assert_called_once()
    exit_process.assert_called_once_with(0)


def test_deadline_exits_when_cleanup_never_completes(mocker):
    mocker.patch("services.index.stop_index_queue")
    stalled = MagicMock()
    stalled.wait.return_value = False
    mocker.patch.object(server_lifecycle.threading, "Event", return_value=stalled)
    mocker.patch.object(server_lifecycle.time, "sleep")
    write_state = mocker.patch.object(server_lifecycle, "_write_session_state")
    exit_process = mocker.patch.object(server_lifecycle.os, "_exit")
    captured = _capture_shutdown_threads(mocker)

    server_lifecycle.request_shutdown()
    _run_captured(captured, "styleai-shutdown-deadline")

    write_state.assert_not_called()
    stalled.wait.assert_called_once_with(5.0)
    exit_process.assert_called_once_with(0)


def test_shutdown_deadline_exits_real_process_when_cleanup_stalls(tmp_path):
    """A hung cleanup worker must not stop the deadline from exiting."""
    src_dir = Path(__file__).resolve().parents[1] / "src"
    db_dir = tmp_path / "styleai.db"
    db_dir.mkdir()
    script = textwrap.dedent(
        """
        import sys
        import time
        import types

        src_dir, db_dir = sys.argv[1], sys.argv[2]
        sys.argv = ["styleai-server", "--db-path", db_dir]
        sys.path.insert(0, src_dir)

        hf = types.ModuleType("huggingface_hub")
        hf.hf_hub_download = lambda *a, **k: ""
        sys.modules["huggingface_hub"] = hf

        services_pkg = types.ModuleType("services")
        services_pkg.__path__ = []
        chroma = types.ModuleType("services.chroma")
        chroma.unload_collections = lambda: None
        index = types.ModuleType("services.index")
        index.active_embeddings_uuids = set()
        index.stop_index_queue = lambda: time.sleep(3600)
        operations = types.ModuleType("services.operations")
        operations.list_active_jobs = lambda *a, **k: []
        operations.recover_interrupted_jobs = lambda *a, **k: 0
        axengine = types.ModuleType("services.axengine_runtime")


        class _Runtime:
            def request_owned_stop(self):
                return None


        axengine.get_axengine_runtime = lambda: _Runtime()
        services_pkg.chroma = chroma
        services_pkg.index = index
        services_pkg.operations = operations
        services_pkg.axengine_runtime = axengine
        for name, module in (
            ("services", services_pkg),
            ("services.chroma", chroma),
            ("services.index", index),
            ("services.operations", operations),
            ("services.axengine_runtime", axengine),
        ):
            sys.modules[name] = module

        import server_lifecycle

        server_lifecycle.remove_pid_file = lambda: None
        server_lifecycle.remove_ok_file = lambda: None
        server_lifecycle.request_shutdown()
        time.sleep(60)
        sys.exit("deadline thread never exited the process")
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(src_dir), str(db_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        return_code = process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        pytest.fail(
            "bounded shutdown did not exit within 8 seconds: "
            + (process.stderr.read() if process.stderr else "")
        )
    assert return_code == 0


def test_main_entry_guards_managed_startup_and_lazy_preload():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "styleai_server.py"
    ).read_text(encoding="utf-8")

    main_body = source[source.index('if __name__ == "__main__":') :]
    signals_at = main_body.index("server_lifecycle.install_signal_handlers()")
    probe_at = main_body.index("server_lifecycle._lightroom_exit_due()")
    monitor_at = main_body.index("server_lifecycle._ensure_lightroom_monitor_thread()")
    catalog_at = main_body.index("service_db.ensure_catalog_ownership")
    preload_guard = main_body.index("if not server_lifecycle.is_lightroom_managed():")
    preload_at = main_body.index("server_lifecycle.preload_models_async()")

    assert signals_at < probe_at < catalog_at
    assert probe_at < monitor_at < catalog_at
    assert probe_at < preload_at
    assert preload_guard < preload_at
    assert "sys.exit(0)" in main_body
    assert main_body.index("sys.exit(0)") < catalog_at


def test_truncated_process_name_still_matches_main_binary(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    assert server_lifecycle._is_lightroom_process(
        "Adobe Lightroom",
        "/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/MacOS/Adobe Lightroom Classic",
    )
    assert not server_lifecycle._is_lightroom_process(
        "Adobe Lightroom",
        "/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/Helpers/Adobe Lightroom Classic Helper",
    )
    assert not server_lifecycle._is_lightroom_process(
        "Adobe Lightroom",
        "/Applications/Adobe Lightroom/Adobe Lightroom.app/Contents/MacOS/Adobe Lightroom",
    )


def test_scan_accepts_truncated_main_process_name(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle, "_lightroom_owner", None)
    owner = _stub_process(
        name="Adobe Lightroom",
        executable="/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/MacOS/Adobe Lightroom Classic",
        pid=4242,
    )
    _patch_process_scan(mocker, [owner])

    assert server_lifecycle._lightroom_exit_due() is False
    assert server_lifecycle._lightroom_owner is owner


def test_pinned_owner_process_errors_are_confirmed_loss(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    for error in (
        server_lifecycle.psutil.NoSuchProcess(pid=4242),
        server_lifecycle.psutil.ZombieProcess(pid=4242),
    ):
        owner = _stub_process(name="Adobe Lightroom Classic")
        owner.is_running.side_effect = error
        mocker.patch.object(server_lifecycle, "_lightroom_owner", owner)
        assert server_lifecycle._lightroom_exit_due() is True

    owner = _stub_process(name="Adobe Lightroom Classic", running=True)
    owner.status.side_effect = server_lifecycle.psutil.ZombieProcess(pid=4242)
    mocker.patch.object(server_lifecycle, "_lightroom_owner", owner)
    assert server_lifecycle._lightroom_exit_due() is True


def test_process_iter_failure_retries_instead_of_exiting(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle, "_lightroom_owner", None)
    mocker.patch.object(
        server_lifecycle.psutil,
        "process_iter",
        side_effect=server_lifecycle.psutil.Error("scan failed"),
    )

    assert server_lifecycle._lightroom_exit_due() is False


def test_unnamed_process_entries_make_scan_unknown(mocker):
    mocker.patch.object(server_lifecycle.sys, "platform", "darwin")
    mocker.patch.object(server_lifecycle, "_lightroom_owner", None)
    unnamed = _stub_process()
    unnamed.info = {"name": None}
    _patch_process_scan(mocker, [unnamed])

    assert server_lifecycle._lightroom_exit_due() is False


def test_deadline_never_logs_and_still_exits(mocker):
    mocker.patch("services.index.stop_index_queue")
    stalled = MagicMock()
    stalled.wait.return_value = False
    mocker.patch.object(server_lifecycle.threading, "Event", return_value=stalled)
    mocker.patch.object(server_lifecycle.time, "sleep")
    info = mocker.patch.object(server_lifecycle.logger, "info")
    exit_process = mocker.patch.object(server_lifecycle.os, "_exit")
    captured = _capture_shutdown_threads(mocker)

    server_lifecycle.request_shutdown()
    _run_captured(captured, "styleai-shutdown-deadline")

    info.assert_not_called()
    exit_process.assert_called_once_with(0)


def test_cleanup_logger_failures_still_complete_and_exit(mocker):
    mocker.patch("services.index.stop_index_queue")
    mocker.patch.object(
        server_lifecycle.logger, "info", side_effect=RuntimeError("blocked")
    )
    mocker.patch.object(server_lifecycle.time, "sleep")
    exit_process = mocker.patch.object(server_lifecycle.os, "_exit")
    captured = _capture_shutdown_threads(mocker)

    server_lifecycle.request_shutdown()
    assert server_lifecycle.GLOBAL_SHUTDOWN_EVENT.is_set()
    _run_captured(captured, "styleai-shutdown-cleanup")
    _run_captured(captured, "styleai-shutdown-deadline")

    exit_process.assert_called_once_with(0)


def test_shutdown_claim_lock_allows_same_thread_reentry():
    lock = server_lifecycle._shutdown_claim_lock
    lock.acquire()
    reacquired = False
    try:
        reacquired = lock.acquire(blocking=False)
    finally:
        lock.release()
        if reacquired:
            lock.release()
    assert reacquired is True
