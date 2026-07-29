from types import SimpleNamespace
from unittest.mock import patch

from services.style_engine import (
    StyleEngineResult,
    _canonical_to_edit_recipe,
    generate_style_edit,
)


def test_canonical_recipe_preserves_all_absolute_target_families():
    recipe = _canonical_to_edit_recipe(
        {
            "exposure": 0.35,
            "sharpen_detail": 40.0,
            "white_balance": "As Shot",
            "crop": {"left": 0.1, "right": 0.9},
            "tone_curve_highlights": 15.0,
            "tone_curve": {"point_curve": {"master": [0.0, 0.0, 255.0, 255.0]}},
        }
    )

    assert recipe["global"]["exposure"] == 0.35
    assert recipe["global"]["sharpen_detail"] == 40.0
    assert recipe["global"]["white_balance"] == "As Shot"
    assert recipe["global"]["crop"] == {"left": 0.1, "right": 0.9}
    assert recipe["global"]["tone_curve"]["highlights"] == 15.0


@patch("services.style_engine.policy_runtime.predict_absolute_edit")
@patch("services.style_engine.policy_runtime.has_active_generation", return_value=True)
@patch("services.style_engine.training_service.compute_exposure_metrics")
def test_generate_style_edit_uses_active_policy_without_legacy_blending(
    mock_exposure,
    _mock_active,
    mock_predict,
):
    mock_exposure.return_value = {"exp_luminance_mean": 0.5}
    mock_predict.return_value = SimpleNamespace(
        policy_id="policy-1",
        policy_name="Soft Contrast",
        confidence=0.92,
        applied={"exposure": 0.35},
        example_count=18,
    )

    result = generate_style_edit(
        "target",
        b"preview",
        clip_embedding=[1.0, 0.0],
        current_settings={"Exposure2012": -2.0},
        style_strength=1.0,
    )

    assert isinstance(result, StyleEngineResult)
    assert result.engine == "policy_v2"
    assert result.confidence == 0.92
    assert result.recipe["global"]["exposure"] == 0.35
    mock_predict.assert_called_once()


@patch("services.style_engine.policy_runtime.predict_absolute_edit")
@patch("services.style_engine.policy_runtime.has_active_generation", return_value=True)
@patch("services.style_engine.training_service.compute_exposure_metrics")
def test_generate_style_edit_never_switches_models_at_an_example_count_threshold(
    mock_exposure,
    _mock_active,
    mock_predict,
):
    mock_exposure.return_value = {}
    mock_predict.return_value = SimpleNamespace(
        policy_id="policy-1",
        policy_name="Stable Policy",
        confidence=0.9,
        applied={"exposure": 0.2},
        example_count=800,
    )

    result = generate_style_edit(
        "target",
        b"preview",
        clip_embedding=[1.0, 0.0],
        camera_profile="Adobe Color",
    )

    assert result.engine == "policy_v2"
    mock_predict.assert_called_once()


@patch("services.style_engine.policy_runtime.has_active_generation", return_value=False)
def test_generate_style_edit_requires_active_policy(_mock_active):
    result = generate_style_edit("target", b"preview")

    assert result.engine == "none"
    assert "No trained editing-policy generation" in result.warning
