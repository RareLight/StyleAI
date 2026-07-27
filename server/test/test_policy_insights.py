import numpy as np
import pytest

from services.policy_insights import (
    DescriptorObservation,
    PolicyCoverageDiagnostics,
    discover_open_vocabulary_descriptors,
)


def test_descriptors_are_discovered_from_observed_terms_without_fixed_taxonomy():
    observations = [
        [DescriptorObservation("user_keyword", "Copper tones", "user")],
        [DescriptorObservation("user_keyword", " copper   tones ", "user")],
        [DescriptorObservation("local_tag", "Quiet geometry", "siglip")],
        [DescriptorObservation("local_tag", "Quiet geometry", "siglip")],
    ]
    responsibilities = np.asarray(
        [[0.98, 0.02], [0.95, 0.05], [0.04, 0.96], [0.02, 0.98]]
    )

    descriptors = discover_open_vocabulary_descriptors(
        observations,
        responsibilities,
        minimum_effective_support=1.5,
    )

    by_policy = {
        policy: [item.descriptor for item in descriptors if item.policy_index == policy]
        for policy in range(2)
    }
    assert by_policy[0] == ["Copper tones"]
    assert by_policy[1] == ["Quiet geometry"]


def test_common_descriptor_is_not_presented_as_distinctive():
    observation = DescriptorObservation("local_tag", "Soft light", "siglip")
    descriptors = discover_open_vocabulary_descriptors(
        [[observation], [observation], [observation], [observation]],
        np.asarray([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]),
    )
    assert descriptors == []


def _coverage_fixture():
    rng = np.random.default_rng(61)
    first = rng.normal(loc=-2.0, scale=0.2, size=(20, 4))
    second = rng.normal(loc=2.0, scale=0.2, size=(20, 4))
    source = np.vstack((first, second))
    responsibilities = np.vstack(
        (
            np.tile([0.95, 0.05], (20, 1)),
            np.tile([0.05, 0.95], (20, 1)),
        )
    )
    categories = [
        {
            "hdr_state": "sdr",
            "camera_model": "A" if index < 20 else "B",
            "camera_profile": "Standard",
        }
        for index in range(40)
    ]
    return source, responsibilities, categories


def test_coverage_records_include_zero_support_gaps_for_each_policy():
    source, responsibilities, categories = _coverage_fixture()
    diagnostics = PolicyCoverageDiagnostics(
        visual_component_count=2,
        desired_effective_count=5,
    ).fit(
        source,
        ("image_embedding_0000", "image_embedding_0001", "luminance", "contrast"),
        responsibilities,
        categories=categories,
        numeric_dimensions=("luminance", "contrast"),
    )
    records = diagnostics.records()

    assert {item.policy_index for item in records} == {0, 1}
    assert {item.dimension_key for item in records} >= {
        "visual_component",
        "numeric:luminance",
        "category:camera_model",
    }
    assert any(item.coverage_score < 0.25 for item in records)


def test_candidate_gain_prefers_underrepresented_policy_region():
    source, responsibilities, categories = _coverage_fixture()
    diagnostics = PolicyCoverageDiagnostics(
        visual_component_count=2,
        desired_effective_count=8,
    ).fit(
        source,
        ("e0", "e1", "luminance", "contrast"),
        responsibilities,
        categories=categories,
        numeric_dimensions=("luminance",),
    )
    candidate = source[30:31]
    gains = diagnostics.score_candidate_gain(
        candidate,
        np.asarray([[0.9, 0.1]]),
        categories=[categories[30]],
    )

    # This source region is well covered by policy 1 but nearly absent from
    # policy 0, so a high-membership policy-0 candidate has strong marginal value.
    assert gains[0, 0] > gains[0, 1]
    assert 0 <= gains[0, 0] <= 1


def test_descriptor_and_coverage_inputs_are_validated():
    with pytest.raises(ValueError, match="positive mass"):
        discover_open_vocabulary_descriptors(
            [[]],
            np.zeros((1, 2)),
        )

    with pytest.raises(ValueError, match="uniquely"):
        PolicyCoverageDiagnostics().fit(
            np.ones((4, 2)),
            ("duplicate", "duplicate"),
            np.ones((4, 1)),
        )
