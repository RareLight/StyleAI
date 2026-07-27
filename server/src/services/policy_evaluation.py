"""Deterministic evaluation tools for editing-policy discovery.

The fixtures in this module intentionally use anonymous context identifiers
rather than photographic genres.  Context may influence the source features
and therefore the required target values, but it must not define editing-policy
membership.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import adjusted_rand_score
from sklearn.model_selection import GroupKFold


@dataclass(frozen=True)
class PolicyDataset:
    """A complete source-to-target fixture with leakage-safe group identifiers."""

    source_features: np.ndarray
    target_values: np.ndarray
    target_scales: np.ndarray
    policy_ids: np.ndarray
    context_ids: np.ndarray
    burst_group_ids: np.ndarray

    def validate(self) -> None:
        n_examples = len(self.source_features)
        if self.source_features.ndim != 2 or self.target_values.ndim != 2:
            raise ValueError("source_features and target_values must be matrices")
        if n_examples == 0:
            raise ValueError("policy dataset must contain examples")
        if len(self.target_values) != n_examples:
            raise ValueError("source and target example counts differ")
        for name, values in (
            ("policy_ids", self.policy_ids),
            ("context_ids", self.context_ids),
            ("burst_group_ids", self.burst_group_ids),
        ):
            if values.ndim != 1 or len(values) != n_examples:
                raise ValueError(f"{name} must contain one value per example")
        if self.target_scales.shape != (self.target_values.shape[1],):
            raise ValueError("target_scales must contain one value per target")
        if not np.all(np.isfinite(self.source_features)):
            raise ValueError("source features contain non-finite values")
        if not np.all(np.isfinite(self.target_values)):
            raise ValueError("target values contain non-finite values")
        if not np.all(np.isfinite(self.target_scales)) or np.any(
            self.target_scales <= 0
        ):
            raise ValueError("target scales must be finite and positive")


@dataclass(frozen=True)
class PolicyMetrics:
    normalized_rmse: float
    per_target_normalized_rmse: list[float]
    adjusted_rand_index: float
    true_policy_count: int
    predicted_policy_count: int
    unnecessary_policy_count: int
    missing_policy_count: int
    mean_assignment_entropy: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_synthetic_policy_dataset(
    *,
    seed: int = 17,
    n_examples: int = 240,
    n_source_features: int = 24,
    n_targets: int = 8,
    n_policies: int = 2,
    n_contexts: int = 6,
    burst_size: int = 2,
    noise_std: float = 0.02,
) -> PolicyDataset:
    """Generate policies that span contexts without using contexts as labels.

    Each policy is a source-conditioned mapping, not a fixed target vector.
    Context and ordinary feature variation therefore change the correct target
    while the underlying editing policy remains the same.
    """
    if (
        min(
            n_examples,
            n_source_features,
            n_targets,
            n_policies,
            n_contexts,
            burst_size,
        )
        <= 0
    ):
        raise ValueError("synthetic dataset dimensions must be positive")
    if n_examples < n_policies * 2:
        raise ValueError("n_examples must support every policy")

    rng = np.random.default_rng(seed)
    n_groups = int(np.ceil(n_examples / burst_size))
    group_policy_ids = np.arange(n_groups, dtype=np.int64) % n_policies
    rng.shuffle(group_policy_ids)
    group_context_ids = np.arange(n_groups, dtype=np.int64) % n_contexts
    rng.shuffle(group_context_ids)

    # Context changes the feature distribution, but its identity remains
    # independent of policy assignment.
    context_offsets = rng.normal(0.0, 0.55, size=(n_contexts, n_source_features))
    group_sources = rng.normal(0.0, 1.0, size=(n_groups, n_source_features))
    group_sources += context_offsets[group_context_ids]

    shared_coefficients = rng.normal(0.0, 0.25, size=(n_source_features, n_targets))
    policy_coefficients = np.repeat(
        shared_coefficients[np.newaxis, :, :], n_policies, axis=0
    )
    policy_coefficients += rng.normal(
        0.0, 0.45, size=(n_policies, n_source_features, n_targets)
    )
    policy_intercepts = rng.normal(0.0, 0.9, size=(n_policies, n_targets))

    source_rows: list[np.ndarray] = []
    target_rows: list[np.ndarray] = []
    policy_rows: list[int] = []
    context_rows: list[int] = []
    burst_rows: list[int] = []
    for group_id in range(n_groups):
        policy_id = int(group_policy_ids[group_id])
        for _ in range(burst_size):
            if len(source_rows) >= n_examples:
                break
            source = group_sources[group_id] + rng.normal(
                0.0, 0.015, size=n_source_features
            )
            target = (
                source @ policy_coefficients[policy_id]
                + policy_intercepts[policy_id]
                + rng.normal(0.0, noise_std, size=n_targets)
            )
            source_rows.append(source)
            target_rows.append(target)
            policy_rows.append(policy_id)
            context_rows.append(int(group_context_ids[group_id]))
            burst_rows.append(group_id)

    targets = np.asarray(target_rows, dtype=np.float64)
    target_scales = np.ptp(targets, axis=0)
    target_scales = np.maximum(target_scales, 1e-6)
    dataset = PolicyDataset(
        source_features=np.asarray(source_rows, dtype=np.float64),
        target_values=targets,
        target_scales=target_scales,
        policy_ids=np.asarray(policy_rows, dtype=np.int64),
        context_ids=np.asarray(context_rows, dtype=np.int64),
        burst_group_ids=np.asarray(burst_rows, dtype=np.int64),
    )
    dataset.validate()
    return dataset


def grouped_policy_folds(
    dataset: PolicyDataset, *, n_splits: int = 5
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return deterministic folds that never split a burst group."""
    dataset.validate()
    unique_groups = np.unique(dataset.burst_group_ids)
    if n_splits < 2 or n_splits > len(unique_groups):
        raise ValueError("n_splits must be between 2 and the burst-group count")
    splitter = GroupKFold(n_splits=n_splits)
    return list(
        splitter.split(
            dataset.source_features,
            dataset.policy_ids,
            groups=dataset.burst_group_ids,
        )
    )


