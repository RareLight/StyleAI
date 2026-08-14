#!/usr/bin/env python3
"""Create a disposable copy of the canonical Lightroom plug-in.

The legacy release/developer mode names remain accepted so existing automation
does not break, but both now copy the same single-build plug-in. Developer task
entry points are registered under Help > Plug-in Extras.
"""

from __future__ import annotations

import argparse
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import shutil


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLUGIN = REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin"
FINGERPRINT_SCRIPT = REPOSITORY_ROOT / "scripts" / "plugin_tree_fingerprint.py"
DEFAULT_OUTPUTS = {
    "release": REPOSITORY_ROOT / "build" / "StyleAI.lrplugin",
    "developer": REPOSITORY_ROOT / "build" / "StyleAI-dev.lrdevplugin",
}


def _load_fingerprint_function():
    spec = spec_from_file_location(
        "styleai_plugin_tree_fingerprint", FINGERPRINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Could not load plug-in fingerprint helper: {FINGERPRINT_SCRIPT}"
        )
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.plugin_tree_fingerprint


plugin_tree_fingerprint = _load_fingerprint_function()


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
    source_fingerprint = plugin_tree_fingerprint(SOURCE_PLUGIN)
    output_fingerprint = plugin_tree_fingerprint(output)
    if output_fingerprint != source_fingerprint:
        shutil.rmtree(output)
        raise RuntimeError("Packaged plug-in tree differs from the canonical source")
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
    print(f"Complete plug-in tree SHA-256: {plugin_tree_fingerprint(destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
