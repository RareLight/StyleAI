import os
import shutil
import sys
import tempfile

import pytest
import requests

# Every xdist worker is a separate process but previously pointed at the same
# /tmp/mock_db_path.  SQLite/Chroma state could therefore leak across workers
# and make otherwise independent tests race.  Establish a process-local root
# before any backend module imports config.py.
_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "master")
_TEST_DB_ROOT = tempfile.mkdtemp(prefix=f"styleai-pytest-{_WORKER_ID}-")
_TEST_DB_PATH = os.path.join(_TEST_DB_ROOT, "styleai.db")

# Monkeypatch sys.argv BEFORE any test modules are collected. This prevents
# config.py argparse failures and gives every worker an isolated backend path.
sys.argv = ["pytest", "--db-path", _TEST_DB_PATH]

# Add src and the server root to path so all tests can find the backend modules
# (supports both 'from src.xxx' and direct 'from xxx' imports)
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(_root)
sys.path.append(os.path.join(_root, "src"))


@pytest.fixture(scope="session", autouse=True)
def _remove_worker_test_database():
    """Remove the worker-local test database after the test session."""
    yield
    shutil.rmtree(_TEST_DB_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_request_scoped_state(monkeypatch):
    """Keep unit tests deterministic and prevent accidental provider egress."""

    def reject_network(*_args, **_kwargs):
        raise AssertionError("Unit tests must mock outbound HTTP requests.")

    # All supported local-provider HTTP paths use requests. Provider-specific
    # tests replace their SDK clients before calling them, so this catches
    # accidental integration work without masking intended unit behavior.
    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)

    import server_lifecycle

    server_lifecycle.GLOBAL_CANCEL_EVENT.clear()
    server_lifecycle.GLOBAL_SHUTDOWN_EVENT.clear()
    yield
    server_lifecycle.GLOBAL_CANCEL_EVENT.clear()
    server_lifecycle.GLOBAL_SHUTDOWN_EVENT.clear()