def evaluate_policy_predictions(
    dataset: PolicyDataset,
    predicted_targets: np.ndarray,
    predicted_policy_ids: np.ndarray,
    *,
    responsibilities: np.ndarray | None = None,
) -> PolicyMetrics:
    """Score both target accuracy and editing-policy recovery."""
    dataset.validate()
    predicted_targets = np.asarray(predicted_targets, dtype=np.float64)
    predicted_policy_ids = np.asarray(predicted_policy_ids)
    if predicted_targets.shape != dataset.target_values.shape:
        raise ValueError("predicted target shape does not match the dataset")
    if predicted_policy_ids.shape != dataset.policy_ids.shape:
        raise ValueError("predicted policy IDs must contain one value per example")
    if not np.all(np.isfinite(predicted_targets)):
        raise ValueError("predicted targets contain non-finite values")

    normalized_errors = (
        predicted_targets - dataset.target_values
    ) / dataset.target_scales
    per_target = np.sqrt(np.mean(np.square(normalized_errors), axis=0))
    true_count = len(np.unique(dataset.policy_ids))
    predicted_count = len(np.unique(predicted_policy_ids))

    mean_entropy: float | None = None
    if responsibilities is not None:
        probabilities = np.asarray(responsibilities, dtype=np.float64)
        if probabilities.ndim != 2 or len(probabilities) != len(dataset.policy_ids):
            raise ValueError("responsibilities must be an examples-by-policies matrix")
        if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0):
            raise ValueError("responsibilities must be finite and non-negative")
        row_sums = probabilities.sum(axis=1, keepdims=True)
        if np.any(row_sums <= 0):
            raise ValueError("every responsibility row must have positive mass")
        probabilities = probabilities / row_sums
        entropy_terms = np.zeros_like(probabilities)
        positive = probabilities > 0
        entropy_terms[positive] = probabilities[positive] * np.log(
            probabilities[positive]
        )
        entropy = -np.sum(entropy_terms, axis=1)
        if probabilities.shape[1] > 1:
            entropy /= np.log(probabilities.shape[1])
        mean_entropy = float(np.mean(entropy))

    return PolicyMetrics(
        normalized_rmse=float(np.sqrt(np.mean(np.square(normalized_errors)))),
        per_target_normalized_rmse=[float(value) for value in per_target],
        adjusted_rand_index=float(
            adjusted_rand_score(dataset.policy_ids, predicted_policy_ids)
        ),
        true_policy_count=true_count,
        predicted_policy_count=predicted_count,
        unnecessary_policy_count=max(0, predicted_count - true_count),
        missing_policy_count=max(0, true_count - predicted_count),
        mean_assignment_entropy=mean_entropy,
    )


def compare_global_and_oracle_partition_baselines(
    dataset: PolicyDataset, *, n_splits: int = 5, alpha: float = 1.0
) -> dict[str, PolicyMetrics]:
    """Compare one broad linear policy with known-partition linear policies.

    The oracle partition is an evaluation ceiling, not a production discovery
    mechanism.  It proves that a fixture actually contains distinguishable
    conditional mappings before discovery algorithms are judged against it.
    """
    folds = grouped_policy_folds(dataset, n_splits=n_splits)
    global_predictions = np.zeros_like(dataset.target_values)
    oracle_predictions = np.zeros_like(dataset.target_values)

    for train_indices, test_indices in folds:
        global_model = Ridge(alpha=alpha)
        global_model.fit(
            dataset.source_features[train_indices],
            dataset.target_values[train_indices],
        )
        global_predictions[test_indices] = global_model.predict(
            dataset.source_features[test_indices]
        )

        for policy_id in np.unique(dataset.policy_ids[test_indices]):
            policy_train = train_indices[dataset.policy_ids[train_indices] == policy_id]
            policy_test = test_indices[dataset.policy_ids[test_indices] == policy_id]
            if len(policy_train) < 2:
                oracle_predictions[policy_test] = global_model.predict(
                    dataset.source_features[policy_test]
                )
                continue
            policy_model = Ridge(alpha=alpha)
            policy_model.fit(
                dataset.source_features[policy_train],
                dataset.target_values[policy_train],
            )
            oracle_predictions[policy_test] = policy_model.predict(
                dataset.source_features[policy_test]
            )

    return {
        "global": evaluate_policy_predictions(
            dataset,
            global_predictions,
            np.zeros_like(dataset.policy_ids),
        ),
        "oracle_partition": evaluate_policy_predictions(
            dataset,
            oracle_predictions,
            dataset.policy_ids,
        ),
    }
