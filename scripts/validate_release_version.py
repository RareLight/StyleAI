#!/usr/bin/env python3
"""Ensure a release tag identifies the versions checked into the source tree."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(tag: str) -> None:
    match = re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?", tag)
    if not match:
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH, optionally with a prerelease suffix")
    version = ".".join(match.groups())
    info = (ROOT / "plugin/StyleAI.lrdevplugin/Info.lua").read_text()
    parts = [re.search(rf"^Info\.{part} = (\d+)$", info, re.MULTILINE) for part in ("MAJOR", "MINOR", "REVISION")]
    backend = (ROOT / "server/src/version_info.py").read_text()
    project = (ROOT / "server/pyproject.toml").read_text()
    if any(part is None for part in parts) or ".".join(part.group(1) for part in parts) != version:
        raise ValueError(f"Plug-in version does not match {version}")
    if not re.search(rf'^BACKEND_VERSION = "{re.escape(version)}"$', backend, re.MULTILINE):
        raise ValueError(f"Backend version does not match {version}")
    if not re.search(rf'^version = "{re.escape(version)}"$', project, re.MULTILINE):
        raise ValueError(f"Project version does not match {version}")


if __name__ == "__main__":
    validate(sys.argv[1])
