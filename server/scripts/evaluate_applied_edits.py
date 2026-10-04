"""Evaluate catalog-local AI edit applications and explicit user outcomes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


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
        from services.applied_edit_reporting import generate_applied_edit_report
    finally:
        sys.argv = original_argv

    result = generate_applied_edit_report(
        db_path=str(database_path),
        output=args.output,
        created_after=args.created_after,
        confidence_bin_count=args.confidence_bins,
        minimum_reviewed_per_generation=args.minimum_reviewed_per_generation,
    )
    report = result["report"]

    dataset = report["dataset"]
    outcomes = report["user_outcomes"]
    print(f"Evaluation report: {result['report_path']}")
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
