import numpy as np
import pytest

from services.policy_features import (
    FEATURE_SCHEMA_VERSION,
    build_source_feature_vector,
)


def test_source_features_are_deterministic_and_embedding_is_normalized():
    metadata = {
        "exp_luminance_mean": 0.4,
        "iso": 400,
        "camera_make": "Example",
        "camera_profile": "Linear",
        "is_hdr": True,
    }
    first = build_source_feature_vector(
        metadata,
        image_embedding=[3.0, 4.0],
        source_provenance="raw_preview",
    )
    second = build_source_feature_vector(
        metadata,
        image_embedding=[3.0, 4.0],
        source_provenance="raw_preview",
    )

    assert first == second
    assert first.schema_version == FEATURE_SCHEMA_VERSION
    assert first.values[:2] == pytest.approx((0.6, 0.8))
    assert first.categories["hdr_state"] == "hdr"
    assert not any("genre" in name for name in first.names)


def test_missing_features_have_explicit_availability_mask():
    features = build_source_feature_vector(
        {},
        image_embedding=[1.0, 0.0],
        source_provenance="embedded_camera_preview",
    )
    luminance_index = features.names.index("exp_luminance_mean")
    assert features.values[luminance_index] == 0.0
    assert features.availability[luminance_index] is False
    assert features.categories["camera_model"] == "unknown"


def test_non_finite_embedding_is_rejected():
    with pytest.raises(ValueError, match="non-finite"):
        build_source_feature_vector(
            {},
            image_embedding=np.array([1.0, np.nan]),
            source_provenance="raw_preview",
        )
