import numpy as np
import pytest
from sklearn.metrics import adjusted_rand_score

from services.policy_discovery import PolicyMixture
from services.policy_models import ReducedRankRidge


def _discoverable_fixture(seed: int = 53):
    rng = np.random.default_rng(seed)
    source = rng.normal(size=(240, 10))
    true_labels = rng.integers(0, 2, size=len(source), dtype=np.int64)
    source[:, :4] += np.where(true_labels[:, np.newaxis] == 0, -3.0, 3.0)
    shared = rng.normal(scale=0.2, size=(10, 4))
    policy_delta = rng.normal(scale=0.8, size=(2, 10, 4))
    intercepts = np.asarray([[-1.0, 0.6, -0.4, 0.8], [1.0, -0.6, 0.4, -0.8]])
    target = np.empty((len(source), 4))
    for index, policy_index in enumerate(true_labels):
        target[index] = (
            source[index] @ (shared + policy_delta[policy_index])
            + intercepts[policy_index]
        )
    target += rng.normal(scale=0.02, size=target.shape)
    return source, target, true_labels


def _mixture():
    return PolicyMixture(
        n_policies=2,
        expert_factory=lambda: ReducedRankRidge(alpha=0.1, rank=4),
        minimum_effective_samples=8,
        ambiguity_margin=0.1,
        minimum_confidence=0.55,
        seed=7,
    )


def test_policy_mixture_recovers_conditional_edit_mappings():
    source, target, true_labels = _discoverable_fixture()
    model = _mixture().fit(source, target)
    discovered = np.argmax(model.training_responsibilities_, axis=1)

    assert adjusted_rand_score(true_labels, discovered) > 0.9
    predicted = model.predict(source)
    normalized_rmse = np.sqrt(
        np.mean(np.square((predicted - target) / np.ptp(target, axis=0)))
    )
    assert normalized_rmse < 0.08


def test_source_gate_marks_boundary_example_ambiguous():
    source, target, _ = _discoverable_fixture()
    model = _mixture().fit(source, target)
    boundary = np.zeros((1, source.shape[1]))

    assignment = model.assignments(boundary)[0]
    assert assignment.ambiguous
    assert assignment.policy_index is None
    assert sum(assignment.responsibilities) == pytest.approx(1.0)


def test_source_gate_returns_confident_supported_assignments():
    source, target, _ = _discoverable_fixture()
    model = _mixture().fit(source, target)
    assignments = model.assignments(source)

    confident_count = sum(not item.ambiguous for item in assignments)
    # High precision is preferred to forced coverage; the gate may abstain on
    # a substantial minority even within this clean fixture.
    assert confident_count > len(source) * 0.6
    assert all(0.0 <= item.entropy <= 1.0 for item in assignments)


def test_policy_mixture_is_deterministic():
    source, target, _ = _discoverable_fixture()
    first = _mixture().fit(source, target)
    second = _mixture().fit(source, target)

    np.testing.assert_allclose(
        first.training_responsibilities_,
        second.training_responsibilities_,
    )
    np.testing.assert_allclose(
        first.source_responsibilities(source[:10]),
        second.source_responsibilities(source[:10]),
    )


def test_policy_mixture_rejects_unsupported_component_count():
    source, target, _ = _discoverable_fixture()
    with pytest.raises(ValueError, match="too few examples"):
        PolicyMixture(
            n_policies=20,
            minimum_effective_samples=20,
        ).fit(source, target)
