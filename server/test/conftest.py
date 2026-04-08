import sys
import os
import tempfile

# Monkeypatch sys.argv BEFORE any test modules are collected.
# This prevents config.py from crashing on argparse missing required args
# and ensures the log directory exists across all platforms (e.g. Windows).
sys.argv = ["pytest", "--db-path", os.path.join(tempfile.gettempdir(), "mock_db_path")]
