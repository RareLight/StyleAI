#!/usr/bin/env python3
"""Ensure a release tag identifies the versions checked into the source tree."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def plugin_release() -> tuple[str, int]:
    source = (ROOT / "plugin/StyleAI.lrdevplugin/Info.lua").read_text()
    fields = dict(
        re.findall(
            r"^Info\.(MAJOR|MINOR|REVISION|BUILD) = (\d+)$", source, re.MULTILINE
        )
    )
    version = ".".join(fields[part] for part in ("MAJOR", "MINOR", "REVISION"))
    return version, int(fields["BUILD"])


def validate(tag: str) -> None:
    match = re.fullmatch(
        r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?", tag
    )
    if not match:
        raise ValueError(
            "Release tag must be vMAJOR.MINOR.PATCH, optionally with a prerelease suffix"
        )
    version = ".".join(match.groups())
    plugin_version, plugin_build = plugin_release()
    backend = (ROOT / "server/src/version_info.py").read_text()
    project = (ROOT / "server/pyproject.toml").read_text()
    if plugin_version != version:
        raise ValueError(f"Plug-in version does not match {version}")
    if not re.search(
        rf'^BACKEND_VERSION = "{re.escape(version)}"$', backend, re.MULTILINE
    ):
        raise ValueError(f"Backend version does not match {version}")
    if not re.search(rf"^BACKEND_BUILD = {plugin_build}$", backend, re.MULTILINE):
        raise ValueError(f"Backend build does not match plug-in build {plugin_build}")
    if not re.search(rf'^version = "{re.escape(version)}"$', project, re.MULTILINE):
        raise ValueError(f"Project version does not match {version}")


if __name__ == "__main__":
    validate(sys.argv[1])
