import pytest

from services.edit_events import (
    classify_reconciled_state,
    develop_settings_state,
    recipe_target_state,
    state_fingerprint,
)


def test_recipe_and_lightroom_settings_share_modeled_fingerprint_geometry():
    target = recipe_target_state(
        {
            "global": {
                "exposure": 0.75,
                "contrast": 12.0,
                "crop": {"left": 0.1, "right": 0.9, "angle": 1.25},
            }
        }
    )
    current = develop_settings_state(
        {
            "Exposure2012": 0.75,
            "Contrast2012": 12,
            "CropLeft": 0.1,
            "CropRight": 0.9,
            "CropAngle": 1.25,
            "Clarity2012": 99,
        },
        tuple(target),
    )

    assert current == target
    assert state_fingerprint(current) == state_fingerprint(target)


def test_fingerprint_ignores_unmodeled_settings_and_dictionary_order():
    first = develop_settings_state(
        {"Exposure2012": 0.5, "Contrast2012": 10, "Clarity2012": 20},
        ("exposure", "contrast"),
    )
    second = develop_settings_state(
        {"Clarity2012": -80, "Contrast2012": 10.0, "Exposure2012": 0.5},
        ("contrast", "exposure"),
    )

    assert state_fingerprint(first) == state_fingerprint(second)


def test_reconciliation_separates_revert_from_divergence():
    assert (
        classify_reconciled_state(
            current_fingerprint="after",
            pre_edit_fingerprint="before",
            applied_fingerprint="after",
        )
        == "apply_confirmed"
    )
    assert (
        classify_reconciled_state(
            current_fingerprint="before",
            pre_edit_fingerprint="before",
            applied_fingerprint="after",
        )
        == "reverted"
    )
    assert (
        classify_reconciled_state(
            current_fingerprint="manual",
            pre_edit_fingerprint="before",
            applied_fingerprint="after",
        )
        == "diverged"
    )


def test_invalid_or_nonfinite_states_are_rejected():
    with pytest.raises(ValueError, match="global settings"):
        recipe_target_state({"global": {}})
    with pytest.raises(ValueError, match="finite"):
        state_fingerprint({"exposure": float("nan")})
