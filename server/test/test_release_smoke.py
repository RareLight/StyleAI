import itertools
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def smoke(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import smoke_release_backend

    return smoke_release_backend


def test_port_handoff_sends_no_http_and_terminates_only_child(
    smoke, tmp_path, monkeypatch
):
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(smoke, "check_port", lambda: None)
    monkeypatch.setattr(smoke.subprocess, "Popen", Mock(return_value=process))
    child = Mock()
    child.net_connections.return_value = [
        SimpleNamespace(laddr=smoke.ADDRESS, raddr=(), status=smoke.psutil.CONN_LISTEN)
    ]
    monkeypatch.setattr(smoke.psutil, "Process", Mock(return_value=child))
    # The connection went to a different listener after our listener check:
    # this peer never appears in the child's OS connection table.
    http = Mock()
    http.sock.getsockname.return_value = ("127.0.0.1", 55000)
    monkeypatch.setattr(smoke, "HTTPConnection", Mock(return_value=http))
    monkeypatch.setattr(
        smoke,
        "time",
        SimpleNamespace(monotonic=itertools.count().__next__, sleep=lambda _: None),
    )
    with pytest.raises(RuntimeError, match="did not own"):
        smoke.smoke_test(tmp_path / "fake")
    http.request.assert_not_called()
    http.close.assert_called_once()
    process.terminate.assert_called_once()
    process.wait.assert_called_once()


def test_success_response_is_not_enough_when_frozen_migration_is_missing(
    smoke, tmp_path, monkeypatch
):
    db_path = tmp_path / "styleai.db"
    db_path.mkdir()
    (db_path / "chroma.sqlite3").touch()
    db = sqlite3.connect(db_path / "styles.sqlite")
    db.execute("CREATE TABLE schema_versions (version_id TEXT)")
    db.close()
    monkeypatch.setattr(smoke, "wait_for_socket", Mock())
    monkeypatch.setattr(smoke.psutil, "Process", Mock())
    http = Mock()
    http.getresponse.return_value.status = 200
    http.getresponse.return_value.read.return_value = json.dumps(
        {
            "results": {"status": "success", "db_path": str(db_path)},
            "error": None,
            "warning": None,
        }
    ).encode()
    monkeypatch.setattr(smoke, "HTTPConnection", Mock(return_value=http))
    with pytest.raises(RuntimeError, match="migrations differ"):
        smoke.initialize_database(Mock(), db_path)
    http.request.assert_called_once_with(
        "POST",
        "/initialize",
        json.dumps({"db_path": str(db_path)}),
        {"Content-Type": "application/json"},
    )
