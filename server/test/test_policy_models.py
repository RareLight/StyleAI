import json

import numpy as np
import pytest

from services.policy_evaluation import make_synthetic_policy_dataset
from services.policy_models import (
    ReducedRankRidge,
    WeightedPLS,
    benchmark_candidate_estimators,
)


def _make_low_rank_regression(seed: int = 11):
    rng = np.random.default_rng(seed)
    source = rng.normal(size=(180, 16))
    source_latent = rng.normal(size=(16, 3))
    target_latent = rng.normal(size=(3, 7))
    target = source @ source_latent @ target_latent
    target += rng.normal(scale=0.01, size=target.shape)
    return source, target


def test_reduced_rank_ridge_recovers_low_rank_mapping():
    source, target = _make_low_rank_regression()
    model = ReducedRankRidge(alpha=0.01, rank=3).fit(source[:140], target[:140])
    predicted = model.predict(source[140:])

    assert model.effective_rank_ == 3
    assert np.sqrt(np.mean(np.square(predicted - target[140:]))) < 0.03


def test_reduced_rank_ridge_supports_more_features_than_examples():
    rng = np.random.default_rng(29)
    source = rng.normal(size=(24, 80))
    target = source[:, :4] @ rng.normal(size=(4, 5))
    model = ReducedRankRidge(alpha=0.1, rank=4).fit(source, target)

    predicted = model.predict(source[:3])
    assert predicted.shape == (3, 5)
    assert np.all(np.isfinite(predicted))


def test_sample_weights_materially_change_reduced_rank_fit():
    source = np.asarray([[0.0], [1.0], [2.0], [2.0]])
    target = np.asarray([[0.0], [1.0], [2.0], [20.0]])
    equal = ReducedRankRidge(alpha=0.01, rank=1).fit(source, target)
    downweighted = ReducedRankRidge(alpha=0.01, rank=1).fit(
        source,
        target,
        sample_weight=np.asarray([1.0, 1.0, 1.0, 0.001]),
    )

    equal_error = abs(float(equal.predict(np.asarray([[2.0]]))[0, 0]) - 2.0)
    weighted_error = abs(float(downweighted.predict(np.asarray([[2.0]]))[0, 0]) - 2.0)
    assert weighted_error < equal_error


def test_pls_bounds_components_to_available_dimensions():
    source, target = _make_low_rank_regression()
    model = WeightedPLS(n_components=100).fit(source[:12, :5], target[:12, :2])

    assert model.effective_components_ == 2
    assert model.predict(source[12:15, :5]).shape == (3, 2)


def test_candidate_benchmark_is_complete_finite_and_serializable():
    dataset = make_synthetic_policy_dataset(
        seed=37,
        n_examples=96,
        n_source_features=12,
        n_targets=5,
        n_policies=1,
    )
    report = benchmark_candidate_estimators(dataset, n_splits=3)

    assert set(report) == {
        "reduced_rank_ridge",
        "weighted_pls",
        "multitask_elastic_net",
        "random_feature_ridge",
    }
    for metrics in report.values():
        assert np.isfinite(metrics.normalized_rmse)
        assert metrics.normalized_rmse >= 0
        assert len(metrics.per_target_normalized_rmse) == 5
        assert metrics.fit_seconds >= 0
        assert metrics.predict_seconds >= 0
        assert metrics.parameter_count > 0
        assert metrics.fold_count == 3
    json.dumps({name: value.as_dict() for name, value in report.items()})


def test_candidate_benchmark_rejects_mixed_policy_fixture():
    dataset = make_synthetic_policy_dataset(seed=3, n_examples=60, n_policies=2)
    with pytest.raises(ValueError, match="one known policy"):
        benchmark_candidate_estimators(dataset, n_splits=3)


@pytest.mark.parametrize(
    ("weights", "message"),
    [
        (np.ones(5), "one value per example"),
        (np.zeros(10), "finite and positive"),
    ],
)
def test_estimator_rejects_invalid_weights(weights, message):
    with pytest.raises(ValueError, match=message):
        ReducedRankRidge().fit(
            np.ones((10, 2)),
            np.ones((10, 2)),
            sample_weight=weights,
        )
