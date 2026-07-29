"""Validation-gated local correction of broad editing-policy residuals."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Self

import numpy as np


def _normalized_rows(values: np.ndarray) -> np.ndarray:
    rows = np.asarray(values, dtype=np.float32)
    if rows.ndim != 2 or not len(rows):
        raise ValueError("embeddings must be a non-empty 2D array")
    if not np.all(np.isfinite(rows)):
        raise ValueError("embeddings must contain only finite values")
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise ValueError("embeddings must have non-zero magnitude")
    return rows / norms


def _deterministic_indices(photo_ids: np.ndarray, maximum: int) -> np.ndarray:
    if len(photo_ids) <= maximum:
        return np.arange(len(photo_ids), dtype=np.int64)
    ranked = sorted(
        range(len(photo_ids)),
        key=lambda index: (
            hashlib.sha256(str(photo_ids[index]).encode("utf-8")).digest(),
            str(photo_ids[index]),
        ),
    )
    return np.asarray(sorted(ranked[:maximum]), dtype=np.int64)


@dataclass
class LocalResidualCorrector:
    """Bounded cosine-neighborhood correction that safely abstains."""

    embeddings: np.ndarray
    residuals: np.ndarray
    groups: np.ndarray
    photo_ids: np.ndarray
    sample_weight: np.ndarray
    target_scales: np.ndarray
    maximum_distance: float = 0.15
    maximum_neighbors: int = 100
    minimum_neighbors: int = 8
    minimum_effective_neighbors: float = 4.0
    prior_strength: float = 4.0
    maximum_normalized_dispersion: float = 0.75

    @classmethod
    def fit_validated(
        cls,
        embeddings: np.ndarray,
        residuals: np.ndarray,
        *,
        groups: np.ndarray,
        photo_ids: np.ndarray,
        sample_weight: np.ndarray,
        target_scales: np.ndarray,
        maximum_bank_size: int = 2048,
        minimum_examples: int = 24,
        minimum_validation_coverage: float = 0.50,
        minimum_relative_improvement: float = 0.05,
    ) -> tuple[Self | None, dict[str, float | int | bool | str]]:
        normalized = _normalized_rows(embeddings)
        residual_array = np.asarray(residuals, dtype=np.float64)
        group_array = np.asarray(groups).reshape(-1)
        photo_array = np.asarray(photo_ids).reshape(-1)
        weights = np.asarray(sample_weight, dtype=np.float64).reshape(-1)
        scales = np.asarray(target_scales, dtype=np.float64).reshape(-1)
        count = len(normalized)
        if residual_array.ndim != 2 or residual_array.shape[0] != count:
            raise ValueError("residuals must contain one row per embedding")
        if residual_array.shape[1] != len(scales):
            raise ValueError("target_scales must match the residual dimensions")
        if any(len(values) != count for values in (group_array, photo_array, weights)):
            raise ValueError("groups, photo_ids, and weights must match embeddings")
        if (
            not np.all(np.isfinite(residual_array))
            or not np.all(np.isfinite(weights))
            or np.any(weights <= 0)
            or not np.all(np.isfinite(scales))
            or np.any(scales <= 0)
        ):
            raise ValueError(
                "residuals, weights, and scales must be finite and positive"
            )
        if maximum_bank_size <= 0:
            raise ValueError("maximum_bank_size must be positive")

        base_diagnostics: dict[str, float | int | bool | str] = {
            "enabled": False,
            "example_count": count,
            "bank_size": min(count, maximum_bank_size),
        }
        if count < minimum_examples:
            return None, {**base_diagnostics, "reason": "insufficient_examples"}

        bank_indices = _deterministic_indices(photo_array, maximum_bank_size)
        candidate = cls(
            embeddings=normalized[bank_indices],
            residuals=residual_array[bank_indices],
            groups=group_array[bank_indices],
            photo_ids=photo_array[bank_indices],
            sample_weight=weights[bank_indices],
            target_scales=scales,
        )
        corrections = np.zeros_like(residual_array)
        admitted = np.zeros(count, dtype=bool)
        maximum_working_bytes = 16 * 1024 * 1024
        bytes_per_query = max(
            1,
            len(candidate.embeddings) * np.dtype(np.float32).itemsize,
        )
        query_block_size = max(
            1,
            min(256, maximum_working_bytes // bytes_per_query),
        )
        for offset in range(0, count, query_block_size):
            block = normalized[offset : offset + query_block_size]
            distance_block = np.clip(
                1.0 - block @ candidate.embeddings.T,
                0.0,
                2.0,
            )
            for block_index, distances in enumerate(distance_block):
                index = offset + block_index
                correction = candidate._predict_from_distances(
                    distances,
                    excluded_group=group_array[index],
                )
                if correction is not None:
                    corrections[index] = correction
                    admitted[index] = True

        coverage = float(np.mean(admitted))
        normalized_residuals = residual_array / scales
        baseline_rmse = float(
            np.sqrt(
                np.average(
                    np.mean(np.square(normalized_residuals), axis=1),
                    weights=weights,
                )
            )
        )
        corrected_rmse = float(
            np.sqrt(
                np.average(
                    np.mean(np.square((residual_array - corrections) / scales), axis=1),
                    weights=weights,
                )
            )
        )
        diagnostics = {
            **base_diagnostics,
            "validation_coverage": coverage,
            "baseline_normalized_rmse": baseline_rmse,
            "corrected_normalized_rmse": corrected_rmse,
            "relative_improvement": (
                (baseline_rmse - corrected_rmse) / max(baseline_rmse, 1e-12)
            ),
        }
        if coverage < minimum_validation_coverage:
            return None, {**diagnostics, "reason": "insufficient_coverage"}
        if corrected_rmse >= baseline_rmse * (1.0 - minimum_relative_improvement):
            return None, {**diagnostics, "reason": "no_material_validation_gain"}
        return candidate, {**diagnostics, "enabled": True, "reason": "validated"}

    def _predict_normalized(
        self,
        embedding: np.ndarray,
        *,
        excluded_group: object | None = None,
    ) -> np.ndarray | None:
        distances = np.clip(1.0 - self.embeddings @ embedding, 0.0, 2.0)
        return self._predict_from_distances(
            distances,
            excluded_group=excluded_group,
        )

    def _predict_from_distances(
        self,
        distances: np.ndarray,
        *,
        excluded_group: object | None = None,
    ) -> np.ndarray | None:
        eligible = distances <= self.maximum_distance
        if excluded_group is not None:
            eligible &= self.groups != excluded_group
        indices = np.flatnonzero(eligible)
        if len(indices) < self.minimum_neighbors:
            return None
        if len(indices) > self.maximum_neighbors:
            nearest = np.argpartition(
                distances[indices],
                self.maximum_neighbors - 1,
            )[: self.maximum_neighbors]
            indices = indices[nearest]
        local_distances = distances[indices]
        kernel_scale = max(self.maximum_distance / 3.0, 1e-8)
        local_weights = self.sample_weight[indices] * np.exp(
            -local_distances / kernel_scale
        )
        weight_sum = float(np.sum(local_weights))
        squared_sum = float(np.sum(np.square(local_weights)))
        if weight_sum <= 0 or squared_sum <= 0:
            return None
        effective_neighbors = weight_sum * weight_sum / squared_sum
        if effective_neighbors < self.minimum_effective_neighbors:
            return None
        correction = np.average(
            self.residuals[indices],
            axis=0,
            weights=local_weights,
        )
        variance = np.average(
            np.square((self.residuals[indices] - correction) / self.target_scales),
            axis=0,
            weights=local_weights,
        )
        normalized_dispersion = float(np.sqrt(np.mean(variance)))
        if normalized_dispersion > self.maximum_normalized_dispersion:
            return None
        shrinkage = effective_neighbors / (effective_neighbors + self.prior_strength)
        return np.asarray(correction * shrinkage, dtype=np.float64)

    def predict(self, embedding: np.ndarray) -> np.ndarray | None:
        query = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if len(query) != self.embeddings.shape[1] or not np.all(np.isfinite(query)):
            return None
        norm = float(np.linalg.norm(query))
        if norm <= 0:
            return None
        return self._predict_normalized(query / norm)
