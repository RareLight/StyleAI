import json

import numpy as np
import pytest

from services.policy_evaluation import (
    compare_global_and_oracle_partition_baselines,
    evaluate_policy_predictions,
    grouped_policy_folds,
    make_synthetic_policy_dataset,
)


def test_synthetic_policy_dataset_is_deterministic_and_genre_neutral():
    first = make_synthetic_policy_dataset(seed=23)
    second = make_synthetic_policy_dataset(seed=23)

    np.testing.assert_allclose(first.source_features, second.source_features)
    np.testing.assert_allclose(first.target_values, second.target_values)
    np.testing.assert_array_equal(first.policy_ids, second.policy_ids)
    np.testing.assert_array_equal(first.context_ids, second.context_ids)

    # Context is deliberately not a proxy for policy: policies span multiple
    # contexts and contexts contain multiple policies.
    for policy_id in np.unique(first.policy_ids):
        assert len(np.unique(first.context_ids[first.policy_ids == policy_id])) > 1
    for context_id in np.unique(first.context_ids):
        assert len(np.unique(first.policy_ids[first.context_ids == context_id])) > 1


def test_grouped_folds_never_leak_bursts():
    dataset = make_synthetic_policy_dataset(seed=9, burst_size=3)
    folds = grouped_policy_folds(dataset, n_splits=5)

    tested_indices: list[int] = []
    for train_indices, test_indices in folds:
        train_groups = set(dataset.burst_group_ids[train_indices])
        test_groups = set(dataset.burst_group_ids[test_indices])
        assert train_groups.isdisjoint(test_groups)
        tested_indices.extend(test_indices.tolist())

    assert sorted(tested_indices) == list(range(len(dataset.source_features)))


def test_metrics_detect_perfect_recovery_and_spurious_splits():
    dataset = make_synthetic_policy_dataset(seed=31)
    perfect_responsibilities = np.eye(2)[dataset.policy_ids]
    perfect = evaluate_policy_predictions(
        dataset,
        dataset.target_values.copy(),
        dataset.policy_ids.copy(),
        responsibilities=perfect_responsibilities,
    )
    assert perfect.normalized_rmse == pytest.approx(0.0)
    assert perfect.adjusted_rand_index == pytest.approx(1.0)
    assert perfect.mean_assignment_entropy == pytest.approx(0.0)

    split_ids = dataset.policy_ids * 2 + dataset.context_ids % 2
    split = evaluate_policy_predictions(
        dataset,
        dataset.target_values.copy(),
        split_ids,
    )
    assert split.unnecessary_policy_count > 0
    assert split.adjusted_rand_index < 1.0


def test_oracle_policy_baseline_beats_one_global_mapping():
    dataset = make_synthetic_policy_dataset(seed=41, n_examples=300)
    report = compare_global_and_oracle_partition_baselines(dataset, n_splits=5)

    assert (
        report["oracle_partition"].normalized_rmse
        < report["global"].normalized_rmse * 0.5
    )
    assert report["global"].missing_policy_count == 1
    assert report["oracle_partition"].adjusted_rand_index == pytest.approx(1.0)


def test_metrics_report_is_json_serializable():
    dataset = make_synthetic_policy_dataset(seed=5, n_examples=60)
    metrics = evaluate_policy_predictions(
        dataset,
        dataset.target_values,
        dataset.policy_ids,
    )
    encoded = json.dumps(metrics.as_dict(), sort_keys=True)
    assert "normalized_rmse" in encoded


def test_evaluation_rejects_invalid_shapes_and_responsibilities():
    dataset = make_synthetic_policy_dataset(seed=7, n_examples=60)
    with pytest.raises(ValueError, match="target shape"):
        evaluate_policy_predictions(
            dataset,
            dataset.target_values[:-1],
            dataset.policy_ids,
        )
    with pytest.raises(ValueError, match="positive mass"):
        evaluate_policy_predictions(
            dataset,
            dataset.target_values,
            dataset.policy_ids,
            responsibilities=np.zeros((len(dataset.policy_ids), 2)),
        )
