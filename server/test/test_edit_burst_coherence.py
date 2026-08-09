"""Safety and boundary tests for operation-scoped edit burst coherence."""

from __future__ import annotations

from dataclasses import replace
import math

import pytest

from services import edit_burst_coherence as coherence
from services.policy_features import SOURCE_METRIC_KEYS
from services.source_embeddings import RAW_PREVIEW_PROVENANCE


def _vector(angle: float) -> tuple[float, float]:
    return (math.cos(angle), math.sin(angle))


def _evidence(
    photo_id: str,
    *,
    capture_time: float = 100.0,
    angle: float = 0.0,
    policy_id: str = "policy-1",
    partition: str = "sdr|adobe color",
    provenance: str = RAW_PREVIEW_PROVENANCE,
    luminance: float = 0.5,
    warmth: float = 0.5,
    shutter: str = "1/1000",
    is_panorama: bool = False,
    camera_model: str = "R5",
) -> coherence.BurstEvidence:
    metrics = {key: 0.25 for key in SOURCE_METRIC_KEYS}
    metrics.update(
        {
            "exp_luminance_mean": luminance,
            "exp_colorfulness": 0.4,
            "exp_warmth_proxy": warmth,
        }
    )
    return coherence.BurstEvidence(
        photo_id=photo_id,
        capture_time=capture_time,
        embedding=_vector(angle),
        source_provenance=provenance,
        source_metrics=metrics,
        camera_make="Canon",
        camera_model=camera_model,
        camera_profile="Adobe Color",
        lens="70-200",
        iso=400.0,
        aperture=2.8,
        shutter_speed=shutter,
        focal_length=200.0,
        is_panorama=is_panorama,
        hard_partition_key=partition,
        policy_id=policy_id,
        confidence=0.9,
        entropy=0.1,
    )


def test_temporal_neighbor_grouping_is_transitive_and_stable():
    evidence = [
        _evidence("a", capture_time=100.0, angle=0.00),
        _evidence("b", capture_time=103.0, angle=0.12),
        _evidence("c", capture_time=106.0, angle=0.24),
    ]
    first, _ = coherence.decide_reuse_tiers(evidence, "operation-1")
    second, _ = coherence.decide_reuse_tiers(evidence, "operation-1")

    group_ids = {decision.group_id for decision in first.values()}
    assert len(group_ids) == 1
    assert None not in group_ids
    assert {key: value.group_id for key, value in first.items()} == {
        key: value.group_id for key, value in second.items()
    }


def test_candidate_visual_medoid_is_inferred_first_without_losing_members():
    evidence = [
        _evidence("a", angle=0.0),
        _evidence("b", capture_time=100.1, angle=0.1),
        _evidence("c", capture_time=100.2, angle=0.2),
        _evidence("unrelated", capture_time=200.0, angle=1.0),
    ]

    order = coherence.representative_first_order(evidence, "operation-1")

    assert order[0] == 1
    assert sorted(order) == [0, 1, 2, 3]


def test_oversized_transitive_group_splits_deterministically(monkeypatch):
    monkeypatch.setattr(coherence, "MAX_GROUP_SIZE", 3)
    evidence = [
        _evidence(str(index), capture_time=100.0 + index * 0.1, angle=index * 0.01)
        for index in range(7)
    ]
    groups, diagnostics = coherence.build_candidate_groups(evidence, "operation-1")

    assert [len(group) for group in groups] == [3, 3]
    assert diagnostics["group_sizes"] == [3, 3]


@pytest.mark.parametrize(
    ("seconds", "angle", "grouped"),
    (
        (coherence.MAX_CAPTURE_DELTA_SECONDS, 0.0, True),
        (coherence.MAX_CAPTURE_DELTA_SECONDS + 0.001, 0.0, False),
        (1.0, math.acos(1.0 - coherence.MAX_COSINE_DISTANCE), True),
        (1.0, math.acos(1.0 - coherence.MAX_COSINE_DISTANCE - 0.001), False),
    ),
)
def test_candidate_ceiling_boundaries(seconds, angle, grouped):
    groups, _ = coherence.build_candidate_groups(
        [
            _evidence("a", capture_time=100.0),
            _evidence("b", capture_time=100.0 + seconds, angle=angle),
        ],
        "operation-1",
    )
    assert bool(groups) is grouped


