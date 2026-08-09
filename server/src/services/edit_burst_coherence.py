"""Conservative, operation-scoped burst coherence for learned edits.

Candidate grouping is deliberately independent of policy inference.  Every
photo keeps its own source evidence and must first receive an ordinary
production prediction.  The decisions returned here describe which result
families may subsequently be shared; they never make a representative the
source of truth for another photo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from typing import Any, Iterable

import numpy as np

from .policy_features import SOURCE_METRIC_KEYS
from .source_embeddings import RAW_PREVIEW_PROVENANCE


GROUPING_SCHEMA_VERSION = "edit-burst-group-v1"
REUSE_POLICY_VERSION = "edit-burst-reuse-v1"
THRESHOLD_VERSION = "edit-burst-thresholds-v1"

MAX_CAPTURE_DELTA_SECONDS = 10.0
MAX_COSINE_DISTANCE = 0.05
MAX_GROUP_SIZE = 16

# Moderate admission is useful only for policy coherence.  Exact target reuse
# is substantially stricter and remains independently service-gated.
POLICY_COHERENT_MAX_SECONDS = 4.0
POLICY_COHERENT_MAX_DISTANCE = 0.035
GLOBAL_REUSE_MAX_SECONDS = 1.5
GLOBAL_REUSE_MAX_DISTANCE = 0.015
GLOBAL_REUSE_MAX_LUMINANCE_DELTA = 0.035
GLOBAL_REUSE_MAX_COLOR_DELTA = 0.05
GLOBAL_REUSE_MAX_EXPOSURE_VALUE_DELTA = 0.20
BRACKET_EXPOSURE_VALUE_DELTA = 0.45

GLOBAL_TARGET_REUSE_ALLOWLIST = frozenset(
    {
        "exposure",
        "contrast",
        "highlights",
        "shadows",
        "whites",
        "blacks",
        "texture",
        "clarity",
        "dehaze",
        "vibrance",
        "saturation",
        "sharpening",
        "sharpen_radius",
        "sharpen_detail",
        "sharpen_masking",
        "noise_reduction",
        "noise_reduction_detail",
        "noise_reduction_contrast",
        "color_noise_reduction",
        "color_noise_reduction_detail",
        "color_noise_reduction_smoothness",
    }
)

_COLOR_METRICS = ("exp_colorfulness", "exp_warmth_proxy")
_LUMINANCE_METRICS = (
    "exp_luminance_mean",
    "exp_luminance_std",
    "exp_highlight_ratio",
    "exp_shadow_ratio",
    "highlight_headroom",
    "shadow_headroom",
)


def exact_reuse_enabled() -> bool:
    """Return the internal release gate without creating a user preference."""
    return os.environ.get("STYLEAI_EDIT_BURST_EXACT_REUSE", "0").strip() == "1"


def coherence_enabled() -> bool:
    """Immediate service-side kill switch for the complete optimization."""
    return os.environ.get("STYLEAI_EDIT_BURST_COHERENCE", "1").strip() != "0"


@dataclass(frozen=True)
class BurstEvidence:
    photo_id: str
    capture_time: float | None
    embedding: tuple[float, ...]
    source_provenance: str
    source_metrics: dict[str, float]
    camera_make: str | None = None
    camera_model: str | None = None
    camera_profile: str | None = None
    lens: str | None = None
    iso: float | None = None
    aperture: float | None = None
    shutter_speed: str | float | None = None
    focal_length: float | None = None
    is_panorama: bool = False
    hard_partition_key: str | None = None
    policy_id: str | None = None
    confidence: float | None = None
    entropy: float | None = None


@dataclass(frozen=True)
class BurstDecision:
    photo_id: str
    tier: str = "independent"
    fallback_reason: str | None = "not_grouped"
    group_id: str | None = None
    representative_photo_id: str | None = None
    group_size: int = 1
    capture_delta_seconds: float | None = None
    cosine_distance: float | None = None
    source_metric_deltas: dict[str, float] = field(default_factory=dict)
    policy_agreement: dict[str, Any] = field(default_factory=dict)

    def provenance(self) -> dict[str, Any]:
        return {
            "grouping_schema_version": GROUPING_SCHEMA_VERSION,
            "reuse_policy_version": REUSE_POLICY_VERSION,
            "threshold_version": THRESHOLD_VERSION,
            "group_id": self.group_id,
            "representative_photo_id": self.representative_photo_id,
            "selected_tier": self.tier,
            "capture_delta_seconds": self.capture_delta_seconds,
            "cosine_distance": self.cosine_distance,
            "source_metric_deltas": dict(self.source_metric_deltas),
            "policy_agreement": dict(self.policy_agreement),
            "fallback_reason": self.fallback_reason,
            "group_size": self.group_size,
        }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalized_embedding(evidence: BurstEvidence) -> np.ndarray | None:
    vector = np.asarray(evidence.embedding, dtype=np.float64).reshape(-1)
    if vector.size == 0 or not np.all(np.isfinite(vector)):
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


def cosine_distance(first: BurstEvidence, second: BurstEvidence) -> float | None:
    left = _normalized_embedding(first)
    right = _normalized_embedding(second)
    if left is None or right is None or left.shape != right.shape:
        return None
    return max(0.0, min(2.0, 1.0 - float(left @ right)))


def _normalized_text(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    return text or None


def _same_required_text(first: Any, second: Any) -> bool:
    left, right = _normalized_text(first), _normalized_text(second)
    return left is not None and left == right


def _shutter_seconds(value: Any) -> float | None:
    if isinstance(value, str) and "/" in value:
        numerator, denominator = value.split("/", 1)
        top, bottom = _finite(numerator), _finite(denominator)
        if top is None or bottom is None or bottom <= 0:
            return None
        return top / bottom
    parsed = _finite(value)
    return parsed if parsed is not None and parsed > 0 else None


def _exposure_value(evidence: BurstEvidence) -> float | None:
    iso = _finite(evidence.iso)
    aperture = _finite(evidence.aperture)
    shutter = _shutter_seconds(evidence.shutter_speed)
    if iso is None or iso <= 0 or aperture is None or aperture <= 0 or shutter is None:
        return None
    # Exposure value normalized to ISO 100.
    return math.log2((aperture * aperture) / shutter) - math.log2(iso / 100.0)


def _metric_deltas(first: BurstEvidence, second: BurstEvidence) -> dict[str, float]:
    deltas: dict[str, float] = {}
    for key in SOURCE_METRIC_KEYS:
        left, right = (
            _finite(first.source_metrics.get(key)),
            _finite(second.source_metrics.get(key)),
        )
        if left is not None and right is not None:
            deltas[key] = round(abs(left - right), 6)
    first_ev, second_ev = _exposure_value(first), _exposure_value(second)
    if first_ev is not None and second_ev is not None:
        deltas["exposure_value"] = round(abs(first_ev - second_ev), 6)
    return deltas


def _candidate_rejection(
    first: BurstEvidence, second: BurstEvidence
) -> tuple[str | None, float | None, float | None]:
    if first.is_panorama or second.is_panorama:
        return "panorama", None, None
    if first.capture_time is None or second.capture_time is None:
        return "missing_capture_time", None, None
    capture_delta = abs(float(first.capture_time) - float(second.capture_time))
    if capture_delta > MAX_CAPTURE_DELTA_SECONDS + 1e-9:
        return "outside_capture_window", capture_delta, None
    if not _same_required_text(
        first.camera_make, second.camera_make
    ) or not _same_required_text(first.camera_model, second.camera_model):
        return "camera_mismatch", capture_delta, None
    if not _same_required_text(first.camera_profile, second.camera_profile):
        return "profile_mismatch", capture_delta, None
    first_lens, second_lens = (
        _normalized_text(first.lens),
        _normalized_text(second.lens),
    )
    if first_lens and second_lens and first_lens != second_lens:
        return "lens_mismatch", capture_delta, None
    first_focal, second_focal = (
        _finite(first.focal_length),
        _finite(second.focal_length),
    )
    if (
        first_focal is not None
        and second_focal is not None
        and abs(first_focal - second_focal) / max(first_focal, second_focal, 1.0) > 0.08
    ):
        return "focal_length_mismatch", capture_delta, None
    distance = cosine_distance(first, second)
    if distance is None:
        return "invalid_embedding", capture_delta, None
    if distance > MAX_COSINE_DISTANCE + 1e-9:
        return "outside_visual_window", capture_delta, distance
    deltas = _metric_deltas(first, second)
    exposure_delta = deltas.get("exposure_value")
    if exposure_delta is not None and exposure_delta >= BRACKET_EXPOSURE_VALUE_DELTA:
        return "likely_exposure_bracket", capture_delta, distance
    if exposure_delta is None and deltas.get("exp_luminance_mean", 0.0) >= 0.15:
        return "likely_exposure_bracket", capture_delta, distance
    return None, capture_delta, distance


def _stable_group_id(operation_id: str, photo_ids: Iterable[str]) -> str:
    encoded = json.dumps(
        {
            "schema": GROUPING_SCHEMA_VERSION,
            "operation_id": operation_id,
            "photo_ids": sorted(photo_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "edit-burst:" + hashlib.sha256(encoded).hexdigest()[:20]


def _medoid(indices: list[int], evidence: list[BurstEvidence]) -> int:
    # Groups are split to MAX_GROUP_SIZE before this bounded O(k^2) step.
    return min(
        indices,
        key=lambda index: (
            sum(
                distance
                if (distance := cosine_distance(evidence[index], evidence[other]))
                is not None
                else 2.0
                for other in indices
                if other != index
            ),
            evidence[index].photo_id,
        ),
    )


def build_candidate_groups(
    evidence: list[BurstEvidence], operation_id: str
) -> tuple[list[list[int]], dict[str, Any]]:
    """Build bounded transitive components using temporal-neighbor comparisons."""
    if not coherence_enabled() or len(evidence) < 2:
        return [], {
            "candidate_count": 0,
            "accepted_group_count": 0,
            "group_sizes": [],
            "distance_distribution": {},
            "rejection_reasons": {"service_kill_switch": len(evidence)},
        }
    parents = list(range(len(evidence)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        left, right = find(first), find(second)
        if left != right:
            parents[max(left, right)] = min(left, right)

    timed = sorted(
        (float(item.capture_time), index)
        for index, item in enumerate(evidence)
        if item.capture_time is not None and math.isfinite(float(item.capture_time))
    )
    candidate_count = 0
    accepted_distances: list[float] = []
    rejection_reasons: dict[str, int] = {}
    for position, (first_time, first_index) in enumerate(timed):
        for second_time, second_index in timed[position + 1 :]:
            if second_time - first_time > MAX_CAPTURE_DELTA_SECONDS:
                break
            candidate_count += 1
            reason, _delta, distance = _candidate_rejection(
                evidence[first_index], evidence[second_index]
            )
            if reason is None:
                union(first_index, second_index)
                accepted_distances.append(float(distance or 0.0))
            else:
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    components: dict[int, list[int]] = {}
    for index in range(len(evidence)):
        components.setdefault(find(index), []).append(index)
    groups: list[list[int]] = []
    for indices in components.values():
        if len(indices) < 2:
            continue
        ordered = sorted(
            indices,
            key=lambda index: (
                float(evidence[index].capture_time or 0.0),
                evidence[index].photo_id,
            ),
        )
        for offset in range(0, len(ordered), MAX_GROUP_SIZE):
            chunk = ordered[offset : offset + MAX_GROUP_SIZE]
            if len(chunk) >= 2:
                groups.append(chunk)
    groups.sort(key=lambda group: min(evidence[index].photo_id for index in group))
    diagnostics = {
        "candidate_count": candidate_count,
        "accepted_group_count": len(groups),
        "group_sizes": [len(group) for group in groups],
        "distance_distribution": {
            "minimum": min(accepted_distances) if accepted_distances else None,
            "maximum": max(accepted_distances) if accepted_distances else None,
            "mean": (
                round(sum(accepted_distances) / len(accepted_distances), 6)
                if accepted_distances
                else None
            ),
        },
        "rejection_reasons": rejection_reasons,
    }
    return groups, diagnostics


def decide_reuse_tiers(
    evidence: list[BurstEvidence], operation_id: str
) -> tuple[dict[str, BurstDecision], dict[str, Any]]:
    """Assign a monotonic tier after every photo has independent policy evidence."""
    decisions = {
        item.photo_id: BurstDecision(photo_id=item.photo_id) for item in evidence
    }
    groups, diagnostics = build_candidate_groups(evidence, operation_id)
    tier_counts = {
        "independent": len(evidence),
        "policy_coherent": 0,
        "global_target_reuse": 0,
    }
    for group in groups:
        representative_index = _medoid(group, evidence)
        representative = evidence[representative_index]
        group_id = _stable_group_id(
            operation_id, (evidence[index].photo_id for index in group)
        )
        for index in group:
            member = evidence[index]
            distance = cosine_distance(representative, member)
            capture_delta = (
                abs(float(representative.capture_time) - float(member.capture_time))
                if representative.capture_time is not None
                and member.capture_time is not None
                else None
            )
            metric_deltas = _metric_deltas(representative, member)
            agreement = {
                "representative_policy_id": representative.policy_id,
                "member_policy_id": member.policy_id,
                "same_policy": bool(
                    representative.policy_id
                    and representative.policy_id == member.policy_id
                ),
                "same_partition": bool(
                    representative.hard_partition_key
                    and representative.hard_partition_key == member.hard_partition_key
                ),
                "member_confidence": member.confidence,
                "member_entropy": member.entropy,
            }
            tier = "independent"
            reason = "representative_anchor" if index == representative_index else None
            moderate = (
                index != representative_index
                and distance is not None
                and distance <= POLICY_COHERENT_MAX_DISTANCE
                and capture_delta is not None
                and capture_delta <= POLICY_COHERENT_MAX_SECONDS
                and agreement["same_policy"]
                and agreement["same_partition"]
                and _finite(member.confidence) is not None
                and float(member.confidence or 0.0) >= 0.40
                and _finite(member.entropy) is not None
                and float(member.entropy or 0.0) <= 0.70
            )
            if moderate:
                tier = "policy_coherent"
                reason = None
                luminance_delta = max(
                    (metric_deltas.get(key, math.inf) for key in _LUMINANCE_METRICS),
                    default=math.inf,
                )
                color_delta = max(
                    (metric_deltas.get(key, math.inf) for key in _COLOR_METRICS),
                    default=math.inf,
                )
                exposure_delta = metric_deltas.get("exposure_value", 0.0)
                strict = (
                    exact_reuse_enabled()
                    and member.source_provenance == RAW_PREVIEW_PROVENANCE
                    and representative.source_provenance == RAW_PREVIEW_PROVENANCE
                    and distance <= GLOBAL_REUSE_MAX_DISTANCE
                    and capture_delta <= GLOBAL_REUSE_MAX_SECONDS
                    and luminance_delta <= GLOBAL_REUSE_MAX_LUMINANCE_DELTA
                    and color_delta <= GLOBAL_REUSE_MAX_COLOR_DELTA
                    and exposure_delta <= GLOBAL_REUSE_MAX_EXPOSURE_VALUE_DELTA
                )
                if strict:
                    tier = "global_target_reuse"
                elif not exact_reuse_enabled():
                    reason = "exact_reuse_release_gate_disabled"
                else:
                    reason = "strict_reuse_gate_failed"
            elif index != representative_index:
                if not agreement["same_partition"]:
                    reason = "rendering_partition_mismatch"
                elif not agreement["same_policy"]:
                    reason = "policy_mismatch_or_ambiguity"
                elif distance is not None and distance > POLICY_COHERENT_MAX_DISTANCE:
                    reason = "moderate_visual_gate_failed"
                elif (
                    capture_delta is not None
                    and capture_delta > POLICY_COHERENT_MAX_SECONDS
                ):
                    reason = "moderate_temporal_gate_failed"
                else:
                    reason = "moderate_admission_failed"
            tier_counts["independent"] -= 1 if tier != "independent" else 0
            tier_counts[tier] += 1 if tier != "independent" else 0
            decisions[member.photo_id] = BurstDecision(
                photo_id=member.photo_id,
                tier=tier,
                fallback_reason=reason,
                group_id=group_id,
                representative_photo_id=representative.photo_id,
                group_size=len(group),
                capture_delta_seconds=(
                    round(capture_delta, 6) if capture_delta is not None else None
                ),
                cosine_distance=(round(distance, 6) if distance is not None else None),
                source_metric_deltas=metric_deltas,
                policy_agreement=agreement,
            )
    diagnostics["tier_counts"] = tier_counts
    diagnostics["grouping_schema_version"] = GROUPING_SCHEMA_VERSION
    diagnostics["reuse_policy_version"] = REUSE_POLICY_VERSION
    diagnostics["threshold_version"] = THRESHOLD_VERSION
    return decisions, diagnostics


def representative_first_order(
    evidence: list[BurstEvidence], operation_id: str
) -> list[int]:
    """Return deterministic candidate medoids first without dropping source order."""
    groups, _diagnostics = build_candidate_groups(evidence, operation_id)
    representatives = [_medoid(group, evidence) for group in groups]
    representative_set = set(representatives)
    return [
        *representatives,
        *(index for index in range(len(evidence)) if index not in representative_set),
    ]


def merge_global_target(
    representative_target: dict[str, Any], member_target: dict[str, Any]
) -> dict[str, Any]:
    """Copy only the versioned exact-reuse allowlist into a member target."""
    merged = dict(member_target)
    for key in GLOBAL_TARGET_REUSE_ALLOWLIST:
        if key in representative_target:
            merged[key] = representative_target[key]
    return merged
