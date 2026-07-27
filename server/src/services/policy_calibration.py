"""Regularized camera/profile calibration around a broad editing policy.

The broad source-conditioned model remains authoritative.  Hierarchical
offsets only correct systematic residuals supported by enough effective
examples, so a sparse camera or profile never creates a separate style.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Self

import numpy as np

from .policy_models import (
    EstimatorFactory,
    WeightedMultiOutputEstimator,
    _validated_prediction_array,
    _validated_training_arrays,
    make_default_reduced_rank_ridge,
)


DEFAULT_CALIBRATION_LEVELS = (
    ("hdr_state",),
    ("hdr_state", "camera_model"),
    ("hdr_state", "camera_model", "camera_profile"),
)


@dataclass(frozen=True)
class CalibrationOffset:
    level: tuple[str, ...]
    key: tuple[str, ...]
    values: tuple[float, ...]
    effective_sample_count: float
    shrinkage: float


def _category_key(
    categories: dict[str, str],
    level: tuple[str, ...],
) -> tuple[str, ...] | None:
    values = tuple(str(categories.get(name) or "unknown") for name in level)
    if any(value.casefold() == "unknown" for value in values):
        return None
    return values


def _effective_sample_count(weights: np.ndarray) -> float:
    squared_sum = float(np.sum(np.square(weights)))
    if squared_sum <= 0:
        return 0.0
    return float(np.square(np.sum(weights)) / squared_sum)


def _robust_weighted_location(
    residuals: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Bound isolated target outliers before computing a weighted location."""
    median = np.median(residuals, axis=0)
    absolute_deviation = np.median(np.abs(residuals - median), axis=0)
    robust_scale = np.maximum(1.4826 * absolute_deviation, 1e-8)
    bounded = np.clip(
        residuals,
        median - 4.0 * robust_scale,
        median + 4.0 * robust_scale,
    )
    return np.average(bounded, axis=0, weights=weights)


class HierarchicalPolicyRegressor:
    """Broad multi-output model plus shrunken nested residual calibrations."""

    def __init__(
        self,
        *,
        base_factory: EstimatorFactory | None = None,
        levels: Iterable[tuple[str, ...]] = DEFAULT_CALIBRATION_LEVELS,
        prior_strength: float = 8.0,
        minimum_effective_samples: float = 2.0,
    ):
        if prior_strength <= 0:
            raise ValueError("prior_strength must be positive")
        if minimum_effective_samples <= 0:
            raise ValueError("minimum_effective_samples must be positive")
        normalized_levels = tuple(tuple(level) for level in levels)
        if not normalized_levels or any(not level for level in normalized_levels):
            raise ValueError("at least one non-empty calibration level is required")
        if len(set(normalized_levels)) != len(normalized_levels):
            raise ValueError("calibration levels must be unique")
        self.base_factory = base_factory or make_default_reduced_rank_ridge
        self.levels = normalized_levels
        self.prior_strength = float(prior_strength)
        self.minimum_effective_samples = float(minimum_effective_samples)

    def fit(
        self,
        source_features: np.ndarray,
        target_values: np.ndarray,
        *,
        categories: list[dict[str, str]],
        sample_weight: np.ndarray | None = None,
    ) -> Self:
        source, target, weights = _validated_training_arrays(
            source_features,
            target_values,
            sample_weight,
        )
        if len(categories) != len(source):
            raise ValueError("categories must contain one mapping per example")
        if not all(isinstance(item, dict) for item in categories):
            raise ValueError("every categories item must be a mapping")

        self.base_model_: WeightedMultiOutputEstimator = self.base_factory()
        self.base_model_.fit(source, target, sample_weight=weights)
        calibrated_predictions = self.base_model_.predict(source)
        self.offsets_: dict[
            tuple[tuple[str, ...], tuple[str, ...]], CalibrationOffset
        ] = {}

        for level in self.levels:
            grouped_indices: dict[tuple[str, ...], list[int]] = {}
            for index, category_values in enumerate(categories):
                key = _category_key(category_values, level)
                if key is not None:
                    grouped_indices.setdefault(key, []).append(index)

            residuals = target - calibrated_predictions
            for key, indices_list in grouped_indices.items():
                indices = np.asarray(indices_list, dtype=np.int64)
                group_weights = weights[indices]
                effective_count = _effective_sample_count(group_weights)
                if effective_count < self.minimum_effective_samples:
                    continue
                shrinkage = effective_count / (effective_count + self.prior_strength)
                location = _robust_weighted_location(
                    residuals[indices],
                    group_weights,
                )
                offset_values = location * shrinkage
                offset = CalibrationOffset(
                    level=level,
                    key=key,
                    values=tuple(float(value) for value in offset_values),
                    effective_sample_count=effective_count,
                    shrinkage=shrinkage,
                )
                self.offsets_[(level, key)] = offset
                calibrated_predictions[indices] += offset_values

        base_parameters = int(getattr(self.base_model_, "parameter_count_", 0))
        target_count = target.shape[1]
        self.parameter_count_ = base_parameters + len(self.offsets_) * target_count
        self.target_count_ = target_count
        return self

    def predict(
        self,
        source_features: np.ndarray,
        *,
        categories: list[dict[str, str]],
    ) -> np.ndarray:
        source = _validated_prediction_array(source_features)
        if len(categories) != len(source):
            raise ValueError("categories must contain one mapping per example")
        predictions = np.asarray(self.base_model_.predict(source), dtype=np.float64)
        for index, category_values in enumerate(categories):
            for level in self.levels:
                key = _category_key(category_values, level)
                if key is None:
                    continue
                offset = self.offsets_.get((level, key))
                if offset is not None:
                    predictions[index] += np.asarray(offset.values)
        return predictions

    def calibration_summary(self) -> list[dict[str, object]]:
        """Return stable, artifact-friendly calibration metadata."""
        return [
            {
                "level": list(offset.level),
                "key": list(offset.key),
                "values": list(offset.values),
                "effective_sample_count": offset.effective_sample_count,
                "shrinkage": offset.shrinkage,
            }
            for offset in sorted(
                self.offsets_.values(),
                key=lambda item: (item.level, item.key),
            )
        ]