@pytest.mark.parametrize(
    "changed",
    (
        {"is_panorama": True},
        {"camera_model": "R6"},
        {"shutter": "1/2000"},
    ),
)
def test_panorama_camera_change_and_bracket_are_rejected(changed):
    first = _evidence("a")
    second = _evidence("b", capture_time=100.2, **changed)
    groups, diagnostics = coherence.build_candidate_groups(
        [first, second], "operation-1"
    )

    assert groups == []
    assert sum(diagnostics["rejection_reasons"].values()) == 1


@pytest.mark.parametrize(
    "second",
    (
        replace(_evidence("b"), camera_make=None),
        replace(_evidence("b"), camera_profile="Camera Standard"),
        replace(_evidence("b"), embedding=(float("nan"), 0.0)),
    ),
)
def test_missing_exif_profile_change_and_invalid_embedding_fall_back(second):
    groups, diagnostics = coherence.build_candidate_groups(
        [_evidence("a"), second], "operation-1"
    )

    assert groups == []
    assert sum(diagnostics["rejection_reasons"].values()) == 1


def test_policy_and_partition_disagreement_fall_back_independently():
    evidence = [
        _evidence("a"),
        _evidence("b", capture_time=100.1, policy_id="policy-2"),
        _evidence("c", capture_time=100.2, partition="hdr|adobe color", angle=0.005),
    ]
    decisions, _ = coherence.decide_reuse_tiers(evidence, "operation-1")

    assert decisions["b"].tier == "independent"
    assert decisions["b"].fallback_reason == "policy_mismatch_or_ambiguity"
    assert decisions["c"].tier == "independent"
    assert decisions["c"].fallback_reason == "rendering_partition_mismatch"


def test_moderate_member_keeps_own_prediction_when_exact_gate_is_off(monkeypatch):
    monkeypatch.delenv("STYLEAI_EDIT_BURST_EXACT_REUSE", raising=False)
    evidence = [
        _evidence("a", capture_time=100.0),
        _evidence("b", capture_time=100.2, angle=0.005),
    ]
    decisions, diagnostics = coherence.decide_reuse_tiers(evidence, "operation-1")

    tiers = {decision.tier for decision in decisions.values()}
    assert tiers == {"independent", "policy_coherent"}
    assert diagnostics["tier_counts"]["policy_coherent"] == 1


def test_exact_reuse_requires_raw_evidence_and_strict_metric_agreement(monkeypatch):
    monkeypatch.setenv("STYLEAI_EDIT_BURST_EXACT_REUSE", "1")
    raw = [_evidence("a"), _evidence("b", capture_time=100.2, angle=0.005)]
    rendered = [
        _evidence("a"),
        _evidence(
            "b",
            capture_time=100.2,
            angle=0.005,
            provenance="lightroom_rendered_preview",
        ),
    ]

    raw_decisions, _ = coherence.decide_reuse_tiers(raw, "operation-1")
    rendered_decisions, _ = coherence.decide_reuse_tiers(rendered, "operation-1")

    assert {item.tier for item in raw_decisions.values()} == {
        "independent",
        "global_target_reuse",
    }
    assert {item.tier for item in rendered_decisions.values()} == {
        "independent",
        "policy_coherent",
    }


def test_exact_merge_copies_only_allowlisted_global_scalars():
    representative = {
        "exposure": 1.0,
        "temperature": 7000.0,
        "white_balance": "Custom",
        "crop": {"left": 0.1, "right": 0.9, "angle": 2.0},
        "hsl": {"red": {"saturation": 20.0}},
        "vignette": -30.0,
        "vibrance": 15.0,
    }
    member = {
        "exposure": -0.25,
        "temperature": 5200.0,
        "white_balance": "As Shot",
        "crop": {"angle": -1.0},
        "hsl": {"red": {"saturation": -5.0}},
        "vignette": 0.0,
        "vibrance": 3.0,
    }

    merged = coherence.merge_global_target(representative, member)

    assert merged["exposure"] == 1.0
    assert merged["vibrance"] == 15.0
    assert merged["temperature"] == 5200.0
    assert merged["white_balance"] == "As Shot"
    assert merged["crop"] == {"angle": -1.0}
    assert merged["hsl"] == {"red": {"saturation": -5.0}}
    assert merged["vignette"] == 0.0
