import sys
import os
from unittest.mock import patch

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from services.style_engine import (
    calculate_composite_score,
    interpolate_recipes,
    generate_style_edit,
    StyleEngineResult,
    _finalize_recipe,
)


def test_calculate_composite_score():
    query_exposure = {"exp_luminance_mean": 0.5, "exp_contrast": 0.5}
    candidate = {
        "exp_luminance_mean": 0.52,
        "exp_contrast": 0.48,
        "scene_tags": ["scene_outdoor", "scene_landscape"],
        "time_of_day_bucket": "afternoon",
    }
    query_scene_tags = ["scene_outdoor", "scene_landscape"]
    query_tod = "afternoon"
    clip_sim = 0.95

    score = calculate_composite_score(
        clip_sim=clip_sim,
        query_exposure=query_exposure,
        candidate=candidate,
        query_scene_tags=query_scene_tags,
        query_tod=query_tod,
    )

    assert score > 0.8

    # Test lower score with mismatch
    score_mismatch = calculate_composite_score(
        clip_sim=0.2,  # low sim
        query_exposure=query_exposure,
        candidate=candidate,
        query_scene_tags=["scene_indoor"],  # mismatch
        query_tod="night",  # mismatch
    )

    assert score > score_mismatch


def test_interpolate_recipes():
    # Example 1: Exposure +1
    ex1 = {"canonical_settings": {"exposure": 1.0, "contrast": 10}}
    # Example 2: Exposure -1
    ex2 = {"canonical_settings": {"exposure": -1.0, "contrast": 50}}

    winners = [(ex1, 0.5), (ex2, 0.5)]

    interpolated = interpolate_recipes(winners)

    # (1.0*0.5) + (-1.0*0.5) = 0
    assert interpolated.get("exposure") == 0.0
    # (10*0.5) + (50*0.5) = 30
    assert interpolated.get("contrast") == 30


@patch("services.style_engine.training_service")
def test_generate_style_edit_adaptive(mock_training):
    # If training photo was dark (0.2) and target is bright (0.8)
    # The engine should suggest LOWERING exposure relative to what was done to the dark photo

    # Mock training service stats
    mock_training.get_training_count.return_value = 20

    # Mock query_similar_training_examples
    # ex1 was a dark photo (0.2) and we gave it +0.5 exposure
    mock_ex = {
        "photo_id": "ex1",
        "filename": "ex1.jpg",
        "exp_luminance_mean": 0.2,
        "exp_contrast": 0.5,
        "canonical_settings": {"exposure": 0.5, "contrast": 20},
        "distance": 0.05,  # high sim
        "scene_tags": [],
        "time_of_day_bucket": "unknown",
    }
    mock_training.query_similar_training_examples.return_value = [mock_ex]

    # Mock compute_exposure_metrics (target is bright: 0.8)
    mock_training.compute_exposure_metrics.return_value = {
        "exp_luminance_mean": 0.8,
        "exp_contrast": 0.5,
    }
    mock_training.compute_scene_tags.return_value = []
    mock_training.time_of_day_bucket.return_value = "unknown"
    mock_training.focal_length_bucket.return_value = "unknown"

    # Run the engine
    result = generate_style_edit(
        photo_id="target1", image_bytes=b"fake", clip_embedding=[0.1] * 512
    )

    assert isinstance(result, StyleEngineResult)
    assert result.engine == "style"

    # target (0.8) - training (0.2) = 0.6 difference.
    # Current logic: exposure_correction = -lum_delta * 5.0
    # -0.6 * 5.0 = -3.0 (clamped to -1.5)
    # Base exposure was 0.5. Result should be 0.5 - 1.5 = -1.0

    final_exposure = result.recipe["global"].get("exposure")
    assert final_exposure < 0.5  # Should have been lowered
    assert final_exposure == -1.0  # 0.5 + (-1.5)


@patch("services.style_engine.training_service")
def test_generate_style_edit_not_enough_training(mock_training):
    mock_training.get_training_count.return_value = 2

    result = generate_style_edit(photo_id="target1", image_bytes=b"fake")

    assert result.engine == "none"
    assert "inactive" in result.warning
    assert "2" in result.warning


