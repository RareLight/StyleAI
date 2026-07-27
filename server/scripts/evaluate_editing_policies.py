"""Run the deterministic editing-policy evaluation fixture."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.policy_evaluation import (  # noqa: E402
    compare_global_and_oracle_partition_baselines,
    make_synthetic_policy_dataset,
)
from services.policy_models import benchmark_candidate_estimators  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate editing-policy models on a genre-neutral fixture."
    )
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--examples", type=int, default=240)
    parser.add_argument("--policies", type=int, default=2)
    parser.add_argument("--contexts", type=int, default=6)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument(
        "--benchmark-estimators",
        action="store_true",
        help="Also compare single-policy multi-output estimator families.",
    )
    args = parser.parse_args()

    dataset = make_synthetic_policy_dataset(
        seed=args.seed,
        n_examples=args.examples,
        n_policies=args.policies,
        n_contexts=args.contexts,
    )
    baselines = compare_global_and_oracle_partition_baselines(
        dataset, n_splits=args.folds
    )
    report = {
        "fixture": {
            "seed": args.seed,
            "examples": len(dataset.source_features),
            "source_features": dataset.source_features.shape[1],
            "targets": dataset.target_values.shape[1],
            "policies": len(set(dataset.policy_ids.tolist())),
            "contexts": len(set(dataset.context_ids.tolist())),
            "burst_groups": len(set(dataset.burst_group_ids.tolist())),
        },
        "baselines": {name: metrics.as_dict() for name, metrics in baselines.items()},
    }
    if args.benchmark_estimators:
        estimator_dataset = make_synthetic_policy_dataset(
            seed=args.seed,
            n_examples=args.examples,
            n_policies=1,
            n_contexts=args.contexts,
        )
        estimator_report = benchmark_candidate_estimators(
            estimator_dataset,
            n_splits=args.folds,
        )
        report["estimator_bakeoff"] = {
            name: metrics.as_dict() for name, metrics in estimator_report.items()
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
