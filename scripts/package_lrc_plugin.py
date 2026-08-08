#!/usr/bin/env python3
"""Create a release or developer Lightroom plugin package.

Lightroom reads menu registrations from a static manifest, so developer-only
Help commands cannot be toggled safely from a runtime preference. This script
copies the source plugin and emits the appropriate literal manifest instead of
mutating the checked-in release package.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLUGIN = REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin"
DEFAULT_OUTPUTS = {
    "release": REPOSITORY_ROOT / "build" / "StyleAI.lrplugin",
    "developer": REPOSITORY_ROOT / "build" / "StyleAI-dev.lrdevplugin",
}
MANIFEST_ANCHOR = '\tLrShutdownApp = "ShutdownApp.lua",\n'
DEVELOPER_HELP_MENU = """\
\tLrHelpMenuItems = {
\t\t{
\t\t\ttitle = LOC("$$$/StyleAI/Menu/DeveloperTests=Developer: Run Automated Tests..."),
\t\t\tfile = "TaskAutomatedTests.lua",
\t\t},
\t\t{
\t\t\ttitle = LOC("$$$/StyleAI/Menu/DeveloperBenchmark=Developer: Run Performance Benchmark..."),
\t\t\tfile = "TaskBenchmark.lua",
\t\t},
\t\t{
\t\t\ttitle = LOC("$$$/StyleAI/Menu/DeveloperRenderingSpike=Developer: Test Profile and HDR Capabilities..."),
\t\t\tfile = "TaskRenderingStateCapabilitySpike.lua",
\t\t},
\t\t{
\t\t\ttitle = LOC("$$$/StyleAI/Menu/DeveloperReconcile=Developer: Reconcile Selected AI Edits..."),
\t\t\tfile = "TaskReconcileAIEditState.lua",
\t\t},
\t},

"""


def build_package(mode: str, output: Path) -> Path:
    if mode not in DEFAULT_OUTPUTS:
        raise ValueError(f"Unsupported package mode: {mode}")
    output = output.resolve()
    if output == SOURCE_PLUGIN.resolve() or SOURCE_PLUGIN.resolve() in output.parents:
        raise ValueError("Output must not overwrite or nest inside the source plugin")
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE_PLUGIN, output)

    manifest_path = output / "Info.lua"
    manifest = manifest_path.read_text(encoding="utf-8")
    if MANIFEST_ANCHOR not in manifest:
        raise RuntimeError("Info.lua packaging anchor was not found")
    if mode == "developer":
        manifest = manifest.replace(
            MANIFEST_ANCHOR,
            DEVELOPER_HELP_MENU + MANIFEST_ANCHOR,
            1,
        )
        build_config_path = output / "BuildConfig.lua"
        build_config = build_config_path.read_text(encoding="utf-8")
        expected = "developerBuild = false"
        if expected not in build_config:
            raise RuntimeError("BuildConfig.lua release flag was not found")
        build_config_path.write_text(
            build_config.replace(expected, "developerBuild = true", 1),
            encoding="utf-8",
        )
    manifest_path.write_text(manifest, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=sorted(DEFAULT_OUTPUTS),
        help="Package a production release or developer build",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination directory (defaults beneath build/)",
    )
    args = parser.parse_args()
    destination = build_package(args.mode, args.output or DEFAULT_OUTPUTS[args.mode])
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
