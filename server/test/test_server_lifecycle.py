"""Regression tests for bounded backend shutdown."""

import json
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
