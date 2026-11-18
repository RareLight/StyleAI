#!/usr/bin/env python3
"""Smoke-test a frozen backend against a disposable catalog database."""

import argparse
from contextlib import closing
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time

import psutil


ADDRESS = ("127.0.0.1", 19819)
MIGRATIONS = Path(__file__).resolve().parents[1] / "server/src/migrations/versions"


def check_port():
    # Never contact or stop an already running user's service.
    with socket.socket() as probe:
        try:
            probe.bind(ADDRESS)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot use loopback port 19819 for the smoke test: {exc}"
            ) from exc


def wait_for_socket(process, child, predicate, timeout):
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        if any(predicate(conn) for conn in child.net_connections(kind="tcp4")):
            return
        time.sleep(0.1)
    raise RuntimeError("Frozen backend exited or did not own the expected socket")


def initialize_database(process, db_path):
    child = psutil.Process(process.pid)
    wait_for_socket(
        process,
        child,
        lambda c: c.laddr == ADDRESS and c.status == psutil.CONN_LISTEN,
        150,
    )
    with closing(HTTPConnection(*ADDRESS, timeout=30)) as http:
        http.connect()
        # Verify the accepted connection, not just the listener: another service
        # can take the port between the readiness check and connect().
        peer = http.sock.getsockname()
        wait_for_socket(
            process, child, lambda c: c.laddr == ADDRESS and c.raddr == peer, 5
        )
        http.request(
            "POST",
            "/initialize",
            json.dumps({"db_path": str(db_path)}),
            {"Content-Type": "application/json"},
        )
        response = http.getresponse()
        payload = json.loads(response.read())
        results = payload.get("results") or {}
        # Startup attach may complete init before this request: either a fresh
        # "success" or "already_initialized" for the same path proves readiness.
        if (
            response.status != 200
            or payload.get("error")
            or results.get("db_path") != str(db_path)
            or results.get("status") not in ("success", "already_initialized")
        ):
            raise RuntimeError(
                f"Catalog initialization failed: {response.status} {payload}"
            )

    if not (db_path / "chroma.sqlite3").is_file():
        raise RuntimeError("Catalog initialization did not create the Chroma database")
    expected = {p.stem for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py"}
    with closing(
        sqlite3.connect((db_path / "styles.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as db:
        applied = {
            row[0] for row in db.execute("SELECT version_id FROM schema_versions")
        }
    if not expected or applied != expected:
        raise RuntimeError(
            f"Frozen migrations differ from source: {sorted(applied ^ expected)}"
        )


def smoke_test(executable):
    check_port()
    with tempfile.TemporaryDirectory(prefix="styleai-smoke-") as directory:
        root = Path(directory).resolve()
        db_path = root / "styleai.db"
        log = root / "backend.log"
        with log.open("w") as output:
            process = subprocess.Popen(
                [
                    str(executable.resolve()),
                    "--db-path",
                    str(db_path),
                    "--disable-backup",
                ],
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
                env={
                    **os.environ,
                    "HF_HOME": str(root / "hf"),
                    "HF_HUB_CACHE": str(root / "hf/hub"),
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                },
            )
            try:
                initialize_database(process, db_path)
            except Exception as exc:
                raise RuntimeError(
                    f"Smoke test failed: {exc}\n{log.read_text(errors='replace')}"
                ) from exc
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    print(
        "Frozen backend initialized a disposable Chroma database and applied all migrations"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    smoke_test(parser.parse_args().executable)


if __name__ == "__main__":
    main()
