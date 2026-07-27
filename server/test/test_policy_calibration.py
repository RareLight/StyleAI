import json

import numpy as np
import pytest

from services.policy_calibration import HierarchicalPolicyRegressor
from services.policy_models import ReducedRankRidge


def _camera_bias_fixture(seed: int = 47):
    rng = np.random.default_rng(seed)
    examples_per_camera = 80
    source = rng.normal(size=(examples_per_camera * 2, 8))
    coefficients = rng.normal(size=(8, 3))
    target = source @ coefficients
    target[:examples_per_camera] += np.asarray([1.4, -0.8, 0.5])
    target[examples_per_camera:] += np.asarray([-1.2, 0.7, -0.4])
    target += rng.normal(scale=0.03, size=target.shape)
    categories = [
        {
            "hdr_state": "sdr",
            "camera_model": "Camera A" if index < examples_per_camera else "Camera B",
            "camera_profile": "Standard",
        }
        for index in range(len(source))
    ]
    return source, target, categories


def test_hierarchical_calibration_corrects_supported_camera_residuals():
    source, target, categories = _camera_bias_fixture()
    base = ReducedRankRidge(alpha=1.0, rank=3).fit(source, target)
    base_error = np.sqrt(np.mean(np.square(base.predict(source) - target)))

    calibrated = HierarchicalPolicyRegressor(
        base_factory=lambda: ReducedRankRidge(alpha=1.0, rank=3),
        prior_strength=2.0,
    ).fit(source, target, categories=categories)
    calibrated_error = np.sqrt(
        np.mean(np.square(calibrated.predict(source, categories=categories) - target))
    )

    assert calibrated_error < base_error * 0.35
    assert any(
        item["level"] == ["hdr_state", "camera_model"]
        for item in calibrated.calibration_summary()
    )


def test_unknown_camera_falls_back_to_broad_policy_prediction():
    source, target, categories = _camera_bias_fixture()
    model = HierarchicalPolicyRegressor(
        base_factory=lambda: ReducedRankRidge(alpha=1.0, rank=3),
    ).fit(source, target, categories=categories)
    unknown = [
        {
            "hdr_state": "unknown",
            "camera_model": "New Camera",
            "camera_profile": "New Profile",
        }
    ]

    expected = model.base_model_.predict(source[:1])
    actual = model.predict(source[:1], categories=unknown)
    np.testing.assert_allclose(actual, expected)


def test_sparse_profile_is_not_given_an_unregularized_offset():
    source, target, categories = _camera_bias_fixture()
    categories[0] = {
        "hdr_state": "sdr",
        "camera_model": "Camera A",
        "camera_profile": "One-off Profile",
    }
    model = HierarchicalPolicyRegressor(
        base_factory=lambda: ReducedRankRidge(alpha=1.0, rank=3),
        minimum_effective_samples=2.0,
    ).fit(source, target, categories=categories)

    summary = model.calibration_summary()
    assert not any(item["key"][-1] == "One-off Profile" for item in summary)


def test_extreme_single_residual_does_not_dominate_camera_calibration():
    source, target, categories = _camera_bias_fixture()
    target[0] += 1000.0
    model = HierarchicalPolicyRegressor(
        base_factory=lambda: ReducedRankRidge(alpha=10.0, rank=3),
        prior_strength=2.0,
    ).fit(source, target, categories=categories)

    camera_offsets = [
        item
        for item in model.calibration_summary()
        if item["level"] == ["hdr_state", "camera_model"]
        and item["key"][-1] == "Camera A"
    ]
    assert len(camera_offsets) == 1
    assert max(abs(value) for value in camera_offsets[0]["values"]) < 10.0


def test_calibration_summary_is_deterministic_and_serializable():
    source, target, categories = _camera_bias_fixture()
    first = HierarchicalPolicyRegressor(prior_strength=4.0).fit(
        source,
        target,
        categories=categories,
    )
    second = HierarchicalPolicyRegressor(prior_strength=4.0).fit(
        source,
        target,
        categories=categories,
    )

    assert first.calibration_summary() == second.calibration_summary()
    json.dumps(first.calibration_summary(), sort_keys=True)


def test_calibration_rejects_misaligned_categories():
    source, target, categories = _camera_bias_fixture()
    with pytest.raises(ValueError, match="one mapping per example"):
        HierarchicalPolicyRegressor().fit(
            source,
            target,
            categories=categories[:-1],
        )
