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

if __package__:
    from .validate_release_version import plugin_release
else:
    from validate_release_version import plugin_release


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


def request_owned_backend(process, child, method, path, body=None, headers=None):
    with closing(HTTPConnection(*ADDRESS, timeout=30)) as http:
        http.connect()
        # Verify the accepted connection, not just the listener: another service
        # can take the port between the readiness check and connect().
        peer = http.sock.getsockname()
        wait_for_socket(
            process, child, lambda c: c.laddr == ADDRESS and c.raddr == peer, 5
        )
        http.request(method, path, body, headers or {})
        response = http.getresponse()
        return response.status, json.loads(response.read())


def initialize_database(process, db_path, expected_release=None):
    child = psutil.Process(process.pid)
    wait_for_socket(
        process,
        child,
        lambda c: c.laddr == ADDRESS and c.status == psutil.CONN_LISTEN,
        150,
    )
    status, payload = request_owned_backend(
        process,
        child,
        "POST",
        "/initialize",
        json.dumps({"db_path": str(db_path)}),
        {"Content-Type": "application/json"},
    )
    results = payload.get("results") or {}
    if (
        status != 200
        or payload.get("error")
        or results.get("db_path") != str(db_path)
        or results.get("status") not in ("success", "already_initialized")
    ):
        raise RuntimeError(f"Catalog initialization failed: {status} {payload}")

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

    if expected_release:
        status, payload = request_owned_backend(process, child, "GET", "/version")
        version = payload.get("results") or {}
        actual = version.get("backend_version"), version.get("backend_build")
        if status != 200 or actual != expected_release:
            raise RuntimeError(
                f"Frozen backend release {actual} differs from plug-in {expected_release}"
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
                initialize_database(process, db_path, plugin_release())
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
        "Frozen backend matched the plug-in release and initialized a disposable catalog with all migrations"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    smoke_test(parser.parse_args().executable)


if __name__ == "__main__":
    main()
