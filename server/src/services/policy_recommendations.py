"""High-precision recommendation ranking for editing-policy v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np


@dataclass(frozen=True)
class PolicyCandidate:
    photo_id: str
    embedding: np.ndarray
    metadata: dict[str, Any]
    responsibilities: np.ndarray
    assignment_entropy: float
    coverage_gain: float = 0.0
    hard_partition_key: str = "default"
    source_ambiguous: bool = False


@dataclass(frozen=True)
class RankedPolicyCandidate:
    photo_id: str
    score: float
    membership_confidence: float
    membership_margin: float
    quality_score: float
    coverage_gain: float
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["reasons"] = list(self.reasons)
        return result


@dataclass(frozen=True)
class RecommendationDiagnostics:
    considered_count: int
    admitted_count: int
    ambiguous_count: int
    partition_rejected_count: int
    duplicate_rejected_count: int
    quality_rejected_count: int
    burst_suppressed_count: int
    diversity_suppressed_count: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievedPolicyNeighbor:
    photo_id: str
    metadata: dict[str, Any]
    cosine_distance: float
    anchor_hits: int


def build_policy_recommendation_payload(
    *,
    policy_id: str,
    policy_name: str,
    camera_profile: str,
    current_count: int,
    needed_count: int,
    ranked_candidates: list[RankedPolicyCandidate],
    diagnostics: RecommendationDiagnostics,
    policy_descriptors: list[dict[str, Any]] | None = None,
    photo_identities: dict[str, dict[str, str]] | None = None,
    training_status: str = "Conditional editing policy",
    estimator_name: str | None = None,
    local_correction_enabled: bool = False,
) -> dict[str, Any]:
    """Build the stable backend-to-Lightroom v2 recommendation contract."""
    identities = photo_identities or {}
    recommended_photos = []
    for candidate in ranked_candidates:
        identity = identities.get(candidate.photo_id, {})
        recommended_photos.append(
            {
                "globalPhotoId": candidate.photo_id,
                "lr_uuid": identity.get("lr_uuid") or identity.get("uuid") or "",
                "score": candidate.score,
                "membership_confidence": candidate.membership_confidence,
                "coverage_gain": candidate.coverage_gain,
                "reasons": list(candidate.reasons),
            }
        )
    rejected_count = (
        diagnostics.partition_rejected_count
        + diagnostics.duplicate_rejected_count
        + diagnostics.quality_rejected_count
    )
    coverage_focused_count = sum(
        candidate.coverage_gain >= 0.5 for candidate in ranked_candidates
    )
    return {
        "recommendation_version": "policy-v2",
        "policy_id": policy_id,
        "policy_name": policy_name,
        # Keep style aliases during the backward-compatible Lightroom rollout.
        "style_id": policy_id,
        "style_name": policy_name,
        "camera_profile": camera_profile or "Default",
        "current_count": int(current_count),
        "needed_count": max(0, int(needed_count)),
        "training_status": training_status,
        "estimator_name": estimator_name or "",
        "local_correction_enabled": bool(local_correction_enabled),
        "target_summary": (
            f"Additional examples requested: {max(0, int(needed_count))}"
        ),
        "policy_descriptors": list(policy_descriptors or []),
        "recommended_photo_ids": recommended_photos,
        "admitted_candidate_count": diagnostics.admitted_count,
        "ambiguous_candidate_count": diagnostics.ambiguous_count,
        "rejected_candidate_count": rejected_count,
        "burst_suppressed_count": diagnostics.burst_suppressed_count,
        "diversity_suppressed_count": diagnostics.diversity_suppressed_count,
        "coverage_focused_count": coverage_focused_count,
        "coverage_summary": (
            f"{coverage_focused_count} recommendations target "
            "underrepresented conditions"
        ),
    }


def retrieve_policy_neighbors(
    collection: Any,
    anchor_embeddings: list[np.ndarray],
    *,
    existing_photo_ids: set[str] | None = None,
    results_per_anchor: int = 300,
    maximum_anchors: int = 6,
    maximum_candidates: int = 1200,
) -> list[RetrievedPolicyNeighbor]:
    """Retrieve bounded multi-medoid neighborhoods in one Chroma query."""
    return retrieve_policy_neighbor_sets(
        collection,
        [anchor_embeddings],
        existing_photo_ids=existing_photo_ids,
        results_per_anchor=results_per_anchor,
        maximum_anchors=maximum_anchors,
        maximum_candidates=maximum_candidates,
    )[0]


def retrieve_policy_neighbor_sets(
    collection: Any,
    policy_anchor_embeddings: list[list[np.ndarray]],
    *,
    existing_photo_ids: set[str] | None = None,
    results_per_anchor: int = 300,
    maximum_anchors: int = 6,
    maximum_candidates: int = 1200,
    include_metadata: bool = True,
) -> list[list[RetrievedPolicyNeighbor]]:
    """Retrieve all policy neighborhoods through one bounded Chroma query."""
    if results_per_anchor <= 0 or maximum_anchors <= 0 or maximum_candidates <= 0:
        raise ValueError("retrieval limits must be positive")
    if not policy_anchor_embeddings:
        return []
    if collection is None:
        return [[] for _ in policy_anchor_embeddings]
    anchors: list[np.ndarray] = []
    anchor_policy_indices: list[int] = []
    for policy_index, policy_anchors in enumerate(policy_anchor_embeddings):
        for value in policy_anchors[:maximum_anchors]:
            anchors.append(_normalized_embedding(value))
            anchor_policy_indices.append(policy_index)
    if not anchors:
        return [[] for _ in policy_anchor_embeddings]
    dimensions = {len(value) for value in anchors}
    if len(dimensions) != 1:
        raise ValueError("all retrieval anchors must have the same dimension")
    available_count = collection.count() if hasattr(collection, "count") else None
    bounded_results = (
        min(results_per_anchor, int(available_count))
        if available_count is not None
        else results_per_anchor
    )
    if bounded_results <= 0:
        return [[] for _ in policy_anchor_embeddings]
    response = collection.query(
        query_embeddings=[value.tolist() for value in anchors],
        n_results=bounded_results,
        include=["metadatas", "distances"] if include_metadata else ["distances"],
    )
    nested_ids = response.get("ids") or []
    nested_metadata = response.get("metadatas") or []
    nested_distances = response.get("distances") or []
    existing = existing_photo_ids or set()
    merged_by_policy: list[dict[str, tuple[dict[str, Any], float, int]]] = [
        {} for _ in policy_anchor_embeddings
    ]
    for anchor_index, ids in enumerate(nested_ids):
        if anchor_index >= len(anchor_policy_indices):
            break
        merged = merged_by_policy[anchor_policy_indices[anchor_index]]
        metadata_rows = (
            nested_metadata[anchor_index] if anchor_index < len(nested_metadata) else []
        )
        distance_rows = (
            nested_distances[anchor_index]
            if anchor_index < len(nested_distances)
            else []
        )
        for row_index, photo_id in enumerate(ids):
            if not photo_id or photo_id in existing:
                continue
            metadata = (
                dict(metadata_rows[row_index])
                if row_index < len(metadata_rows) and metadata_rows[row_index]
                else {}
            )
            try:
                distance = float(distance_rows[row_index])
            except (IndexError, TypeError, ValueError):
                distance = float("inf")
            if not math.isfinite(distance):
                continue
            previous = merged.get(str(photo_id))
            if previous is None:
                merged[str(photo_id)] = (metadata, distance, 1)
            else:
                merged[str(photo_id)] = (
                    previous[0] if previous[0] else metadata,
                    min(previous[1], distance),
                    previous[2] + 1,
                )
    result = []
    for merged in merged_by_policy:
        ranked = sorted(
            (
                RetrievedPolicyNeighbor(
                    photo_id=photo_id,
                    metadata=values[0],
                    cosine_distance=values[1],
                    anchor_hits=values[2],
                )
                for photo_id, values in merged.items()
            ),
            key=lambda item: (
                item.cosine_distance,
                -item.anchor_hits,
                item.photo_id,
            ),
        )
        result.append(ranked[:maximum_candidates])
    return result


def _normalized_embedding(value: np.ndarray) -> np.ndarray:
    embedding = np.asarray(value, dtype=np.float64).reshape(-1)
    if not len(embedding) or not np.all(np.isfinite(embedding)):
        raise ValueError("candidate embedding must be finite and non-empty")
    norm = float(np.linalg.norm(embedding))
    if norm <= 0:
        raise ValueError("candidate embedding must have positive norm")
    return embedding / norm


def _normalized_embedding_matrix(
    values: list[np.ndarray] | np.ndarray | None,
) -> np.ndarray:
    if values is None:
        return np.empty((0, 0), dtype=np.float32)
    # This matrix is used only for cosine duplicate screening. Float32 retains
    # ample precision while halving the largest recommendation artifact and
    # its matrix-multiply bandwidth on unified-memory Macs.
    matrix = np.asarray(values, dtype=np.float32)
    if matrix.size == 0:
        return np.empty((0, 0), dtype=np.float32)
    if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
        raise ValueError("existing embeddings must be a finite 2D matrix")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms <= 0):
        raise ValueError("existing embeddings must have positive norms")
    if np.allclose(norms, 1.0, rtol=1e-7, atol=1e-9):
        return matrix
    return matrix / norms[:, np.newaxis]


def _duplicate_mask(
    existing: np.ndarray,
    candidates: list[np.ndarray],
    *,
    maximum_cosine_distance: float,
    maximum_working_bytes: int = 16 * 1024 * 1024,
) -> np.ndarray:
    if not candidates:
        return np.zeros(0, dtype=bool)
    if not len(existing):
        return np.zeros(len(candidates), dtype=bool)
    # Bound the temporary existing_count × block_size similarity matrix while
    # still feeding matrix batches to Accelerate/BLAS on Apple Silicon.
    bytes_per_column = max(1, len(existing) * existing.dtype.itemsize)
    block_size = max(1, min(128, maximum_working_bytes // bytes_per_column))
    result = np.zeros(len(candidates), dtype=bool)
    threshold = 1.0 - maximum_cosine_distance
    for offset in range(0, len(candidates), block_size):
        block = np.asarray(
            candidates[offset : offset + block_size],
            dtype=existing.dtype,
        )
        maximum_similarity = np.max(existing @ block.T, axis=0)
        result[offset : offset + len(block)] = maximum_similarity >= threshold
    return result


def _capture_time(metadata: dict[str, Any]) -> float | None:
    raw_value = metadata.get("capture_time") or metadata.get("dateTimeOriginal")
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _quality_score(metadata: dict[str, Any]) -> float:
    """Return a bounded preference score; only explicit rejects are excluded."""
    try:
        rating = max(0, min(5, int(metadata.get("rating", 0) or 0)))
    except (TypeError, ValueError):
        rating = 0
    try:
        pick_status = int(metadata.get("pick_status", 0) or 0)
    except (TypeError, ValueError):
        pick_status = 0
    score = 0.55 * (rating / 5.0)
    if pick_status == 1:
        score += 0.25
    if metadata.get("is_edited") or metadata.get("has_develop_settings"):
        score += 0.20
    return min(1.0, score)


def _membership_values(
    responsibilities: np.ndarray,
    policy_index: int,
) -> tuple[float, float]:
    values = np.asarray(responsibilities, dtype=np.float64).reshape(-1)
    if (
        not len(values)
        or policy_index < 0
        or policy_index >= len(values)
        or not np.all(np.isfinite(values))
        or np.any(values < 0)
        or float(np.sum(values)) <= 0
    ):
        raise ValueError("candidate responsibilities are invalid")
    values = values / np.sum(values)
    ordered = np.sort(values)
    confidence = float(values[policy_index])
    competing = float(ordered[-2]) if len(values) > 1 else 0.0
    return confidence, confidence - competing


def _same_burst(
    first: tuple[PolicyCandidate, np.ndarray],
    second: tuple[PolicyCandidate, np.ndarray],
    *,
    maximum_seconds: float,
    maximum_cosine_distance: float,
) -> bool:
    first_candidate, first_embedding = first
    second_candidate, second_embedding = second
    first_group = first_candidate.metadata.get("burst_group_id")
    second_group = second_candidate.metadata.get("burst_group_id")
    if first_group and second_group:
        return str(first_group) == str(second_group)
    first_time = _capture_time(first_candidate.metadata)
    second_time = _capture_time(second_candidate.metadata)
    if first_time is None or second_time is None:
        return False
    return (
        abs(first_time - second_time) <= maximum_seconds
        and 1.0 - float(first_embedding @ second_embedding) <= maximum_cosine_distance
    )


def rank_policy_candidates(
    candidates: list[PolicyCandidate],
    *,
    policy_index: int,
    target_count: int,
    existing_embeddings: list[np.ndarray] | np.ndarray | None = None,
    hard_partition_key: str = "default",
    minimum_confidence: float = 0.60,
    minimum_margin: float = 0.15,
    maximum_entropy: float = 0.80,
    burst_seconds: float = 10.0,
    burst_cosine_distance: float = 0.05,
    duplicate_cosine_distance: float = 0.05,
    selected_similarity_ceiling: float = 0.90,
    membership_weight: float = 0.65,
    coverage_weight: float = 0.20,
    quality_weight: float = 0.15,
) -> tuple[list[RankedPolicyCandidate], RecommendationDiagnostics]:
    """Admit by policy precision, then rank by quality and coverage.

    Coverage and hero quality never compensate for ambiguous membership,
    incompatible hard partitions, explicit rejects, or training duplicates.
    """
    if target_count < 0:
        raise ValueError("target_count must be non-negative")
    ranking_weights = np.asarray(
        [membership_weight, coverage_weight, quality_weight],
        dtype=np.float64,
    )
    if (
        not np.all(np.isfinite(ranking_weights))
        or np.any(ranking_weights < 0)
        or float(np.sum(ranking_weights)) <= 0
    ):
        raise ValueError("ranking weights must be finite, non-negative, and non-zero")
    ranking_weights /= np.sum(ranking_weights)
    existing_matrix = _normalized_embedding_matrix(existing_embeddings)
    preduplicate: list[tuple[PolicyCandidate, np.ndarray, float, float, float]] = []
    embedding_dimension: int | None = (
        existing_matrix.shape[1] if len(existing_matrix) else None
    )
    counts = {
        "ambiguous": 0,
        "partition": 0,
        "duplicate": 0,
        "quality": 0,
        "burst": 0,
        "diversity": 0,
    }

    for candidate in candidates:
        embedding = _normalized_embedding(candidate.embedding)
        if embedding_dimension is None:
            embedding_dimension = len(embedding)
        elif len(embedding) != embedding_dimension:
            raise ValueError("candidate and existing embedding dimensions differ")
        confidence, margin = _membership_values(
            candidate.responsibilities,
            policy_index,
        )
        top_policy = int(np.argmax(candidate.responsibilities))
        if (
            candidate.source_ambiguous
            or top_policy != policy_index
            or confidence < minimum_confidence
            or margin < minimum_margin
            or not math.isfinite(candidate.assignment_entropy)
            or candidate.assignment_entropy > maximum_entropy
        ):
            counts["ambiguous"] += 1
            continue
        if (
            hard_partition_key != "default"
            and candidate.hard_partition_key != hard_partition_key
        ):
            counts["partition"] += 1
            continue
        try:
            pick_status = int(candidate.metadata.get("pick_status", 0) or 0)
        except (TypeError, ValueError):
            pick_status = 0
        if pick_status == -1:
            counts["quality"] += 1
            continue
        coverage_gain = float(candidate.coverage_gain)
        if not math.isfinite(coverage_gain):
            raise ValueError("candidate coverage gain must be finite")
        coverage_gain = min(1.0, max(0.0, coverage_gain))
        preduplicate.append(
            (
                candidate,
                embedding,
                confidence,
                margin,
                _quality_score(candidate.metadata),
            )
        )

    duplicate_mask = _duplicate_mask(
        existing_matrix,
        [item[1] for item in preduplicate],
        maximum_cosine_distance=duplicate_cosine_distance,
    )
    admitted = [
        item for index, item in enumerate(preduplicate) if not duplicate_mask[index]
    ]
    counts["duplicate"] = int(np.sum(duplicate_mask))

    # Burst representatives are chosen before ranking, using deterministic
    # union-find so input ordering cannot change cluster membership.
    parents = list(range(len(admitted)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first_index: int, second_index: int) -> None:
        first_root = find(first_index)
        second_root = find(second_index)
        if first_root != second_root:
            parents[max(first_root, second_root)] = min(first_root, second_root)

    explicit_groups: dict[str, list[int]] = {}
    timed_indices: list[tuple[float, int]] = []
    for index, (candidate, _, _, _, _) in enumerate(admitted):
        burst_group = candidate.metadata.get("burst_group_id")
        if burst_group:
            explicit_groups.setdefault(str(burst_group), []).append(index)
        capture_time = _capture_time(candidate.metadata)
        if capture_time is not None:
            timed_indices.append((capture_time, index))
    for indices in explicit_groups.values():
        for index in indices[1:]:
            union(indices[0], index)

    # Only compare temporal neighbors inside the burst window.  This avoids an
    # unbounded full candidate-by-candidate matrix as catalogs grow.
    timed_indices.sort()
    for first_position, (first_time, first_index) in enumerate(timed_indices):
        second_position = first_position + 1
        while second_position < len(timed_indices):
            second_time, second_index = timed_indices[second_position]
            if second_time - first_time > burst_seconds:
                break
            if _same_burst(
                (admitted[first_index][0], admitted[first_index][1]),
                (admitted[second_index][0], admitted[second_index][1]),
                maximum_seconds=burst_seconds,
                maximum_cosine_distance=burst_cosine_distance,
            ):
                union(first_index, second_index)
            second_position += 1

    clusters: dict[int, list[int]] = {}
    for index in range(len(admitted)):
        clusters.setdefault(find(index), []).append(index)
    survivors = []
    for cluster_indices in clusters.values():
        best_index = max(
            cluster_indices,
            key=lambda index: (
                admitted[index][4],
                admitted[index][2],
                float(admitted[index][0].coverage_gain),
                admitted[index][0].photo_id,
            ),
        )
        survivors.append(admitted[best_index])
        counts["burst"] += len(cluster_indices) - 1

    base_ranked = []
    for candidate, embedding, confidence, margin, quality in survivors:
        coverage = min(1.0, max(0.0, float(candidate.coverage_gain)))
        score = (
            ranking_weights[0] * confidence
            + ranking_weights[1] * coverage
            + ranking_weights[2] * quality
        )
        reasons = ["high_policy_membership"]
        if coverage >= 0.5:
            reasons.append("fills_coverage_gap")
        if quality >= 0.5:
            reasons.append("strong_user_quality_signal")
        base_ranked.append(
            (
                score,
                candidate.photo_id,
                embedding,
                RankedPolicyCandidate(
                    photo_id=candidate.photo_id,
                    score=score,
                    membership_confidence=confidence,
                    membership_margin=margin,
                    quality_score=quality,
                    coverage_gain=coverage,
                    reasons=tuple(reasons),
                ),
            )
        )
    base_ranked.sort(key=lambda item: (-item[0], item[1]))

    selected: list[RankedPolicyCandidate] = []
    selected_embeddings: list[np.ndarray] = []
    for _, _, embedding, result in base_ranked:
        if len(selected) >= target_count:
            break
        if (
            selected_embeddings
            and max(
                float(embedding @ selected_embedding)
                for selected_embedding in selected_embeddings
            )
            > selected_similarity_ceiling
        ):
            counts["diversity"] += 1
            continue
        selected.append(result)
        selected_embeddings.append(embedding)

    diagnostics = RecommendationDiagnostics(
        considered_count=len(candidates),
        admitted_count=len(admitted),
        ambiguous_count=counts["ambiguous"],
        partition_rejected_count=counts["partition"],
        duplicate_rejected_count=counts["duplicate"],
        quality_rejected_count=counts["quality"],
        burst_suppressed_count=counts["burst"],
        diversity_suppressed_count=counts["diversity"],
    )
    return selected, diagnostics
