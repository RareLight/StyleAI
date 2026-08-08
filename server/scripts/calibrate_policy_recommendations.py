"""Calibrate policy recommendation parameters from local labelled reviews."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.policy_recommendation_evaluation import (  # noqa: E402
    calibrate_recommendations,
    parse_review_document,
)


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
            "Evaluate recommendation admission and ranking configurations from "
            "local labelled review groups. This never changes production defaults."
        )
    )
    parser.add_argument("reviews", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--target-policy-precision", type=float, default=0.95)
    parser.add_argument("--minimum-labeled-selected", type=int, default=5)
    parser.add_argument("--folds", type=int, default=3)
    args = parser.parse_args()

    review_path = args.reviews.expanduser().resolve()
    if not review_path.is_file():
        parser.error(f"review file does not exist: {review_path}")
    document = json.loads(review_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        parser.error("review document must be a JSON object")
    reviews = parse_review_document(document)
    report = calibrate_recommendations(
        reviews,
        target_policy_precision=args.target_policy_precision,
        minimum_labeled_selected=args.minimum_labeled_selected,
        requested_folds=args.folds,
    )
    report["generated_at_utc"] = datetime.now(UTC).isoformat()
    output = (
        args.output.expanduser().resolve()
        if args.output is not None
        else review_path.with_name(review_path.stem + "-calibration.json")
    )
    _atomic_json(output, report)
    baseline = report["baseline"]["metrics"]
    recommended = report["recommended"]
    held_out = report["cross_validation"]["held_out_mean_metrics"]
    print(f"Calibration report: {output}")
    print(f"Review fingerprint: {report['dataset_fingerprint']}")
    print(f"Baseline policy precision: {baseline['policy_precision']}")
    print(
        "Recommended in-sample policy precision: "
        f"{recommended['metrics']['policy_precision']}"
    )
    print(
        "Held-out policy precision: "
        f"{held_out['policy_precision'] if held_out else 'insufficient review groups'}"
    )
    print("Deployment status: evaluation only; production defaults are unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
