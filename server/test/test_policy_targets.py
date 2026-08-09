import pytest

from services.policy_targets import (
    AbsoluteTarget,
    TARGET_SCHEMA_VERSION,
    flatten_absolute_target,
    interpolate_absolute_target,
    unflatten_absolute_target,
)


def _target(values):
    return AbsoluteTarget(
        schema_version=TARGET_SCHEMA_VERSION,
        process_version="Version 6",
        values=values,
        modeled_paths=tuple(values),
    )


def test_full_strength_converges_from_neutral_and_edited_starts():
    target = _target(
        {
            "exposure": 0.75,
            "contrast": 20.0,
            "hsl": {"red": {"saturation": -12.0}},
        }
    )
    neutral = {"exposure": 0.0, "contrast": 0.0}
    edited = {
        "exposure": -2.0,
        "contrast": 80.0,
        "hsl": {"red": {"saturation": 50.0}},
    }

    assert interpolate_absolute_target(neutral, target, strength=1.0) == target.values
    assert interpolate_absolute_target(edited, target, strength=1.0) == target.values


def test_partial_strength_interpolates_from_current_not_neutral():
    target = _target({"exposure": 1.0, "contrast": -20.0})
    result = interpolate_absolute_target(
        {"exposure": -1.0, "contrast": 40.0},
        target,
        strength=0.5,
    )
    assert result == {"exposure": 0.0, "contrast": 10.0}


def test_full_strength_is_idempotent():
    target = _target({"exposure": 0.4, "sharpening": 65.0})
    first = interpolate_absolute_target({}, target, strength=1.0)
    second = interpolate_absolute_target(first, target, strength=1.0)
    assert second == first == target.values


def test_zero_strength_uses_current_or_true_lightroom_neutral():
    target = _target(
        {
            "sharpening": 70.0,
            "crop": {"left": 0.1, "right": 0.9, "top": 0.2, "bottom": 0.8},
        }
    )
    result = interpolate_absolute_target({}, target, strength=0.0)
    assert result["sharpening"] == 40.0
    assert result["crop"] == {
        "left": 0.0,
        "right": 1.0,
        "top": 0.0,
        "bottom": 1.0,
    }


def test_color_grading_hue_uses_shortest_circular_path():
    target = _target({"color_grading": {"shadows": {"hue": 10.0}}})
    result = interpolate_absolute_target(
        {"color_grading": {"shadows": {"hue": 350.0}}},
        target,
        strength=0.5,
    )
    assert result["color_grading"]["shadows"]["hue"] == pytest.approx(0.0)


def test_curve_defaults_to_linear_and_reaches_target_exactly():
    curve = [0.0, 0.0, 128.0, 180.0, 255.0, 255.0]
    target = _target({"tone_curve": {"point_curve": {"master": curve}}})
    halfway = interpolate_absolute_target({}, target, strength=0.5)
    assert halfway["tone_curve"]["point_curve"]["master"][3] == pytest.approx(154.0)
    assert interpolate_absolute_target({}, target, strength=1.0) == target.values


def test_white_balance_uses_calibrated_categorical_threshold():
    target = _target({"white_balance": "Custom"})
    current = {"white_balance": "As Shot"}
    assert (
        interpolate_absolute_target(current, target, strength=0.69)["white_balance"]
        == "As Shot"
    )
    assert (
        interpolate_absolute_target(current, target, strength=0.7)["white_balance"]
        == "Custom"
    )


def test_strength_is_clamped_and_non_finite_rejected():
    target = _target({"exposure": 1.0})
    assert interpolate_absolute_target({"exposure": 0.0}, target, strength=2.0) == {
        "exposure": 1.0
    }
    with pytest.raises(ValueError, match="finite"):
        interpolate_absolute_target({}, target, strength=float("nan"))


def test_flat_target_round_trip_preserves_nested_targets():
    canonical = {
        "exposure": 0.4,
        "hsl": {"red": {"saturation": -12.0}},
        "color_grading": {
            "shadows": {"hue": 220.0, "saturation": 8.0},
            "blending": 65.0,
        },
        "tone_curve": {
            "point_curve": {"master": [0.0, 0.0, 128.0, 150.0, 255.0, 255.0]}
        },
        "crop": {"left": 0.1, "right": 0.9, "angle": 1.5},
        "white_balance": "Custom",
    }
    rebuilt = unflatten_absolute_target(flatten_absolute_target(canonical))

    assert rebuilt["exposure"] == pytest.approx(0.4)
    assert rebuilt["hsl"] == canonical["hsl"]
    assert rebuilt["color_grading"]["shadows"]["hue"] == pytest.approx(220.0)
    assert rebuilt["color_grading"]["shadows"]["saturation"] == 8.0
    assert rebuilt["color_grading"]["blending"] == 65.0
    assert rebuilt["crop"] == canonical["crop"]
    assert rebuilt["white_balance"] == "Custom"
    assert len(rebuilt["tone_curve"]["point_curve"]["master"]) == 32


def test_neutral_geometry_is_learned_as_no_crop_and_no_rotation():
    flat = flatten_absolute_target(
        {
            "crop": {
                "left": 0.0,
                "right": 1.0,
                "top": 0.0,
                "bottom": 1.0,
                "angle": 0.0,
            }
        },
        include_applicability=True,
    )

    assert flat["crop_is_applied"] == 0.0
    assert flat["rotation_is_applied"] == 0.0
    assert "crop" not in unflatten_absolute_target(flat)


@pytest.mark.parametrize(
    ("crop_score", "rotation_score", "expected"),
    (
        (0.69, 0.69, None),
        (0.70, 0.69, {"left": 0.1, "right": 0.9}),
        (0.69, 0.70, {"angle": 1.5}),
        (0.70, 0.70, {"left": 0.1, "right": 0.9, "angle": 1.5}),
    ),
)
def test_crop_and_rotation_require_independent_conservative_gates(
    crop_score,
    rotation_score,
    expected,
):
    rebuilt = unflatten_absolute_target(
        {
            "crop_left": 0.1,
            "crop_right": 0.9,
            "crop_angle": 1.5,
            "crop_is_applied": crop_score,
            "rotation_is_applied": rotation_score,
        }
    )

    assert rebuilt.get("crop") == expected


def test_as_shot_white_balance_does_not_apply_numeric_overrides():
    rebuilt = unflatten_absolute_target(
        {
            "white_balance_is_custom": 0.69,
            "temperature": 7200.0,
            "tint": 18.0,
        }
    )

    assert rebuilt["white_balance"] == "As Shot"
    assert "temperature" not in rebuilt
    assert "tint" not in rebuilt


def test_color_grading_hue_modeling_wraps_across_zero_degrees():
    first = flatten_absolute_target({"color_grading": {"shadows": {"hue": 359.0}}})
    second = flatten_absolute_target({"color_grading": {"shadows": {"hue": 1.0}}})
    averaged = {
        key: (first[key] + second[key]) / 2.0
        for key in first
        if key.startswith("cg_shadows_hue_")
    }

    rebuilt = unflatten_absolute_target(averaged)

    assert rebuilt["color_grading"]["shadows"]["hue"] == pytest.approx(0.0, abs=1e-6)


def test_absolute_target_rejects_invalid_crop_geometry():
    target = _target({"crop": {"left": 0.8, "right": 0.2}})

    with pytest.raises(ValueError, match="crop left"):
        target.validate()
