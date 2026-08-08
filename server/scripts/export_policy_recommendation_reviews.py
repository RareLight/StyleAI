"""Export catalog-local recommendation feedback for offline calibration."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export labelled Style Upgrade Assistant reviews from one local "
            "catalog database. No data leaves this computer."
        )
    )
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    db_path = args.db_path.expanduser().resolve()
    if not db_path.is_dir():
        parser.error(f"database directory does not exist: {db_path}")
    original_argv = sys.argv
    try:
        # Bind config to this catalog, but do not let the server parser consume
        # this analysis command's output argument.
        sys.argv = [original_argv[0], "--db-path", str(db_path)]
        from services import chroma
        from services.policy_feedback import export_review_document
    finally:
        sys.argv = original_argv
    chroma.reset_chroma_client()
    chroma.ensure_db_path(str(db_path))
    if chroma.collection is None:
        parser.error("image embedding collection is unavailable")
    document = export_review_document(
        db_path=str(db_path),
        collection=chroma.collection,
    )
    document["exported_at_utc"] = datetime.now(UTC).isoformat()
    output = args.output.expanduser().resolve()
    _atomic_json(output, document)
    labelled = sum(
        candidate["policy_match"] is not None or candidate["useful"] is not None
        for review in document["reviews"]
        for candidate in review["candidates"]
    )
    print(f"Review export: {output}")
    print(f"Review groups: {len(document['reviews'])}")
    print(f"Labelled candidates: {labelled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
