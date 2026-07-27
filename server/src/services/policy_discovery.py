"""Editing-policy discovery from target behavior with source-space inference."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Self

import numpy as np
from sklearn.cluster import KMeans

from .policy_models import (
    EstimatorFactory,
    WeightedMultiOutputEstimator,
    _validated_prediction_array,
    _validated_training_arrays,
    make_default_reduced_rank_ridge,
)


@dataclass(frozen=True)
class PolicyAssignment:
    policy_index: int | None
    responsibilities: tuple[float, ...]
    entropy: float
    confidence: float
    ambiguous: bool


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exponentials = np.exp(np.clip(shifted, -700.0, 0.0))
    return exponentials / np.sum(exponentials, axis=1, keepdims=True)


def _normalized_entropy(probabilities: np.ndarray) -> np.ndarray:
    terms = np.zeros_like(probabilities)
    positive = probabilities > 0
    terms[positive] = probabilities[positive] * np.log(probabilities[positive])
    entropy = -np.sum(terms, axis=1)
    if probabilities.shape[1] > 1:
        entropy /= math.log(probabilities.shape[1])
    return entropy


class PolicyMixture:
    """Mixture of conditional edit models with a multi-medoid source gate.

    Target residuals are used only while discovering policies from edited
    examples.  New photos are assigned solely from their source evidence;
    uncertain source-space matches remain unassigned rather than leaking into
    a high-confidence training or recommendation set.
    """

    def __init__(
        self,
        *,
        n_policies: int,
        expert_factory: EstimatorFactory | None = None,
        max_iterations: int = 30,
        tolerance: float = 1e-4,
        assignment_temperature: float = 0.35,
        gate_temperature: float = 0.5,
        medoids_per_policy: int = 3,
        minimum_effective_samples: float = 8.0,
        ambiguity_margin: float = 0.15,
        minimum_confidence: float = 0.6,
        maximum_entropy: float = 0.8,
        maximum_gate_distance: float = 2.5,
        seed: int = 17,
    ):
        if n_policies <= 0:
            raise ValueError("n_policies must be positive")
        if max_iterations <= 0 or medoids_per_policy <= 0:
            raise ValueError("iteration and medoid counts must be positive")
        if assignment_temperature <= 0 or gate_temperature <= 0:
            raise ValueError("temperatures must be positive")
        if maximum_gate_distance <= 0:
            raise ValueError("maximum gate distance must be positive")
        self.n_policies = int(n_policies)
        self.expert_factory = expert_factory or make_default_reduced_rank_ridge
        self.max_iterations = int(max_iterations)
        self.tolerance = float(tolerance)
        self.assignment_temperature = float(assignment_temperature)
        self.gate_temperature = float(gate_temperature)
        self.medoids_per_policy = int(medoids_per_policy)
        self.minimum_effective_samples = float(minimum_effective_samples)
        self.ambiguity_margin = float(ambiguity_margin)
        self.minimum_confidence = float(minimum_confidence)
        self.maximum_entropy = float(maximum_entropy)
        self.maximum_gate_distance = float(maximum_gate_distance)
        self.seed = int(seed)

    def fit(
        self,
        source_features: np.ndarray,
        target_values: np.ndarray,
        *,
        sample_weight: np.ndarray | None = None,
        gate_feature_indices: np.ndarray | list[int] | None = None,
    ) -> Self:
        source, target, weights = _validated_training_arrays(
            source_features,
            target_values,
            sample_weight,
        )
        if len(source) < self.n_policies * self.minimum_effective_samples:
            raise ValueError("too few examples for the requested policy count")
        self.target_scales_ = np.maximum(np.ptp(target, axis=0), 1e-6)
        if gate_feature_indices is None:
            self.gate_feature_indices_ = np.arange(source.shape[1], dtype=np.int64)
        else:
            self.gate_feature_indices_ = np.asarray(
                gate_feature_indices,
                dtype=np.int64,
            ).reshape(-1)
            if (
                not len(self.gate_feature_indices_)
                or np.any(self.gate_feature_indices_ < 0)
                or np.any(self.gate_feature_indices_ >= source.shape[1])
                or len(np.unique(self.gate_feature_indices_))
                != len(self.gate_feature_indices_)
            ):
                raise ValueError("gate feature indices are invalid")

        broad_model = self.expert_factory()
        broad_model.fit(source, target, sample_weight=weights)
        normalized_residuals = (
            target - broad_model.predict(source)
        ) / self.target_scales_
        source_scale = np.maximum(np.std(source, axis=0), 1e-8)
        standardized_source = (source - np.mean(source, axis=0)) / source_scale
        # A policy must be supported by both a distinct editing response and a
        # source-space region that can be recognized on an unedited new photo.
        # Joint initialization avoids inventing components that can only be
        # distinguished after their target edits are already known.
        initialization_features = np.hstack((standardized_source, normalized_residuals))
        initial_labels = KMeans(
            n_clusters=self.n_policies,
            random_state=self.seed,
            n_init=10,
        ).fit_predict(initialization_features)
        responsibilities = np.eye(self.n_policies, dtype=np.float64)[initial_labels]

        self.experts_: list[WeightedMultiOutputEstimator] = []
        for iteration in range(self.max_iterations):
            experts: list[WeightedMultiOutputEstimator] = []
            priors = np.zeros(self.n_policies, dtype=np.float64)
            prediction_errors = np.zeros((len(source), self.n_policies))
            for policy_index in range(self.n_policies):
                component_weights = weights * np.maximum(
                    responsibilities[:, policy_index],
                    1e-6,
                )
                effective_count = float(
                    np.square(np.sum(component_weights))
                    / np.sum(np.square(component_weights))
                )
                if effective_count < self.minimum_effective_samples:
                    raise ValueError("policy component collapsed below minimum support")
                expert = self.expert_factory()
                expert.fit(source, target, sample_weight=component_weights)
                experts.append(expert)
                normalized_error = (
                    expert.predict(source) - target
                ) / self.target_scales_
                prediction_errors[:, policy_index] = np.mean(
                    np.square(normalized_error),
                    axis=1,
                )
                priors[policy_index] = np.sum(
                    weights * responsibilities[:, policy_index]
                )

            priors = np.maximum(priors / np.sum(priors), 1e-9)
            logits = (
                -prediction_errors / (2.0 * self.assignment_temperature**2)
                + np.log(priors)[np.newaxis, :]
            )
            updated_soft = _softmax(logits)
            updated_labels = np.argmax(updated_soft, axis=1)
            if len(np.unique(updated_labels)) != self.n_policies:
                raise ValueError("policy component collapsed during discovery")
            updated = np.eye(self.n_policies, dtype=np.float64)[updated_labels]
            maximum_change = float(np.max(np.abs(updated - responsibilities)))
            responsibilities = updated
            self.experts_ = experts
            self.iterations_ = iteration + 1
            if maximum_change <= self.tolerance:
                break

        # Preserve calibrated uncertainty after the final hard expert refit.
        self.training_responsibilities_ = updated_soft
        self.policy_priors_ = np.average(
            updated_soft,
            axis=0,
            weights=weights,
        )
        self._fit_source_gate(
            source[:, self.gate_feature_indices_],
            updated_soft,
            weights,
        )
        return self

    def _fit_source_gate(
        self,
        source: np.ndarray,
        responsibilities: np.ndarray,
        weights: np.ndarray,
    ) -> None:
        self.source_mean_ = np.average(source, axis=0, weights=weights)
        variance = np.average(
            np.square(source - self.source_mean_),
            axis=0,
            weights=weights,
        )
        self.source_scale_ = np.sqrt(np.maximum(variance, 1e-8))
        standardized = (source - self.source_mean_) / self.source_scale_
        hard_labels = np.argmax(responsibilities, axis=1)
        self.policy_medoids_: list[np.ndarray] = []
        self.policy_distance_scale_: list[float] = []

        for policy_index in range(self.n_policies):
            indices = np.flatnonzero(hard_labels == policy_index)
            cluster_count = min(self.medoids_per_policy, len(indices))
            if cluster_count == 0:
                raise ValueError("policy component has no source-space members")
            component = standardized[indices]
            centers = (
                KMeans(
                    n_clusters=cluster_count,
                    random_state=self.seed + policy_index,
                    n_init=10,
                )
                .fit(component, sample_weight=weights[indices])
                .cluster_centers_
            )
            medoids = []
            for center in centers:
                nearest = int(np.argmin(np.sum(np.square(component - center), axis=1)))
                medoids.append(component[nearest])
            medoid_matrix = np.asarray(medoids)
            distances = np.sqrt(
                np.min(
                    np.sum(
                        np.square(
                            component[:, np.newaxis, :]
                            - medoid_matrix[np.newaxis, :, :]
                        ),
                        axis=2,
                    ),
                    axis=1,
                )
            )
            distance_scale = max(float(np.quantile(distances, 0.9)), 1e-6)
            self.policy_medoids_.append(medoid_matrix)
            self.policy_distance_scale_.append(distance_scale)

    def source_responsibilities(self, source_features: np.ndarray) -> np.ndarray:
        distances = self.source_gate_distances(source_features)
        scores = (
            -distances + np.log(np.maximum(self.policy_priors_, 1e-9))[np.newaxis, :]
        )
        return _softmax(scores / self.gate_temperature)

    def source_gate_distances(self, source_features: np.ndarray) -> np.ndarray:
        source = _validated_prediction_array(source_features)
        source = source[:, self.gate_feature_indices_]
        standardized = (source - self.source_mean_) / self.source_scale_
        result = np.zeros((len(source), self.n_policies), dtype=np.float64)
        for policy_index, medoids in enumerate(self.policy_medoids_):
            distances = np.sqrt(
                np.min(
                    np.sum(
                        np.square(
                            standardized[:, np.newaxis, :] - medoids[np.newaxis, :, :]
                        ),
                        axis=2,
                    ),
                    axis=1,
                )
            )
            result[:, policy_index] = (
                distances / self.policy_distance_scale_[policy_index]
            )
        return result

    def assignments(self, source_features: np.ndarray) -> list[PolicyAssignment]:
        responsibilities = self.source_responsibilities(source_features)
        gate_distances = self.source_gate_distances(source_features)
        entropies = _normalized_entropy(responsibilities)
        ordered = np.sort(responsibilities, axis=1)
        results: list[PolicyAssignment] = []
        for index, row in enumerate(responsibilities):
            confidence = float(ordered[index, -1])
            margin = (
                1.0
                if self.n_policies == 1
                else float(ordered[index, -1] - ordered[index, -2])
            )
            ambiguous = (
                confidence < self.minimum_confidence
                or margin < self.ambiguity_margin
                or float(entropies[index]) > self.maximum_entropy
                or float(gate_distances[index, int(np.argmax(row))])
                > self.maximum_gate_distance
            )
            results.append(
                PolicyAssignment(
                    policy_index=None if ambiguous else int(np.argmax(row)),
                    responsibilities=tuple(float(value) for value in row),
                    entropy=float(entropies[index]),
                    confidence=confidence,
                    ambiguous=ambiguous,
                )
            )
        return results

    def predict(self, source_features: np.ndarray) -> np.ndarray:
        source = _validated_prediction_array(source_features)
        responsibilities = self.source_responsibilities(source)
        expert_predictions = np.stack(
            [expert.predict(source) for expert in self.experts_],
            axis=1,
        )
        selected = np.argmax(responsibilities, axis=1)
        # Callers must consult assignments() and withhold edits for ambiguous
        # rows.  Blending competing policies would create a third, untrained
        # look and is therefore never an acceptable uncertainty fallback.
        return expert_predictions[np.arange(len(source)), selected]
