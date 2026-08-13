#!/usr/bin/env python3
"""Create a disposable copy of the canonical Lightroom plug-in.

The legacy release/developer mode names remain accepted so existing automation
does not break, but both now copy the same single-build plug-in. Developer task
entry points are registered under Help > Plug-in Extras.
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
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=sorted(DEFAULT_OUTPUTS),
        help="Compatibility output label; both modes package the canonical build",
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
