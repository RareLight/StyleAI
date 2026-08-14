#!/usr/bin/env python3
"""Calculate a deterministic content fingerprint for a complete plug-in tree."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


FINGERPRINT_SCHEMA = b"styleai-plugin-tree-v1\0"


def _record(digest: hashlib._Hash, kind: bytes, path: str, payload: bytes) -> None:
    path_bytes = path.encode("utf-8")
    digest.update(kind)
    digest.update(len(path_bytes).to_bytes(8, "big"))
    digest.update(path_bytes)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def plugin_tree_fingerprint(root: Path) -> str:
    """Hash relative paths, empty directories, symlinks, and all file contents."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Plug-in tree does not exist or is not a directory: {root}")

    digest = hashlib.sha256(FINGERPRINT_SCHEMA)
    entries = sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    )
    for entry in entries:
        relative_path = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            _record(
                digest, b"L", relative_path, entry.readlink().as_posix().encode("utf-8")
            )
        elif entry.is_dir():
            _record(digest, b"D", relative_path, b"")
        elif entry.is_file():
            file_digest = hashlib.sha256()
            with entry.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    file_digest.update(chunk)
            _record(digest, b"F", relative_path, file_digest.digest())
        else:
            raise ValueError(f"Unsupported filesystem entry in plug-in tree: {entry}")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="+", type=Path, help="Plug-in directories to fingerprint"
    )
    parser.add_argument(
        "--digest-only",
        action="store_true",
        help="Print only the digest; requires exactly one path",
    )
    args = parser.parse_args()
    if args.digest_only and len(args.paths) != 1:
        parser.error("--digest-only requires exactly one path")
    for path in args.paths:
        fingerprint = plugin_tree_fingerprint(path)
        print(fingerprint if args.digest_only else f"{fingerprint}  {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
