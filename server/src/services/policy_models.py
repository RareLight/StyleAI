"""Candidate estimators for source-conditioned Lightroom editing policies.

The classes in this module deliberately share a small, weighted multi-output
interface.  This lets evaluation compare model families on identical
burst-safe folds before any estimator is selected for the production path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any, Callable, Protocol, Self

import numpy as np
from sklearn.cross_decomposition import PLSRegression
from sklearn.kernel_approximation import RBFSampler
from sklearn.linear_model import MultiTaskElasticNet, Ridge

from .policy_evaluation import PolicyDataset, grouped_policy_folds


class WeightedMultiOutputEstimator(Protocol):
    """Common interface required by the editing-policy benchmark."""

    def fit(
        self,
        source_features: np.ndarray,
        target_values: np.ndarray,
        *,
        sample_weight: np.ndarray | None = None,
    ) -> Self: ...

    def predict(self, source_features: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class EstimatorBenchmark:
    """Cross-validated accuracy and runtime for one candidate family."""

    normalized_rmse: float
    per_target_normalized_rmse: list[float]
    fit_seconds: float
    predict_seconds: float
    parameter_count: int
    fold_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validated_training_arrays(
    source_features: np.ndarray,
    target_values: np.ndarray,
    sample_weight: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    source = np.asarray(source_features, dtype=np.float64)
    target = np.asarray(target_values, dtype=np.float64)
    if source.ndim != 2 or target.ndim != 2:
        raise ValueError("source_features and target_values must be matrices")
    if len(source) == 0 or len(source) != len(target):
        raise ValueError("source and target example counts must be equal and positive")
    if not np.all(np.isfinite(source)) or not np.all(np.isfinite(target)):
        raise ValueError("training arrays must contain only finite values")

    if sample_weight is None:
        weights = np.ones(len(source), dtype=np.float64)
    else:
        weights = np.asarray(sample_weight, dtype=np.float64)
        if weights.shape != (len(source),):
            raise ValueError("sample_weight must contain one value per example")
        if not np.all(np.isfinite(weights)) or np.any(weights <= 0):
            raise ValueError("sample weights must be finite and positive")
    return source, target, weights


class _WeightedStandardizer:
    """Weighted centering/scaling shared by all candidate estimators."""

    def fit(
        self,
        source: np.ndarray,
        target: np.ndarray,
        weights: np.ndarray,
    ) -> None:
        self.source_mean_ = np.average(source, axis=0, weights=weights)
        self.target_mean_ = np.average(target, axis=0, weights=weights)
        source_variance = np.average(
            np.square(source - self.source_mean_), axis=0, weights=weights
        )
        self.source_scale_ = np.sqrt(np.maximum(source_variance, 1e-12))

    def transform_source(self, source: np.ndarray) -> np.ndarray:
        return (source - self.source_mean_) / self.source_scale_

    def center_target(self, target: np.ndarray) -> np.ndarray:
        return target - self.target_mean_


class ReducedRankRidge:
    """Weighted ridge followed by a reduced-rank output projection.

    Ridge stabilizes high-dimensional embedding inputs.  The output projection
    then exploits correlations between Lightroom controls without forcing a
    single latent axis or fitting every target independently.
    """

    def __init__(self, *, alpha: float = 1.0, rank: int | None = None):
        if alpha < 0:
            raise ValueError("alpha must be non-negative")
        if rank is not None and rank <= 0:
            raise ValueError("rank must be positive")
        self.alpha = float(alpha)
        self.rank = rank

    def fit(
        self,
        source_features: np.ndarray,
        target_values: np.ndarray,
        *,
        sample_weight: np.ndarray | None = None,
    ) -> Self:
        source, target, weights = _validated_training_arrays(
            source_features, target_values, sample_weight
        )
        self.standardizer_ = _WeightedStandardizer()
        self.standardizer_.fit(source, target, weights)
        standardized_source = self.standardizer_.transform_source(source)
        centered_target = self.standardizer_.center_target(target)
        root_weights = np.sqrt(weights)[:, np.newaxis]
        weighted_source = standardized_source * root_weights
        weighted_target = centered_target * root_weights

        n_examples, n_features = weighted_source.shape
        if n_features <= n_examples:
            gram = weighted_source.T @ weighted_source
            gram.flat[:: n_features + 1] += self.alpha
            full_coefficients = np.linalg.pinv(gram) @ (
                weighted_source.T @ weighted_target
            )
        else:
            # The dual form avoids a large feature-by-feature inverse when
            # SigLIP embeddings outnumber the available training examples.
            gram = weighted_source @ weighted_source.T
            gram.flat[:: n_examples + 1] += self.alpha
            full_coefficients = weighted_source.T @ (
                np.linalg.pinv(gram) @ weighted_target
            )

        maximum_rank = min(
            full_coefficients.shape[0],
            full_coefficients.shape[1],
            len(source) - 1,
        )
        effective_rank = (
            maximum_rank if self.rank is None else min(self.rank, maximum_rank)
        )
        fitted = standardized_source @ full_coefficients
        _, _, right_vectors = np.linalg.svd(fitted * root_weights, full_matrices=False)
        output_projection = (
            right_vectors[:effective_rank].T @ right_vectors[:effective_rank]
        )
        self.coefficients_ = full_coefficients @ output_projection
        self.effective_rank_ = effective_rank
        self.parameter_count_ = int(self.coefficients_.size)
        return self

    def predict(self, source_features: np.ndarray) -> np.ndarray:
        source = _validated_prediction_array(source_features)
        return (
            self.standardizer_.transform_source(source) @ self.coefficients_
            + self.standardizer_.target_mean_
        )


class WeightedPLS:
    """PLS candidate with explicit weighted centering and row scaling."""

    def __init__(self, *, n_components: int = 6):
        if n_components <= 0:
            raise ValueError("n_components must be positive")
        self.n_components = int(n_components)

    def fit(
        self,
        source_features: np.ndarray,
        target_values: np.ndarray,
        *,
        sample_weight: np.ndarray | None = None,
    ) -> Self:
        source, target, weights = _validated_training_arrays(
            source_features, target_values, sample_weight
        )
        self.standardizer_ = _WeightedStandardizer()
        self.standardizer_.fit(source, target, weights)
        root_weights = np.sqrt(weights)[:, np.newaxis]
        standardized_source = self.standardizer_.transform_source(source)
        centered_target = self.standardizer_.center_target(target)
        maximum_components = min(
            len(source) - 1,
            source.shape[1],
            target.shape[1],
        )
        self.effective_components_ = min(self.n_components, maximum_components)
        self.model_ = PLSRegression(
            n_components=self.effective_components_,
            scale=False,
            max_iter=1000,
        )
        self.model_.fit(
            standardized_source * root_weights,
            centered_target * root_weights,
        )
        self.parameter_count_ = int(self.model_.coef_.size)
        return self

    def predict(self, source_features: np.ndarray) -> np.ndarray:
        source = _validated_prediction_array(source_features)
        return (
            self.model_.predict(self.standardizer_.transform_source(source))
            + self.standardizer_.target_mean_
        )


class WeightedMultiTaskElasticNet:
    """Sparse multi-output linear candidate with weighted row scaling."""

    def __init__(
        self,
        *,
        alpha: float = 0.01,
        l1_ratio: float = 0.2,
        max_iter: int = 5000,
    ):
        self.alpha = float(alpha)
        self.l1_ratio = float(l1_ratio)
        self.max_iter = int(max_iter)

    def fit(
        self,
        source_features: np.ndarray,
        target_values: np.ndarray,
        *,
        sample_weight: np.ndarray | None = None,
    ) -> Self:
        source, target, weights = _validated_training_arrays(
            source_features, target_values, sample_weight
        )
        self.standardizer_ = _WeightedStandardizer()
        self.standardizer_.fit(source, target, weights)
        root_weights = np.sqrt(weights)[:, np.newaxis]
        self.model_ = MultiTaskElasticNet(
            alpha=self.alpha,
            l1_ratio=self.l1_ratio,
            fit_intercept=False,
            max_iter=self.max_iter,
            selection="cyclic",
        )
        self.model_.fit(
            self.standardizer_.transform_source(source) * root_weights,
            self.standardizer_.center_target(target) * root_weights,
        )
        self.parameter_count_ = int(np.count_nonzero(self.model_.coef_))
        return self

    def predict(self, source_features: np.ndarray) -> np.ndarray:
        source = _validated_prediction_array(source_features)
        return (
            self.model_.predict(self.standardizer_.transform_source(source))
            + self.standardizer_.target_mean_
        )


class RandomFeatureRidge:
    """Bounded nonlinear challenger using deterministic random Fourier features."""

    def __init__(
        self,
        *,
        alpha: float = 1.0,
        gamma: float = 0.05,
        n_components: int = 128,
        seed: int = 17,
    ):
        if n_components <= 0:
            raise ValueError("n_components must be positive")
        self.alpha = float(alpha)
        self.gamma = float(gamma)
        self.n_components = int(n_components)
        self.seed = int(seed)

    def fit(
        self,
        source_features: np.ndarray,
        target_values: np.ndarray,
        *,
        sample_weight: np.ndarray | None = None,
    ) -> Self:
        source, target, weights = _validated_training_arrays(
            source_features, target_values, sample_weight
        )
        self.standardizer_ = _WeightedStandardizer()
        self.standardizer_.fit(source, target, weights)
        self.features_ = RBFSampler(
            gamma=self.gamma,
            n_components=self.n_components,
            random_state=self.seed,
        )
        transformed = self.features_.fit_transform(
            self.standardizer_.transform_source(source)
        )
        self.model_ = Ridge(alpha=self.alpha, fit_intercept=True)
        self.model_.fit(transformed, target, sample_weight=weights)
        self.parameter_count_ = int(self.model_.coef_.size)
        return self

    def predict(self, source_features: np.ndarray) -> np.ndarray:
        source = _validated_prediction_array(source_features)
        transformed = self.features_.transform(
            self.standardizer_.transform_source(source)
        )
        return self.model_.predict(transformed)


def _validated_prediction_array(source_features: np.ndarray) -> np.ndarray:
    source = np.asarray(source_features, dtype=np.float64)
    if source.ndim != 2 or not np.all(np.isfinite(source)):
        raise ValueError("source_features must be a finite matrix")
    return source


EstimatorFactory = Callable[[], WeightedMultiOutputEstimator]


def default_estimator_factories() -> dict[str, EstimatorFactory]:
    """Return fresh, deterministic candidates for each validation fold."""
    return {
        "reduced_rank_ridge": lambda: ReducedRankRidge(alpha=1.0, rank=6),
        "weighted_pls": lambda: WeightedPLS(n_components=6),
        "multitask_elastic_net": lambda: WeightedMultiTaskElasticNet(
            alpha=0.01,
            l1_ratio=0.2,
        ),
        "random_feature_ridge": lambda: RandomFeatureRidge(
            alpha=1.0,
            gamma=0.05,
            n_components=128,
            seed=17,
        ),
    }


def benchmark_candidate_estimators(
    dataset: PolicyDataset,
    *,
    n_splits: int = 5,
    sample_weight: np.ndarray | None = None,
    factories: dict[str, EstimatorFactory] | None = None,
) -> dict[str, EstimatorBenchmark]:
    """Evaluate candidates on identical burst-safe folds and target scales."""
    dataset.validate()
    if len(np.unique(dataset.policy_ids)) != 1:
        raise ValueError(
            "estimator bake-off requires one known policy; "
            "policy discovery is evaluated separately"
        )
    _, _, weights = _validated_training_arrays(
        dataset.source_features,
        dataset.target_values,
        sample_weight,
    )
    folds = grouped_policy_folds(dataset, n_splits=n_splits)
    candidate_factories = factories or default_estimator_factories()
    if not candidate_factories:
        raise ValueError("at least one estimator factory is required")

    report: dict[str, EstimatorBenchmark] = {}
    for name, factory in candidate_factories.items():
        predictions = np.zeros_like(dataset.target_values)
        fit_seconds = 0.0
        predict_seconds = 0.0
        parameter_counts: list[int] = []
        for train_indices, test_indices in folds:
            estimator = factory()
            started = perf_counter()
            estimator.fit(
                dataset.source_features[train_indices],
                dataset.target_values[train_indices],
                sample_weight=weights[train_indices],
            )
            fit_seconds += perf_counter() - started
            started = perf_counter()
            predictions[test_indices] = estimator.predict(
                dataset.source_features[test_indices]
            )
            predict_seconds += perf_counter() - started
            parameter_counts.append(int(getattr(estimator, "parameter_count_", 0)))

        normalized_errors = (
            predictions - dataset.target_values
        ) / dataset.target_scales
        per_target = np.sqrt(np.mean(np.square(normalized_errors), axis=0))
        report[name] = EstimatorBenchmark(
            normalized_rmse=float(np.sqrt(np.mean(np.square(normalized_errors)))),
            per_target_normalized_rmse=[float(value) for value in per_target],
            fit_seconds=fit_seconds,
            predict_seconds=predict_seconds,
            parameter_count=max(parameter_counts, default=0),
            fold_count=len(folds),
        )
    return report
