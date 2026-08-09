"""Leakage-safe evaluation of production editing policies on local catalog data."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import math
from time import perf_counter
from typing import Any

import numpy as np
from sklearn.model_selection import GroupKFold

from . import policy_runtime
from .policy_features import FEATURE_SCHEMA_VERSION
from .policy_targets import TARGET_SCHEMA_VERSION, default_flat_target_value


EVALUATION_SCHEMA_VERSION = "catalog-policy-evaluation-v1"
DEFAULT_MAX_EXAMPLES_PER_PARTITION = 600
DEFAULT_CATASTROPHIC_ERROR_THRESHOLD = 0.5


@dataclass(frozen=True)
class _PredictionRow:
    photo_id: str
    confidence: float
    entropy: float
    actual: dict[str, float]
    predicted: dict[str, float]


def _target_family(key: str) -> str:
    if key == "white_balance_is_custom":
        return "white_balance"
    if key.startswith("crop_") or key == "rotation_is_applied":
        return "crop"
    if key.startswith("curve_") or key.startswith("tone_curve_"):
        return "tone_curve"
    if key.startswith("hsl_"):
        return "hsl"
    if key.startswith("cg_"):
        return "color_grading"
    if key.startswith(
        (
            "sharpen",
            "noise_reduction",
            "color_noise_reduction",
            "defringe_",
        )
    ):
        return "detail"
    if key.startswith(("manual_distortion", "manual_vignette")):
        return "lens"
    if key.startswith(("vignette", "grain")):
        return "effects"
    return "basic"


def _finite_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "p90": None,
            "p95": None,
            "maximum": None,
        }
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": len(values),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "p90": float(np.quantile(array, 0.90)),
        "p95": float(np.quantile(array, 0.95)),
        "maximum": float(np.max(array)),
    }


def _dataset_fingerprint(
    partitions: dict[str, tuple[list[dict[str, Any]], np.ndarray]],
) -> str:
    digest = hashlib.sha256()
    digest.update(EVALUATION_SCHEMA_VERSION.encode("utf-8"))
    digest.update(policy_runtime.POLICY_ALGORITHM_VERSION.encode("utf-8"))
    digest.update(FEATURE_SCHEMA_VERSION.encode("utf-8"))
    digest.update(TARGET_SCHEMA_VERSION.encode("utf-8"))
    for partition_key in sorted(partitions):
        rows, weights = partitions[partition_key]
        digest.update(partition_key.encode("utf-8"))
        for row, weight in zip(rows, weights, strict=True):
            digest.update(row["photo_id"].encode("utf-8"))
            digest.update(np.asarray(row["source"], dtype="<f4").tobytes(order="C"))
            digest.update(
                np.asarray(
                    [
                        row["flat_target"].get(
                            key,
                            default_flat_target_value(key),
                        )
                        for key in sorted(row["flat_target"])
                    ],
                    dtype="<f8",
                ).tobytes(order="C")
            )
            digest.update("|".join(sorted(row["flat_target"])).encode("utf-8"))
            digest.update(np.asarray([weight], dtype="<f8").tobytes())
    return digest.hexdigest()


def _outer_fold_count(example_count: int, requested: int) -> int:
    for fold_count in range(min(requested, example_count), 1, -1):
        maximum_test_count = math.ceil(example_count / fold_count)
        if example_count - maximum_test_count >= policy_runtime.MIN_PARTITION_EXAMPLES:
            return fold_count
    return 0


def _target_scales(
    partitions: dict[str, tuple[list[dict[str, Any]], np.ndarray]],
) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for rows, _ in partitions.values():
        partition_keys = sorted({key for row in rows for key in row["flat_target"]})
        for row in rows:
            for key in partition_keys:
                values[key].append(
                    float(
                        row["flat_target"].get(
                            key,
                            default_flat_target_value(key),
                        )
                    )
                )
    scales = {}
    for key, target_values in values.items():
        array = np.asarray(target_values, dtype=np.float64)
        robust_range = float(np.quantile(array, 0.90) - np.quantile(array, 0.10))
        scales[key] = max(robust_range, float(np.ptp(array)) * 0.05, 1.0)
    return scales


def _score_predictions(
    predictions: list[_PredictionRow],
    *,
    scales: dict[str, float],
    catastrophic_error_threshold: float,
    include_photo_ids: bool,
) -> dict[str, Any]:
    squared_by_target: dict[str, list[float]] = defaultdict(list)
    raw_squared_by_target: dict[str, list[float]] = defaultdict(list)
    squared_by_family: dict[str, list[float]] = defaultdict(list)
    row_errors: list[tuple[float, str]] = []
    white_balance_matches: list[bool] = []

    for row in predictions:
        normalized_squared: list[float] = []
        for key, predicted in row.predicted.items():
            actual = row.actual[key]
            raw_squared = (predicted - actual) ** 2
            normalized_squared_value = raw_squared / (scales[key] ** 2)
            raw_squared_by_target[key].append(raw_squared)
            squared_by_target[key].append(normalized_squared_value)
            squared_by_family[_target_family(key)].append(normalized_squared_value)
            normalized_squared.append(normalized_squared_value)
            if key == "white_balance_is_custom":
                white_balance_matches.append((predicted >= 0.7) == (actual >= 0.5))
        if normalized_squared:
            row_errors.append(
                (float(np.sqrt(np.mean(normalized_squared))), row.photo_id)
            )

    per_target = {
        key: {
            "count": len(values),
            "normalized_rmse": float(np.sqrt(np.mean(values))),
            "raw_rmse": float(np.sqrt(np.mean(raw_squared_by_target[key]))),
            "normalization_scale": scales[key],
        }
        for key, values in sorted(squared_by_target.items())
    }
    per_family = {
        family: {
            "count": len(values),
            "normalized_rmse": float(np.sqrt(np.mean(values))),
        }
        for family, values in sorted(squared_by_family.items())
    }
    all_squared = [value for values in squared_by_target.values() for value in values]
    ordered_outliers = sorted(row_errors, key=lambda item: (-item[0], item[1]))
    outliers: dict[str, Any] = {
        "threshold": catastrophic_error_threshold,
        "count": sum(error >= catastrophic_error_threshold for error, _ in row_errors),
        "row_normalized_rmse": _finite_summary([error for error, _ in row_errors]),
    }
    if include_photo_ids:
        outliers["worst_examples"] = [
            {"photo_id": photo_id, "normalized_rmse": error}
            for error, photo_id in ordered_outliers[:20]
        ]
    return {
        "normalized_rmse": (
            float(np.sqrt(np.mean(all_squared))) if all_squared else None
        ),
        "per_target": per_target,
        "per_family": per_family,
        "white_balance_accuracy": (
            float(np.mean(white_balance_matches)) if white_balance_matches else None
        ),
        "outliers": outliers,
    }


def evaluate_catalog_training_examples(
    raw_examples: list[dict[str, Any]],
    *,
    requested_folds: int = 3,
    maximum_examples_per_partition: int = DEFAULT_MAX_EXAMPLES_PER_PARTITION,
    seed: int = 17,
    catastrophic_error_threshold: float = DEFAULT_CATASTROPHIC_ERROR_THRESHOLD,
    include_photo_ids: bool = False,
) -> dict[str, Any]:
    """Cross-validate production policy artifacts without changing active state."""
    if requested_folds < 2:
        raise ValueError("requested_folds must be at least 2")
    if maximum_examples_per_partition < policy_runtime.MIN_PARTITION_EXAMPLES:
        raise ValueError(
            "maximum_examples_per_partition must support a production partition"
        )
    if (
        not math.isfinite(catastrophic_error_threshold)
        or catastrophic_error_threshold <= 0
    ):
        raise ValueError("catastrophic_error_threshold must be positive")

    prepared = policy_runtime._prepare_rows(raw_examples)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prepared:
        grouped[row["partition_key"]].append(row)

    partitions: dict[str, tuple[list[dict[str, Any]], np.ndarray]] = {}
    partition_input_counts: dict[str, int] = {}
    for partition_key in sorted(grouped):
        partition_input_counts[partition_key] = len(grouped[partition_key])
        curated, weights = policy_runtime._curate_bursts(grouped[partition_key])
        if len(curated) > maximum_examples_per_partition:
            groups = np.asarray(
                [row.get("burst_group_id") or row["photo_id"] for row in curated]
            )
            selected = policy_runtime._bounded_group_sample(
                groups,
                maximum=maximum_examples_per_partition,
            )
            curated = [curated[int(index)] for index in selected]
            weights = weights[selected]
        partitions[partition_key] = curated, weights

    fingerprint = _dataset_fingerprint(partitions)
    scales = _target_scales(partitions)
    predictions: list[_PredictionRow] = []
    partition_reports: list[dict[str, Any]] = []
    estimator_counts: Counter[str] = Counter()
    policy_count_counts: Counter[int] = Counter()
    local_correction_fold_count = 0
    total_holdout = 0
    total_abstained = 0
    fit_seconds = 0.0
    predict_seconds = 0.0

    for partition_index, partition_key in enumerate(sorted(partitions)):
        rows, weights = partitions[partition_key]
        fold_count = _outer_fold_count(len(rows), requested_folds)
        if fold_count == 0:
            partition_reports.append(
                {
                    "partition_key": partition_key,
                    "input_examples": partition_input_counts[partition_key],
                    "curated_examples": len(rows),
                    "evaluated_examples": 0,
                    "predicted_examples": 0,
                    "abstained_examples": 0,
                    "coverage": None,
                    "fold_count": 0,
                    "status": "insufficient_examples_for_held_out_evaluation",
                }
            )
            continue
        groups = np.asarray(
            [row.get("burst_group_id") or row["photo_id"] for row in rows]
        )
        splitter = GroupKFold(n_splits=fold_count)
        partition_holdout = 0
        partition_abstained = 0
        partition_predicted = 0
        for fold_index, (train_indices, test_indices) in enumerate(
            splitter.split(np.arange(len(rows)), groups=groups)
        ):
            train_rows = [rows[int(index)] for index in train_indices]
            started = perf_counter()
            artifact = policy_runtime._fit_partition(
                train_rows,
                weights[train_indices],
                generation_id=f"evaluation-{fingerprint[:12]}",
                seed=seed + partition_index * 100 + fold_index,
            )
            fit_seconds += perf_counter() - started
            estimator_counts[artifact.estimator_name] += 1
            policy_count_counts[len(artifact.policy_ids)] += 1
            if any(corrector is not None for corrector in artifact.local_correctors):
                local_correction_fold_count += 1

            for index in test_indices:
                row = rows[int(index)]
                partition_holdout += 1
                total_holdout += 1
                started = perf_counter()
                prediction = policy_runtime.predict_partition_artifact(
                    artifact,
                    source=row["source"],
                    metadata=row["metadata"],
                    embedding=row["normalized_embedding"],
                )
                predict_seconds += perf_counter() - started
                if prediction is None:
                    partition_abstained += 1
                    total_abstained += 1
                    continue
                actual = {
                    key: float(
                        row["flat_target"].get(
                            key,
                            default_flat_target_value(key),
                        )
                    )
                    for key in prediction.flat_target
                }
                predictions.append(
                    _PredictionRow(
                        photo_id=row["photo_id"],
                        confidence=prediction.confidence,
                        entropy=prediction.entropy,
                        actual=actual,
                        predicted=prediction.flat_target,
                    )
                )
                partition_predicted += 1
        partition_reports.append(
            {
                "partition_key": partition_key,
                "input_examples": partition_input_counts[partition_key],
                "curated_examples": len(rows),
                "evaluated_examples": partition_holdout,
                "predicted_examples": partition_predicted,
                "abstained_examples": partition_abstained,
                "coverage": (
                    partition_predicted / partition_holdout
                    if partition_holdout
                    else None
                ),
                "fold_count": fold_count,
                "status": "evaluated",
            }
        )

    scored = _score_predictions(
        predictions,
        scales=scales,
        catastrophic_error_threshold=catastrophic_error_threshold,
        include_photo_ids=include_photo_ids,
    )
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "algorithm_version": policy_runtime.POLICY_ALGORITHM_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "target_schema_version": TARGET_SCHEMA_VERSION,
        "dataset_fingerprint": fingerprint,
        "configuration": {
            "seed": seed,
            "requested_folds": requested_folds,
            "maximum_examples_per_partition": maximum_examples_per_partition,
            "catastrophic_error_threshold": catastrophic_error_threshold,
            "photo_ids_included": include_photo_ids,
        },
        "dataset": {
            "raw_examples": len(raw_examples),
            "prepared_examples": len(prepared),
            "curated_examples": sum(len(rows) for rows, _ in partitions.values()),
            "partition_count": len(partitions),
        },
        "selective_prediction": {
            "evaluated_examples": total_holdout,
            "predicted_examples": len(predictions),
            "abstained_examples": total_abstained,
            "coverage": len(predictions) / total_holdout if total_holdout else None,
            "confidence": _finite_summary([row.confidence for row in predictions]),
            "entropy": _finite_summary([row.entropy for row in predictions]),
        },
        "fidelity": scored,
        "model_selection": {
            "estimator_fold_counts": dict(sorted(estimator_counts.items())),
            "policy_count_fold_counts": {
                str(key): value for key, value in sorted(policy_count_counts.items())
            },
            "local_correction_enabled_folds": local_correction_fold_count,
        },
        "partitions": partition_reports,
        "timing": {
            "fit_seconds": fit_seconds,
            "predict_seconds": predict_seconds,
        },
    }
