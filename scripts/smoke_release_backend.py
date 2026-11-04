#!/usr/bin/env python3
"""Smoke-test a frozen backend against a disposable catalog database."""
import argparse
from pathlib import Path
import subprocess
import socket
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    # Never contact or stop an already running user's service.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 19819))
        except OSError as exc:
            raise RuntimeError("Port 19819 is in use; refusing smoke test") from exc
    with tempfile.TemporaryDirectory(prefix="styleai-smoke-") as directory:
        log = Path(directory) / "backend.log"
        with log.open("w+") as output:
            process = subprocess.Popen([str(args.executable.resolve()), "--db-path", str(Path(directory) / "styleai.db")], stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with urlopen("http://127.0.0.1:19819/ping", timeout=2) as response:
                            if b"pong" in response.read():
                                break
                    except (URLError, TimeoutError):
                        pass
                    time.sleep(0.5)
                else:
                    output.flush()
                    raise RuntimeError(f"Backend did not become ready:\n{log.read_text(errors='replace')}")
                # Ensure we tested the process we launched, not an unrelated server.
                if process.poll() is not None:
                    raise RuntimeError("Backend exited during smoke test")
                with urlopen(Request("http://127.0.0.1:19819/shutdown", data=b"", method="POST"), timeout=10):
                    pass
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    print("Frozen backend responded to /ping with a disposable database")


if __name__ == "__main__":
    main()
