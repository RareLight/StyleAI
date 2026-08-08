"""Regression tests for bounded background housekeeping."""

from unittest.mock import MagicMock

import server_lifecycle
import styleai_server


def test_backup_scheduler_waits_before_first_backup(mocker):
    backup = mocker.patch("styleai_server.service_db.build_backup_zip")
    prune = mocker.patch("styleai_server.service_db.prune_old_backups")
    mocker.patch.object(styleai_server.config.args, "disable_backup", False)
    mocker.patch.object(styleai_server.config.args, "backup_interval", 86400)
    mocker.patch.object(styleai_server.config.args, "backup_max_keep", 14)
    mocker.patch.object(styleai_server.config, "DB_PATH", "/catalog/styleai.db")

    wait_calls = 0

    def wait(_timeout):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            backup.assert_not_called()
            return False
        return True

    mocker.patch.object(
        server_lifecycle.GLOBAL_SHUTDOWN_EVENT, "wait", side_effect=wait
    )
    thread = MagicMock()
    thread_factory = mocker.patch.object(
        styleai_server.threading, "Thread", return_value=thread
    )

    styleai_server._start_housekeeping_scheduler()
    target = thread_factory.call_args.kwargs["target"]
    backup.return_value = ("/tmp/styleai-backup.zip", "backup.zip")
    mocker.patch.object(styleai_server.os, "remove")
    target()

    backup.assert_called_once()
    prune.assert_called_once_with(max_keep=14)
    assert wait_calls == 2
    thread.start.assert_called_once()