def test_finalize_recipe_interpolation():
    recipe = {
        "global": {
            "highlights": -60.0,
            "shadows": 50.0,
        }
    }
    # Simulate Adobe Auto already applied to photo
    current_settings = {
        "Highlights2012": -50.0,
        "Shadows2012": 50.0,
    }

    # At 100% strength, final settings should match style target exactly (no double stacking to -110 or +100)
    final_100 = _finalize_recipe(recipe, {}, current_settings, style_strength=1.0)
    assert final_100["global"]["highlights"] == -60.0
    assert final_100["global"]["shadows"] == 50.0

    # At 50% strength, final settings should blend halfway between current (-50) and target (-60) -> -55
    final_50 = _finalize_recipe(recipe, {}, current_settings, style_strength=0.5)
    assert final_50["global"]["highlights"] == -55.0
    assert final_50["global"]["shadows"] == 50.0


def test_interpolate_recipes_crop_fallback():
    # ex1 is cropped (right=0.8), ex2 is uncropped (no crop dict)
    ex1 = {
        "canonical_settings": {
            "crop": {"left": 0.0, "right": 0.8, "top": 0.1, "bottom": 0.9, "angle": 0.0}
        }
    }
    ex2 = {"canonical_settings": {}}
    winners = [(ex1, 0.5), (ex2, 0.5)]
    res = interpolate_recipes(winners)
    assert "crop" in res
    # Uncropped right is 1.0, so (0.8*0.5 + 1.0*0.5) = 0.9 (before aspect ratio averaging)
    # Let's check that it did not shrink toward 0.4
    assert res["crop"]["right"] > 0.7


def test_interpolate_recipes_tone_curve_fallback():
    # ex1 has a custom curve, ex2 has no curve (should default to linear)
    ex1 = {
        "canonical_settings": {
            "tone_curve": {"point_curve": {"master": [0, 0, 128, 180, 255, 255]}}
        }
    }
    ex2 = {"canonical_settings": {}}
    winners = [(ex1, 0.5), (ex2, 0.5)]
    res = interpolate_recipes(winners)
    assert "tone_curve" in res
    # At x=136.0 (index 8), ex1 curve is ~184.7, linear baseline is 136.0 -> average should be ~160.4
    master_curve = res["tone_curve"]["point_curve"]["master"]
    ys = master_curve[1::2]
    assert 150.0 < ys[8] < 170.0


def test_finalize_recipe_no_auto_wb():
    # Even if warmth_proxy is extreme (0.05 or 0.95), white_balance must not be overridden to Auto
    recipe = {"global": {"exposure": 0.5}, "white_balance": "As Shot"}
    query_exposure = {"exp_warmth_proxy": 0.95}
    final_recipe = _finalize_recipe(recipe, query_exposure, {}, style_strength=1.0)
    assert final_recipe.get("white_balance") == "As Shot"


@patch("services.style_engine.training_service")
@patch("services.style_catalog.find_matching_styles")
@patch("services.predictive_engine.predict_edits")
@patch("services.style_catalog.get_style_recipe")
def test_generate_style_edit_ml_fallback_to_knn(
    mock_get_recipe, mock_predict, mock_find, mock_training
):
    mock_training.get_training_count.return_value = 20
    mock_training.compute_exposure_metrics.return_value = {"exp_luminance_mean": 0.5}
    mock_training.compute_scene_tags.return_value = ["scene_portrait"]
    mock_training.time_of_day_bucket.return_value = "afternoon"
    mock_training.focal_length_bucket.return_value = "normal"

    mock_find.return_value = [
        (
            {
                "style_id": "style-15-examples",
                "style_name": "Pro Portrait Style",
                "camera_profile": "Adobe Standard",
                "example_count": 20,
            },
            0.85,
        )
    ]
    mock_predict.return_value = None
    mock_get_recipe.return_value = {"exposure": 0.4, "contrast": 10}

    res = generate_style_edit("photo_test", b"fake_bytes", style_strength=1.0)
    assert res.engine != "error"
    assert res.error is None
    assert "exposure" in res.recipe.get("global", {})
