#!/usr/bin/env python3
"""Bundle a frozen backend and the canonical plug-in into a release ZIP."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from package_lrc_plugin import SOURCE_PLUGIN, REPOSITORY_ROOT, build_package


def package_release(backend: Path, platform: str, version: str, output: Path) -> Path:
    if platform not in ("macos-arm64", "windows-x64"):
        raise ValueError("Unsupported platform")
    if not version or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_" for c in version):
        raise ValueError("Invalid version label")
    backend = backend.resolve()
    executable = backend / ("styleai-server.exe" if platform == "windows-x64" else "styleai-server")
    if not executable.is_file():
        raise ValueError(f"Frozen backend executable not found: {executable}")
    output = output.resolve()
    if output == backend or backend in output.parents or output == SOURCE_PLUGIN.resolve() or SOURCE_PLUGIN.resolve() in output.parents:
        raise ValueError("Output must be outside the inputs")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"StyleAI-{platform}-{version}.zip"
    with tempfile.TemporaryDirectory(prefix="styleai-release-") as directory:
        root = Path(directory)
        build_package("release", root / "StyleAI.lrplugin")
        # Dereference PyInstaller's macOS links so ordinary ZIP extractors work.
        shutil.copytree(backend, root / "styleai-server", symlinks=False)
        shutil.copy2(REPOSITORY_ROOT / "docs" / "INSTALL.md", root / "INSTALL.md")
        shutil.copy2(REPOSITORY_ROOT / "LICENSE", root / "LICENSE")
        temp_archive = root / "release.zip"
        with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for path in sorted(root.rglob("*")):
                if path == temp_archive or not path.is_file():
                    continue
                zf.write(path, path.relative_to(root).as_posix())
        os.replace(temp_archive, archive) if root.stat().st_dev == output.stat().st_dev else shutil.copy2(temp_archive, archive)
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    (output / f"{archive.name}.sha256").write_text(f"{digest.hexdigest()}  {archive.name}\n", encoding="utf-8")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, type=Path, help="PyInstaller onedir output")
    parser.add_argument("--platform", required=True, choices=("macos-arm64", "windows-x64"))
    parser.add_argument("--version", required=True, help="Version or build identifier")
    parser.add_argument("--output", type=Path, default=REPOSITORY_ROOT / "dist" / "release")
    args = parser.parse_args()
    print(package_release(args.backend, args.platform, args.version, args.output))


if __name__ == "__main__":
    main()
