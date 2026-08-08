"""Evaluate production editing policies on held-out local catalog examples."""

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
            "Run burst-safe cross-validation against local StyleAI training "
            "examples without replacing the active model generation."
        )
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        required=True,
        help="Catalog-local styleai.db directory.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument(
        "--max-examples-per-partition",
        type=int,
        help="Bounded validation sample size (defaults to the production value).",
    )
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--catastrophic-error-threshold",
        type=float,
        help="Normalized error threshold (defaults to the production value).",
    )
    parser.add_argument(
        "--include-photo-ids",
        action="store_true",
        help="Include local photo IDs for the 20 worst predicted examples.",
    )
    args = parser.parse_args()

    database_path = args.db_path.expanduser().resolve()
    if not database_path.is_dir():
        parser.error(f"database directory does not exist: {database_path}")
    original_argv = sys.argv
    try:
        # Bind config to this catalog, but do not let the server parser consume
        # this analysis command's evaluation-specific arguments.
        sys.argv = [original_argv[0], "--db-path", str(database_path)]
        from services import training
        from services.policy_catalog_evaluation import (
            DEFAULT_CATASTROPHIC_ERROR_THRESHOLD,
            DEFAULT_MAX_EXAMPLES_PER_PARTITION,
            evaluate_catalog_training_examples,
        )
    finally:
        sys.argv = original_argv
    raw_examples = training.list_training_examples_with_embeddings()
    report = evaluate_catalog_training_examples(
        raw_examples,
        requested_folds=args.folds,
        maximum_examples_per_partition=(
            args.max_examples_per_partition
            if args.max_examples_per_partition is not None
            else DEFAULT_MAX_EXAMPLES_PER_PARTITION
        ),
        seed=args.seed,
        catastrophic_error_threshold=(
            args.catastrophic_error_threshold
            if args.catastrophic_error_threshold is not None
            else DEFAULT_CATASTROPHIC_ERROR_THRESHOLD
        ),
        include_photo_ids=args.include_photo_ids,
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
            / f"catalog-policy-{report['dataset_fingerprint'][:12]}.json"
        )
    else:
        output = output.expanduser().resolve()
    _atomic_json(output, report)

    fidelity = report["fidelity"]
    selective = report["selective_prediction"]
    print(f"Evaluation report: {output}")
    print(f"Dataset fingerprint: {report['dataset_fingerprint']}")
    print(
        "Coverage: "
        f"{selective['predicted_examples']}/{selective['evaluated_examples']} "
        f"({(selective['coverage'] or 0.0):.1%})"
    )
    normalized_rmse = fidelity["normalized_rmse"]
    print(
        "Normalized RMSE: "
        + (f"{normalized_rmse:.6f}" if normalized_rmse is not None else "n/a")
    )
    print(f"Catastrophic outliers: {fidelity['outliers']['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
