import json

import numpy as np

from services.rendering_state import (
    RENDERING_STATE_SCHEMA_VERSION,
    _bounded_group_indices,
    fit_rendering_selector,
    profile_id,
    rendering_partition_key,
    rendering_state_from_metadata,
    rendering_state_from_settings,
)


def _state(profile, *, hdr=False, model="Z 7"):
    return rendering_state_from_settings(
        {"CameraProfile": profile, "HDREditMode": int(hdr)},
        camera_make="Nikon",
        camera_model=model,
    )


def test_rendering_contract_separates_legacy_hdr_from_profile_name():
    state = rendering_state_from_metadata(
        {
            "camera_make": "Nikon",
            "camera_model": "Z 7",
            "camera_profile": "Nikon Z7 AgX + HDR",
        }
    )

    assert state["schema_version"] == RENDERING_STATE_SCHEMA_VERSION
    assert state["profile"]["display_name"] == "Nikon Z7 AgX"
    assert state["is_hdr"] is True


def test_profile_identity_includes_sdk_representation_and_camera_compatibility():
    first = _state("Custom", model="Z 7")
    second = _state("Custom", model="Z 8")
    raw = rendering_state_from_settings(
        {"CameraProfileRaw": "custom-id", "HDREditMode": 0},
        camera_make="Nikon",
        camera_model="Z 7",
    )

    assert first["profile"]["profile_id"] != second["profile"]["profile_id"]
    assert first["profile"]["profile_id"] != raw["profile"]["profile_id"]
    assert rendering_partition_key(first).startswith("sdr|")


def test_profile_identity_ignores_non_identity_look_diagnostics():
    common = {"Name": "Custom", "UUID": "profile-uuid", "Amount": 1.0}
    first = profile_id(
        camera_make="Nikon",
        camera_model="Z 7",
        sdk_representation={"Look": {**common, "DebugDescription": "first"}},
    )
    second = profile_id(
        camera_make="Nikon",
        camera_model="Z 7",
        sdk_representation={"Look": {"DebugDescription": "second", **common}},
    )

    assert first == second


def test_selector_uses_only_neutral_source_evidence_and_respects_modes():
    rows = []
    for hdr in (False, True):
        for profile_index, profile in enumerate(("Base", "Contrast")):
            for index in range(12):
                vector = np.asarray(
                    [
                        1.0 if hdr else -1.0,
                        (1.0 if profile_index else -1.0) * (-1.0 if hdr else 1.0),
                        (index - 5.5) * 0.002,
                    ],
                    dtype=float,
                )
                vector /= np.linalg.norm(vector)
                state = _state(profile, hdr=hdr)
                rows.append(
                    {
                        "photo_id": f"{hdr}-{profile}-{index}",
                        "metadata": {"source_provenance": "raw_preview"},
                        "normalized_embedding": vector,
                        "burst_group_id": f"burst-{hdr}-{profile}-{index}",
                        "rendering_state": state,
                    }
                )
    artifact = fit_rendering_selector(rows, generation_id="generation")
    current = _state("Base", hdr=False)
    query = np.asarray([1.0, 1.0, 0.0])
    query /= np.linalg.norm(query)

    selected = artifact.select(
        embedding=query,
        current_state=current,
        camera_make="Nikon",
        camera_model="Z 7",
        profile_mode="auto",
        hdr_mode="auto",
        source_provenance="raw_preview",
    )
    unsafe = artifact.select(
        embedding=query,
        current_state=current,
        camera_make="Nikon",
        camera_model="Z 7",
        profile_mode="auto",
        hdr_mode="auto",
        source_provenance="lightroom_rendered_preview",
    )

    assert selected["effective"]["is_hdr"] is True
    assert selected["effective"]["profile"]["display_name"] == "Base"
    assert unsafe["effective"] == current
    assert "source_not_neutral" in unsafe["abstention_reason"]

    mixed = artifact.select(
        embedding=query,
        current_state=current,
        camera_make="Nikon",
        camera_model="Z 7",
        profile_mode="auto",
        hdr_mode="suggest",
        source_provenance="raw_preview",
    )
    assert mixed["proposed"]["is_hdr"] is True
    assert mixed["effective"]["is_hdr"] is False
    assert mixed["effective"]["profile"]["display_name"] == "Contrast"

    other_camera = _state("Base", model="Z 8")
    incompatible = artifact.select(
        embedding=query,
        current_state=other_camera,
        camera_make="Nikon",
        camera_model="Z 8",
        profile_mode="auto",
        hdr_mode="auto",
        source_provenance="raw_preview",
    )
    assert incompatible["effective"] == other_camera
    assert "selector_unavailable" in incompatible["abstention_reason"]

    for profile_mode in ("off", "suggest", "auto"):
        for hdr_mode in ("off", "suggest", "auto"):
            result = artifact.select(
                embedding=query,
                current_state=current,
                camera_make="Nikon",
                camera_model="Z 7",
                profile_mode=profile_mode,
                hdr_mode=hdr_mode,
                source_provenance="raw_preview",
            )
            assert result["effective"]["is_hdr"] is (hdr_mode == "auto")
            expected_profile = (
                "Base"
                if profile_mode == "off"
                or profile_mode == "suggest"
                or hdr_mode == "auto"
                else "Contrast"
            )
            assert result["effective"]["profile"]["display_name"] == expected_profile


def test_auto_requires_more_evidence_than_suggest():
    rows = []
    for hdr in (False, True):
        for profile_index, profile in enumerate(("Base", "Contrast")):
            for index in range(6):
                vector = np.asarray(
                    [
                        1.0 if hdr else -1.0,
                        (1.0 if profile_index else -1.0) * (-1.0 if hdr else 1.0),
                        index * 0.001,
                    ]
                )
                vector /= np.linalg.norm(vector)
                rows.append(
                    {
                        "metadata": {"source_provenance": "raw_preview"},
                        "normalized_embedding": vector,
                        "burst_group_id": f"{hdr}-{profile}-{index}",
                        "rendering_state": _state(profile, hdr=hdr),
                    }
                )
    artifact = fit_rendering_selector(rows, generation_id="generation")
    compatibility = _state("Base")["profile"]["compatibility_key"]

    assert artifact.hdr_models[compatibility].validation["auto_eligible"] is False
    assert (
        artifact.profile_models[f"{compatibility}|sdr"].validation["auto_eligible"]
        is False
    )


def test_bounded_validation_never_splits_burst_groups():
    groups = np.asarray(["a", "a", "b", "b", "c", "d"], dtype=object)
    selected = _bounded_group_indices(groups, 3)
    selected_set = set(selected.tolist())

    assert len(selected) <= 3
    for group in np.unique(groups):
        members = set(np.flatnonzero(groups == group).tolist())
        assert not (selected_set & members) or members <= selected_set


def test_rendering_state_json_round_trip_is_preferred_over_legacy_name():
    state = _state("Custom + HDR", hdr=False)
    loaded = rendering_state_from_metadata(
        {
            "rendering_state_json": json.dumps(state),
            "camera_profile": "Wrong Legacy + HDR",
        }
    )
    assert loaded == state
