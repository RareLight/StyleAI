"""Evaluate catalog-local AI edit applications and explicit user outcomes."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import platform
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
            "Evaluate applied StyleAI edits and explicit catalog-local user "
            "outcomes without changing active models or thresholds."
        )
    )
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--created-after",
        help="Optional exclusive lower time boundary in stored ISO-8601 form.",
    )
    parser.add_argument("--confidence-bins", type=int, default=5)
    parser.add_argument("--minimum-reviewed-per-generation", type=int, default=30)
    args = parser.parse_args()

    database_path = args.db_path.expanduser().resolve()
    if not database_path.is_dir():
        parser.error(f"database directory does not exist: {database_path}")
    original_argv = sys.argv
    try:
        sys.argv = [original_argv[0], "--db-path", str(database_path)]
        from services.edit_history import iter_inference_history_batches
        from services.edit_quality_evaluation import evaluate_applied_edit_histories
    finally:
        sys.argv = original_argv

    histories = (
        history
        for batch in iter_inference_history_batches(
            db_path=str(database_path),
            created_after=args.created_after,
        )
        for history in batch
    )
    report = evaluate_applied_edit_histories(
        histories,
        confidence_bin_count=args.confidence_bins,
        minimum_reviewed_per_generation=args.minimum_reviewed_per_generation,
    )
    report["generated_at_utc"] = datetime.now(UTC).isoformat()
    report["system"] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    output = args.output
    if output is None:
        output = (
            database_path
            / "evaluation_reports"
            / f"applied-edits-{report['dataset_fingerprint'][:12]}.json"
        )
    else:
        output = output.expanduser().resolve()
    _atomic_json(output, report)

    dataset = report["dataset"]
    outcomes = report["user_outcomes"]
    print(f"Evaluation report: {output}")
    print(f"Dataset fingerprint: {report['dataset_fingerprint']}")
    print(
        "Review coverage: "
        f"{dataset['reviewed_inferences']}/{dataset['applied_inferences']} "
        f"({dataset['review_coverage']})"
    )
    print(
        "Outcomes: "
        f"accepted={outcomes['accepted']}, "
        f"modified_and_kept={outcomes['modified_and_kept']}, "
        f"rejected={outcomes['rejected']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
