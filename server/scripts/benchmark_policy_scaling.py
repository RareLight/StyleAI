"""Measure bounded editing-policy validation and recommendation hot paths."""

from __future__ import annotations

import argparse
import json
import os
from time import perf_counter
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.policy_local import LocalResidualCorrector  # noqa: E402
from services.policy_recommendations import _duplicate_mask  # noqa: E402


DISCOVERY_VALIDATION_LIMIT = 600
LOCAL_VALIDATION_LIMIT = 2048


def _positive_sizes(value: str) -> list[int]:
    sizes = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not sizes or any(item <= 0 for item in sizes):
        raise argparse.ArgumentTypeError(
            "sizes must be positive comma-separated integers"
        )
    return sizes


def _normalized_random(
    rng: np.random.Generator,
    rows: int,
    dimensions: int,
) -> np.ndarray:
    values = rng.normal(size=(rows, dimensions)).astype(np.float32)
    return values / np.linalg.norm(values, axis=1, keepdims=True)


def benchmark_scale(
    example_count: int,
    *,
    dimensions: int,
    candidate_count: int,
    seed: int,
) -> dict[str, float | int | bool]:
    rng = np.random.default_rng(seed + example_count)
    local_count = min(example_count, LOCAL_VALIDATION_LIMIT)
    cluster_count = min(8, dimensions)
    labels = np.arange(local_count) % cluster_count
    centers = np.eye(cluster_count, dimensions, dtype=np.float32)
    local_embeddings = centers[labels] + rng.normal(
        0.0,
        0.01,
        (local_count, dimensions),
    ).astype(np.float32)
    local_embeddings /= np.linalg.norm(local_embeddings, axis=1, keepdims=True)
    residual_centers = rng.normal(0.0, 4.0, (cluster_count, 8))
    residuals = residual_centers[labels] + rng.normal(
        0.0,
        0.1,
        (local_count, 8),
    )
    started = perf_counter()
    corrector, diagnostics = LocalResidualCorrector.fit_validated(
        local_embeddings,
        residuals,
        groups=np.asarray([f"group-{index}" for index in range(local_count)]),
        photo_ids=np.asarray([f"photo-{index}" for index in range(local_count)]),
        sample_weight=np.ones(local_count),
        target_scales=np.full(8, 10.0),
        maximum_bank_size=LOCAL_VALIDATION_LIMIT,
    )
    local_seconds = perf_counter() - started

    existing = _normalized_random(rng, example_count, dimensions)
    candidates = [
        value for value in _normalized_random(rng, candidate_count, dimensions)
    ]
    started = perf_counter()
    duplicate_mask = _duplicate_mask(
        existing,
        candidates,
        maximum_cosine_distance=0.05,
    )
    duplicate_seconds = perf_counter() - started
    return {
        "catalog_examples": example_count,
        "embedding_dimensions": dimensions,
        "discovery_validation_examples": min(
            example_count,
            DISCOVERY_VALIDATION_LIMIT,
        ),
        "local_validation_examples": local_count,
        "local_validation_seconds": local_seconds,
        "local_correction_enabled": corrector is not None,
        "local_validation_coverage": float(diagnostics.get("validation_coverage", 0.0)),
        "recommendation_candidates": candidate_count,
        "duplicate_screen_seconds": duplicate_seconds,
        "duplicate_count": int(np.sum(duplicate_mask)),
        "recommendation_embedding_mib": (existing.nbytes / (1024.0 * 1024.0)),
        "maximum_similarity_working_mib": 16,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark bounded editing-policy scale paths."
    )
    parser.add_argument(
        "--sizes",
        type=_positive_sizes,
        default=[600, 2048, 10000],
        help="Comma-separated catalog example counts.",
    )
    parser.add_argument("--dimensions", type=int, default=1152)
    parser.add_argument("--candidates", type=int, default=128)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()
    if args.dimensions <= 0 or args.candidates <= 0:
        parser.error("dimensions and candidates must be positive")
    report = [
        benchmark_scale(
            size,
            dimensions=args.dimensions,
            candidate_count=args.candidates,
            seed=args.seed,
        )
        for size in args.sizes
    ]
    print(json.dumps({"scales": report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
