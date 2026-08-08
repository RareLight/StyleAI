"""Transactional training and inference runtime for editing-policy v2."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import shutil
import threading
from time import perf_counter, time
from typing import Any, Callable
from uuid import uuid4

import joblib
import numpy as np
from sklearn.cluster import KMeans
from sklearn.model_selection import GroupKFold

import config
from config import logger
from . import policy_store
from . import training as training_service
from .policy_calibration import HierarchicalPolicyRegressor
from .policy_discovery import PolicyMixture
from .policy_features import (
    FEATURE_SCHEMA_VERSION,
    build_source_feature_vector,
)
from .policy_insights import (
    DescriptorObservation,
    PolicyCoverageDiagnostics,
    discover_open_vocabulary_descriptors,
)
from .policy_local import LocalResidualCorrector
from .policy_models import EstimatorFactory, default_estimator_factories
from .photo_constraints import is_stitched_panorama
from .policy_recommendations import (
    PolicyCandidate,
    build_policy_recommendation_payload,
    rank_policy_candidates,
    retrieve_policy_neighbor_sets,
)
from .policy_targets import (
    TARGET_SCHEMA_VERSION,
    AbsoluteTarget,
    default_flat_target_value,
    flatten_absolute_target,
    interpolate_absolute_target,
    unflatten_absolute_target,
)
from .rendering_state import (
    RenderingSelectorArtifact,
    fit_rendering_selector,
    rendering_partition_key,
    rendering_state_from_metadata,
)


POLICY_ALGORITHM_VERSION = "editing-policy-v2.5"
MIN_PARTITION_EXAMPLES = 12
MAX_POLICIES_PER_PARTITION = 4
# Policy-count validation is a guard against unnecessary expert proliferation,
# not the final fit.  A small number of EM iterations is sufficient to reject
# weak extra components and avoids multiplying full model fits on every fold.
MAX_CV_MIXTURE_ITERATIONS = 6
MAX_FINAL_MIXTURE_ITERATIONS = 12
MAX_DISCOVERY_VALIDATION_EXAMPLES = 600
MAX_LOCAL_VALIDATION_EXAMPLES = 2048
# Coordinate-descent Elastic Net is not a viable production candidate when a
# small Lightroom partition is paired with the full SigLIP feature vector.  In
# that p >> n regime it is both poorly conditioned and dramatically slower than
# the ridge/PLS candidates.  Keep it in the estimator bake-off for adequately
# supported low-dimensional data instead of letting it monopolize discovery.
MAX_ELASTIC_NET_FEATURES = 512
MODEL_DIRECTORY_NAME = "policy_v2_models"

_PROFILE_VERSION_SUFFIX = re.compile(r"\s*\(v\d+\)\s*$", re.IGNORECASE)
_runtime_lock = threading.RLock()
_cached_generation_id: str | None = None
_cached_artifacts: dict[str, "PartitionPolicyArtifact"] = {}
_cached_custom_names: dict[str, str] | None = None
_cached_rendering_selector: RenderingSelectorArtifact | None = None
_rebuild_lock = threading.Lock()
_rebuild_requested = 0
_rebuild_worker: threading.Thread | None = None
_rebuild_status: dict[str, Any] = {
    "status": "idle",
    "phase": "idle",
    "requested_at": None,
    "started_at": None,
    "completed_at": None,
    "generation": None,
    "error": None,
    "eligible_partitions": 0,
    "completed_partitions": 0,
}


@dataclass
class PartitionPolicyArtifact:
    generation_id: str
    partition_key: str
    camera_profile: str
    feature_names: tuple[str, ...]
    target_keys: tuple[str, ...]
    policy_ids: tuple[str, ...]
    policy_names: tuple[str, ...]
    mixture: PolicyMixture
    calibrators: list[HierarchicalPolicyRegressor]
    local_correctors: list[LocalResidualCorrector | None]
    slider_bounds: list[dict[str, tuple[float, float]]]
    coverage: PolicyCoverageDiagnostics
    descriptors: list[list[dict[str, Any]]]
    image_anchors: list[list[np.ndarray]]
    example_embeddings: list[np.ndarray]
    example_photo_ids: list[list[str]]
    example_count: int
    estimator_name: str
    validation: dict[str, Any]


@dataclass(frozen=True)
class PolicyPrediction:
    generation_id: str
    policy_id: str
    policy_name: str
    hard_partition_key: str
    confidence: float
    entropy: float
    target: dict[str, Any]
    applied: dict[str, Any]
    example_count: int
    rendering_intent: dict[str, Any]


@dataclass(frozen=True)
class PartitionArtifactPrediction:
    policy_index: int
    confidence: float
    entropy: float
    flat_target: dict[str, float]


def _database_path() -> str:
    if not config.DB_PATH:
        raise RuntimeError("StyleAI database path is not configured")
    return config.DB_PATH


def _normalized_profile(value: Any) -> str:
    profile = _PROFILE_VERSION_SUFFIX.sub("", str(value or "Default").strip())
    return profile or "Default"


def hard_partition_key(metadata: dict[str, Any]) -> str:
    if metadata.get("rendering_state") or metadata.get("rendering_state_json"):
        return rendering_partition_key(rendering_state_from_metadata(metadata))
    profile = _normalized_profile(metadata.get("camera_profile"))
    is_hdr = bool(metadata.get("is_hdr")) or "hdr" in profile.casefold()
    return f"{'hdr' if is_hdr else 'sdr'}|{profile.casefold()}"


def _categories(metadata: dict[str, Any]) -> dict[str, str]:
    state = rendering_state_from_metadata(metadata)
    profile = state["profile"].get("display_name") or _normalized_profile(
        metadata.get("camera_profile")
    )
    return {
        "hdr_state": ("hdr" if bool(state.get("is_hdr")) else "sdr"),
        "camera_make": str(metadata.get("camera_make") or "unknown"),
        "camera_model": str(metadata.get("camera_model") or "unknown"),
        "camera_profile": profile,
        "lens": str(metadata.get("lens") or "unknown"),
    }


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_descriptor_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _source_row(
    metadata: dict[str, Any],
    embedding: Any,
) -> tuple[np.ndarray, tuple[str, ...]]:
    vector = build_source_feature_vector(
        metadata,
        image_embedding=embedding,
        source_provenance=str(metadata.get("source_provenance") or "raw_preview"),
    )
    optional_availability = tuple(
        (name, available)
        for name, available in zip(vector.names, vector.availability, strict=True)
        if not name.startswith(("image_embedding_", "semantic_embedding_"))
    )
    values = np.asarray(
        (
            *vector.values,
            *(1.0 if available else 0.0 for _, available in optional_availability),
        ),
        dtype=np.float64,
    )
    names = (
        *vector.names,
        *(f"available:{name}" for name, _ in optional_availability),
    )
    return values, tuple(names)


def _training_quality(metadata: dict[str, Any], target: dict[str, float]) -> tuple:
    try:
        rating = int(metadata.get("rating", 0) or 0)
    except (TypeError, ValueError):
        rating = 0
    try:
        pick = int(metadata.get("pick_status", 0) or 0)
    except (TypeError, ValueError):
        pick = 0
    complexity = sum(abs(float(value)) > 1e-6 for value in target.values())
    return rating, pick == 1, complexity


def _capture_time(metadata: dict[str, Any]) -> float | None:
    raw = metadata.get("capture_time") or metadata.get("capture_time_unix")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _curate_bursts(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Keep one deterministic hero per temporal/visual burst."""
    parents = list(range(len(rows)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parents[max(first_root, second_root)] = min(first_root, second_root)

    timed = sorted(
        (
            (capture_time, index)
            for index, row in enumerate(rows)
            if (capture_time := _capture_time(row["metadata"])) is not None
        )
    )
    for position, (first_time, first_index) in enumerate(timed):
        next_position = position + 1
        while next_position < len(timed):
            second_time, second_index = timed[next_position]
            if second_time - first_time > 10.0:
                break
            similarity = float(
                rows[first_index]["normalized_embedding"]
                @ rows[second_index]["normalized_embedding"]
            )
            if 1.0 - similarity <= 0.05:
                union(first_index, second_index)
            next_position += 1

    clusters: dict[int, list[int]] = {}
    for index in range(len(rows)):
        clusters.setdefault(find(index), []).append(index)
    selected: list[dict[str, Any]] = []
    weights: list[float] = []
    for indices in clusters.values():
        hero_index = max(
            indices,
            key=lambda index: (
                _training_quality(
                    rows[index]["metadata"],
                    rows[index]["flat_target"],
                ),
                rows[index]["photo_id"],
            ),
        )
        hero = dict(rows[hero_index])
        hero["burst_group_id"] = "burst:" + min(
            rows[index]["photo_id"] for index in indices
        )
        selected.append(hero)
        weights.append(1.0 / len(indices))
    order = np.argsort([row["photo_id"] for row in selected])
    return (
        [selected[int(index)] for index in order],
        np.asarray([weights[int(index)] for index in order], dtype=np.float64),
    )


def _prepare_rows(raw_examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for item in raw_examples:
        metadata = dict(item.get("metadata") or {})
        embedding = item.get("embedding")
        if embedding is None or is_stitched_panorama(metadata):
            continue
        normalized_embedding = np.asarray(embedding, dtype=np.float64).reshape(-1)
        if (
            not len(normalized_embedding)
            or not np.all(np.isfinite(normalized_embedding))
            or float(np.linalg.norm(normalized_embedding)) <= 0
        ):
            continue
        normalized_embedding /= np.linalg.norm(normalized_embedding)
        canonical = _safe_json_dict(metadata.get("canonical_settings"))
        flat_target = flatten_absolute_target(canonical)
        if not flat_target:
            continue
        source, feature_names = _source_row(metadata, normalized_embedding)
        rendering_state = rendering_state_from_metadata(metadata)
        prepared.append(
            {
                "photo_id": str(item["photo_id"]),
                "metadata": metadata,
                "embedding": normalized_embedding,
                "normalized_embedding": normalized_embedding,
                "source": source,
                "feature_names": feature_names,
                "flat_target": flat_target,
                "canonical_target": canonical,
                "partition_key": hard_partition_key(metadata),
                "categories": _categories(metadata),
                "rendering_state": rendering_state,
            }
        )
    return prepared


def _policy_mixture(
    n_policies: int,
    n_examples: int,
    *,
    expert_factory: EstimatorFactory,
    seed: int,
    max_iterations: int = MAX_FINAL_MIXTURE_ITERATIONS,
) -> PolicyMixture:
    minimum_support = max(3.0, min(8.0, n_examples / max(3 * n_policies, 1)))
    return PolicyMixture(
        n_policies=n_policies,
        expert_factory=expert_factory,
        minimum_effective_samples=minimum_support,
        seed=seed,
        max_iterations=max_iterations,
    )


def _bounded_group_sample(
    groups: np.ndarray,
    *,
    maximum: int = MAX_DISCOVERY_VALIDATION_EXAMPLES,
) -> np.ndarray:
    """Deterministically cap repeated validation without splitting groups."""
    group_array = np.asarray(groups).reshape(-1)
    if len(group_array) <= maximum:
        return np.arange(len(group_array), dtype=np.int64)
    grouped: dict[str, list[int]] = {}
    for index, group in enumerate(group_array):
        grouped.setdefault(str(group), []).append(index)
    ranked_groups = sorted(
        grouped,
        key=lambda group: (
            hashlib.sha256(group.encode("utf-8")).digest(),
            group,
        ),
    )
    selected: list[int] = []
    for group in ranked_groups:
        indices = grouped[group]
        if selected and len(selected) + len(indices) > maximum:
            continue
        selected.extend(indices)
        if len(selected) >= maximum:
            break
    return np.asarray(sorted(selected), dtype=np.int64)


def _cross_validated_estimator(
    source: np.ndarray,
    targets: np.ndarray,
    groups: np.ndarray,
    weights: np.ndarray,
) -> tuple[str, EstimatorFactory, dict[str, Any]]:
    """Select a production expert on burst-safe held-out examples.

    The nonlinear challenger remains available to the offline benchmark but is
    intentionally excluded here: small Lightroom training sets do not provide
    enough evidence to justify its added variance and artifact size.
    """
    full_example_count = len(source)
    validation_indices = _bounded_group_sample(groups)
    source = source[validation_indices]
    targets = targets[validation_indices]
    groups = groups[validation_indices]
    weights = weights[validation_indices]
    candidates = {
        name: factory
        for name, factory in default_estimator_factories().items()
        if name != "random_feature_ridge"
    }
    skipped_estimators: dict[str, str] = {}
    if source.shape[1] > MAX_ELASTIC_NET_FEATURES:
        candidates.pop("multitask_elastic_net", None)
        skipped_estimators["multitask_elastic_net"] = (
            "requires at most "
            f"{MAX_ELASTIC_NET_FEATURES} source features; received {source.shape[1]}"
        )
    unique_groups = np.unique(groups)
    fold_count = min(3, len(unique_groups))
    if fold_count < 2:
        name = "reduced_rank_ridge"
        return (
            name,
            candidates[name],
            {
                "selected_estimator": name,
                "fold_count": 0,
                "candidates": {},
                "skipped_estimators": skipped_estimators,
                "validation_example_count": len(source),
                "full_example_count": full_example_count,
            },
        )

    folds = list(GroupKFold(n_splits=fold_count).split(source, groups=groups))
    scales = np.maximum(np.ptp(targets, axis=0), 1e-6)
    scores: dict[str, dict[str, float | int]] = {}
    for name, factory in candidates.items():
        predictions = np.zeros_like(targets)
        failed = False
        parameter_count = 0
        for train_indices, test_indices in folds:
            try:
                estimator = factory().fit(
                    source[train_indices],
                    targets[train_indices],
                    sample_weight=weights[train_indices],
                )
                predictions[test_indices] = estimator.predict(source[test_indices])
                parameter_count = max(
                    parameter_count,
                    int(getattr(estimator, "parameter_count_", 0)),
                )
            except (ValueError, np.linalg.LinAlgError):
                failed = True
                break
        if failed:
            continue
        normalized_rmse = float(
            np.sqrt(np.mean(np.square((predictions - targets) / scales)))
        )
        scores[name] = {
            "normalized_rmse": normalized_rmse,
            "parameter_count": parameter_count,
        }
    if not scores:
        name = "reduced_rank_ridge"
    else:
        name = min(
            scores,
            key=lambda item: (
                float(scores[item]["normalized_rmse"]),
                int(scores[item]["parameter_count"]),
                item,
            ),
        )
    return (
        name,
        candidates[name],
        {
            "selected_estimator": name,
            "fold_count": fold_count,
            "candidates": scores,
            "skipped_estimators": skipped_estimators,
            "validation_example_count": len(source),
            "full_example_count": full_example_count,
        },
    )


def _cross_fitted_residual_labels(
    source: np.ndarray,
    targets: np.ndarray,
    groups: np.ndarray,
    weights: np.ndarray,
    *,
    n_policies: int,
    expert_factory: EstimatorFactory,
    seed: int,
) -> np.ndarray:
    """Discover target-response candidates without source-dimension dominance."""
    unique_groups = np.unique(groups)
    fold_count = min(3, len(unique_groups))
    predictions = np.zeros_like(targets)
    if fold_count >= 2:
        folds = GroupKFold(n_splits=fold_count).split(source, groups=groups)
        for train_indices, test_indices in folds:
            estimator = expert_factory().fit(
                source[train_indices],
                targets[train_indices],
                sample_weight=weights[train_indices],
            )
            predictions[test_indices] = estimator.predict(source[test_indices])
    else:
        estimator = expert_factory().fit(source, targets, sample_weight=weights)
        predictions = estimator.predict(source)
    lower, upper = np.quantile(targets, (0.1, 0.9), axis=0)
    scales = np.maximum(upper - lower, np.maximum(np.ptp(targets, axis=0) * 0.05, 1e-6))
    residuals = (targets - predictions) / scales
    return KMeans(
        n_clusters=n_policies,
        random_state=seed,
        n_init=10,
    ).fit_predict(residuals)


def _cross_fitted_calibrator_residuals(
    source: np.ndarray,
    targets: np.ndarray,
    categories: list[dict[str, str]],
    groups: np.ndarray,
    weights: np.ndarray,
    evaluation_indices: np.ndarray,
    *,
    base_factory: EstimatorFactory,
) -> np.ndarray:
    """Return honest residuals for examples eligible for one local policy."""
    evaluation_indices = np.asarray(evaluation_indices, dtype=np.int64)
    if not len(evaluation_indices):
        return np.empty((0, targets.shape[1]), dtype=np.float64)
    unique_groups = np.unique(groups)
    fold_count = min(3, len(unique_groups))
    predictions = np.zeros(
        (len(evaluation_indices), targets.shape[1]), dtype=np.float64
    )
    evaluated = np.zeros(len(evaluation_indices), dtype=bool)
    if fold_count < 2:
        raise ValueError("local residual validation requires at least two groups")
    evaluation_positions = {
        int(source_index): output_index
        for output_index, source_index in enumerate(evaluation_indices)
    }
    for train_indices, test_indices in GroupKFold(n_splits=fold_count).split(
        source,
        groups=groups,
    ):
        fold_evaluation = [
            int(index) for index in test_indices if int(index) in evaluation_positions
        ]
        if not fold_evaluation:
            continue
        calibrator = HierarchicalPolicyRegressor(
            base_factory=base_factory,
        ).fit(
            source[train_indices],
            targets[train_indices],
            categories=[categories[int(index)] for index in train_indices],
            sample_weight=weights[train_indices],
        )
        fold_predictions = calibrator.predict(
            source[fold_evaluation],
            categories=[categories[index] for index in fold_evaluation],
        )
        for source_index, prediction in zip(
            fold_evaluation,
            fold_predictions,
            strict=True,
        ):
            output_index = evaluation_positions[source_index]
            predictions[output_index] = prediction
            evaluated[output_index] = True
    if not np.all(evaluated):
        raise ValueError("cross-fitted local residuals did not cover every example")
    return targets[evaluation_indices] - predictions


def _canonicalize_components(
    mixture: PolicyMixture,
    targets: np.ndarray,
    weights: np.ndarray,
) -> None:
    """Give otherwise permutation-invariant mixture components stable indices."""
    responsibilities = mixture.training_responsibilities_
    signatures = []
    for policy_index in range(mixture.n_policies):
        component_weight = weights * responsibilities[:, policy_index]
        centroid = np.average(targets, axis=0, weights=component_weight)
        signatures.append(
            (
                tuple(np.round(centroid, 8)),
                policy_index,
            )
        )
    order = [item[1] for item in sorted(signatures)]
    if order == list(range(mixture.n_policies)):
        return
    mixture.training_responsibilities_ = responsibilities[:, order]
    mixture.policy_priors_ = mixture.policy_priors_[order]
    mixture.experts_ = [mixture.experts_[index] for index in order]
    mixture.policy_medoids_ = [mixture.policy_medoids_[index] for index in order]
    mixture.policy_distance_scale_ = [
        mixture.policy_distance_scale_[index] for index in order
    ]


def _cross_validated_policy_count(
    source: np.ndarray,
    targets: np.ndarray,
    groups: np.ndarray,
    weights: np.ndarray,
    gate_feature_indices: np.ndarray,
    *,
    expert_factory: EstimatorFactory,
    seed: int,
) -> tuple[int, dict[str, Any]]:
    full_example_count = len(source)
    validation_indices = _bounded_group_sample(groups)
    source = source[validation_indices]
    targets = targets[validation_indices]
    groups = groups[validation_indices]
    weights = weights[validation_indices]
    maximum = min(
        MAX_POLICIES_PER_PARTITION,
        max(1, len(source) // MIN_PARTITION_EXAMPLES),
    )
    unique_groups = np.unique(groups)
    fold_count = min(3, len(unique_groups))
    if maximum == 1 or fold_count < 2:
        return 1, {
            "selected_policy_count": 1,
            "candidates": {},
            "validation_example_count": len(source),
            "full_example_count": full_example_count,
        }
    folds = list(GroupKFold(n_splits=fold_count).split(source, groups=groups))
    scales = np.maximum(np.ptp(targets, axis=0), 1e-6)
    candidate_scores: dict[int, dict[str, float]] = {}
    for policy_count in range(1, maximum + 1):
        predictions = np.zeros_like(targets)
        ambiguous = 0
        failed = False
        for fold_index, (train_indices, test_indices) in enumerate(folds):
            try:
                initial_labels = _cross_fitted_residual_labels(
                    source[train_indices],
                    targets[train_indices],
                    groups[train_indices],
                    weights[train_indices],
                    n_policies=policy_count,
                    expert_factory=expert_factory,
                    seed=seed + fold_index,
                )
                model = _policy_mixture(
                    policy_count,
                    len(train_indices),
                    expert_factory=expert_factory,
                    seed=seed + fold_index,
                    max_iterations=MAX_CV_MIXTURE_ITERATIONS,
                ).fit(
                    source[train_indices],
                    targets[train_indices],
                    sample_weight=weights[train_indices],
                    gate_feature_indices=gate_feature_indices,
                    initial_labels=initial_labels,
                )
                predictions[test_indices] = model.predict(source[test_indices])
                ambiguous += sum(
                    assignment.ambiguous
                    for assignment in model.assignments(source[test_indices])
                )
            except (ValueError, np.linalg.LinAlgError):
                failed = True
                break
        if failed:
            continue
        normalized_rmse = float(
            np.sqrt(np.mean(np.square((predictions - targets) / scales)))
        )
        ambiguity_rate = ambiguous / len(source)
        penalized = normalized_rmse + 0.015 * (policy_count - 1) + 0.05 * ambiguity_rate
        candidate_scores[policy_count] = {
            "normalized_rmse": normalized_rmse,
            "ambiguity_rate": ambiguity_rate,
            "penalized_score": penalized,
        }
        # A more complex mixture is only justified when the immediately
        # simpler split already earns a material grouped held-out gain.  This
        # is both the intended anti-proliferation rule and avoids evaluating a
        # combinatorial tail of weak components on large catalogs.
        if policy_count == 2:
            baseline = candidate_scores.get(1, {}).get("penalized_score")
            if baseline is None or penalized >= baseline * 0.95:
                break
    selected = 1
    best_score = candidate_scores.get(1, {}).get("penalized_score", float("inf"))
    for policy_count in range(2, maximum + 1):
        metrics = candidate_scores.get(policy_count)
        if not metrics or metrics["ambiguity_rate"] > 0.35:
            continue
        # Require material held-out improvement before proliferating experts.
        if metrics["penalized_score"] < best_score * 0.95:
            selected = policy_count
            best_score = metrics["penalized_score"]
    return selected, {
        "selected_policy_count": selected,
        "candidates": candidate_scores,
        "fold_count": fold_count,
        "validation_example_count": len(source),
        "full_example_count": full_example_count,
    }


def _select_image_anchors(
    embeddings: np.ndarray,
    responsibilities: np.ndarray,
    policy_index: int,
    *,
    maximum: int = 6,
) -> tuple[list[np.ndarray], list[int]]:
    indices = np.flatnonzero(np.argmax(responsibilities, axis=1) == policy_index)
    if not len(indices):
        return [], []
    component = embeddings[indices]
    first = int(np.argmax(responsibilities[indices, policy_index]))
    selected_local = [first]
    minimum_distance = 1.0 - component @ component[first]
    while len(selected_local) < min(maximum, len(component)):
        next_local = int(np.argmax(minimum_distance))
        if next_local in selected_local:
            break
        selected_local.append(next_local)
        minimum_distance = np.minimum(
            minimum_distance,
            1.0 - component @ component[next_local],
        )
    selected_indices = [int(indices[index]) for index in selected_local]
    return [embeddings[index] for index in selected_indices], selected_indices


def _descriptor_observations(metadata_rows: list[dict[str, Any]]):
    observations: list[list[DescriptorObservation]] = []
    for metadata in metadata_rows:
        row: list[DescriptorObservation] = []
        for value in _safe_descriptor_values(metadata.get("user_keywords")):
            row.append(DescriptorObservation("user_keyword", value, "user"))
        for key in ("scene_tags", "content_tags", "tags"):
            for value in _safe_descriptor_values(metadata.get(key)):
                row.append(DescriptorObservation("local_visual_tag", value, key))
        observations.append(row)
    return observations


def _partition_slug(partition_key: str) -> str:
    return hashlib.sha256(partition_key.encode("utf-8")).hexdigest()[:12]


def _fit_partition(
    rows: list[dict[str, Any]],
    weights: np.ndarray,
    *,
    generation_id: str,
    seed: int,
) -> PartitionPolicyArtifact:
    feature_names = rows[0]["feature_names"]
    if any(row["feature_names"] != feature_names for row in rows):
        raise ValueError("source feature dimensions differ within a partition")
    source = np.stack([row["source"] for row in rows])
    target_keys = tuple(sorted({key for row in rows for key in row["flat_target"]}))
    targets = np.asarray(
        [
            [
                row["flat_target"].get(key, default_flat_target_value(key))
                for key in target_keys
            ]
            for row in rows
        ],
        dtype=np.float64,
    )
    groups = np.asarray([row.get("burst_group_id") or row["photo_id"] for row in rows])
    gate_feature_indices = np.asarray(
        [
            index
            for index, name in enumerate(feature_names)
            if name.startswith("image_embedding_")
        ],
        dtype=np.int64,
    )
    estimator_name, estimator_factory, estimator_validation = (
        _cross_validated_estimator(
            source,
            targets,
            groups,
            weights,
        )
    )
    policy_count, validation = _cross_validated_policy_count(
        source,
        targets,
        groups,
        weights,
        gate_feature_indices,
        expert_factory=estimator_factory,
        seed=seed,
    )
    validation["estimator_selection"] = estimator_validation
    initial_labels = (
        _cross_fitted_residual_labels(
            source,
            targets,
            groups,
            weights,
            n_policies=policy_count,
            expert_factory=estimator_factory,
            seed=seed,
        )
        if policy_count > 1
        else None
    )
    mixture = _policy_mixture(
        policy_count,
        len(rows),
        expert_factory=estimator_factory,
        seed=seed,
        max_iterations=MAX_FINAL_MIXTURE_ITERATIONS,
    ).fit(
        source,
        targets,
        sample_weight=weights,
        gate_feature_indices=gate_feature_indices,
        initial_labels=initial_labels,
    )
    _canonicalize_components(mixture, targets, weights)
    responsibilities = mixture.training_responsibilities_
    categories = [row["categories"] for row in rows]
    embedding_matrix = np.stack([row["normalized_embedding"] for row in rows])
    hard_labels = np.argmax(responsibilities, axis=1)
    local_fit_indices = _bounded_group_sample(
        groups,
        maximum=MAX_LOCAL_VALIDATION_EXAMPLES,
    )
    calibrators = []
    local_correctors: list[LocalResidualCorrector | None] = []
    local_validation: list[dict[str, Any]] = []
    bounds = []
    policy_ids = []
    for policy_index in range(policy_count):
        component_weights = weights * np.maximum(
            responsibilities[:, policy_index],
            1e-4,
        )
        calibrator = HierarchicalPolicyRegressor(
            base_factory=estimator_factory,
        ).fit(
            source,
            targets,
            categories=categories,
            sample_weight=component_weights,
        )
        calibrators.append(calibrator)
        member_indices = np.flatnonzero(hard_labels == policy_index)
        member_targets = targets[member_indices] if len(member_indices) else targets
        policy_bounds = {
            key: (
                float(np.min(member_targets[:, target_index])),
                float(np.max(member_targets[:, target_index])),
            )
            for target_index, key in enumerate(target_keys)
        }
        bounds.append(policy_bounds)
        local_member_positions = np.flatnonzero(
            hard_labels[local_fit_indices] == policy_index
        )
        local_member_indices = local_fit_indices[local_member_positions]
        if len(local_member_indices) >= 24:
            try:
                local_residuals = _cross_fitted_calibrator_residuals(
                    source[local_fit_indices],
                    targets[local_fit_indices],
                    [categories[int(index)] for index in local_fit_indices],
                    groups[local_fit_indices],
                    component_weights[local_fit_indices],
                    local_member_positions,
                    base_factory=estimator_factory,
                )
                local_member_targets = targets[local_member_indices]
                lower, upper = np.quantile(
                    local_member_targets,
                    (0.1, 0.9),
                    axis=0,
                )
                target_scales = np.maximum(
                    upper - lower,
                    np.maximum(
                        np.ptp(local_member_targets, axis=0) * 0.05,
                        1e-6,
                    ),
                )
                local_corrector, local_diagnostics = (
                    LocalResidualCorrector.fit_validated(
                        embedding_matrix[local_member_indices],
                        local_residuals,
                        groups=groups[local_member_indices],
                        photo_ids=np.asarray(
                            [
                                rows[int(index)]["photo_id"]
                                for index in local_member_indices
                            ]
                        ),
                        sample_weight=component_weights[local_member_indices],
                        target_scales=target_scales,
                    )
                )
                local_diagnostics["full_policy_example_count"] = len(member_indices)
            except (ValueError, np.linalg.LinAlgError):
                logger.warning(
                    "Local residual validation failed for partition %s policy %d",
                    rows[0]["partition_key"],
                    policy_index,
                    exc_info=True,
                )
                local_corrector = None
                local_diagnostics = {
                    "enabled": False,
                    "reason": "validation_failed",
                    "example_count": len(local_member_indices),
                    "full_policy_example_count": len(member_indices),
                }
        else:
            local_corrector = None
            local_diagnostics = {
                "enabled": False,
                "reason": "insufficient_examples",
                "example_count": len(local_member_indices),
                "full_policy_example_count": len(member_indices),
            }
        local_correctors.append(local_corrector)
        local_validation.append(local_diagnostics)
        policy_ids.append(
            f"policy-{_partition_slug(rows[0]['partition_key'])}-{policy_index + 1}"
        )
    validation["local_residual_correction"] = local_validation

    coverage = PolicyCoverageDiagnostics(
        visual_component_count=min(6, max(1, len(rows) // 8)),
    ).fit(
        source[:, gate_feature_indices],
        tuple(feature_names[index] for index in gate_feature_indices),
        responsibilities,
        categories=categories,
        numeric_dimensions=(),
        sample_weight=weights,
    )
    discovered = discover_open_vocabulary_descriptors(
        _descriptor_observations([row["metadata"] for row in rows]),
        responsibilities,
        sample_weight=weights,
        minimum_effective_support=max(1.5, min(3.0, len(rows) / 10.0)),
    )
    descriptors: list[list[dict[str, Any]]] = [[] for _ in range(policy_count)]
    for descriptor in discovered:
        descriptors[descriptor.policy_index].append(
            {
                "descriptor_kind": descriptor.descriptor_kind,
                "descriptor": descriptor.descriptor,
                "score": descriptor.score,
                "provenance": descriptor.provenance,
                "effective_support": descriptor.effective_support,
            }
        )
    policy_names = []
    for policy_index in range(policy_count):
        cues = [item["descriptor"] for item in descriptors[policy_index][:3]]
        policy_names.append(
            " • ".join(cues) if cues else f"Editing Policy {policy_index + 1}"
        )
    image_anchors = []
    example_embeddings = []
    example_photo_ids = []
    for policy_index in range(policy_count):
        anchors, _ = _select_image_anchors(
            embedding_matrix,
            responsibilities,
            policy_index,
        )
        image_anchors.append(anchors)
        example_embeddings.append(
            embedding_matrix[np.flatnonzero(hard_labels == policy_index)].astype(
                np.float32
            )
        )
        example_photo_ids.append(
            [
                rows[index]["photo_id"]
                for index in np.flatnonzero(hard_labels == policy_index)
            ]
        )
    return PartitionPolicyArtifact(
        generation_id=generation_id,
        partition_key=rows[0]["partition_key"],
        camera_profile=_normalized_profile(rows[0]["metadata"].get("camera_profile")),
        feature_names=feature_names,
        target_keys=target_keys,
        policy_ids=tuple(policy_ids),
        policy_names=tuple(policy_names),
        mixture=mixture,
        calibrators=calibrators,
        local_correctors=local_correctors,
        slider_bounds=bounds,
        coverage=coverage,
        descriptors=descriptors,
        image_anchors=image_anchors,
        example_embeddings=example_embeddings,
        example_photo_ids=example_photo_ids,
        example_count=len(rows),
        estimator_name=estimator_name,
        validation=validation,
    )


def _artifact_directory(generation_id: str) -> str:
    return os.path.join(
        _database_path(),
        MODEL_DIRECTORY_NAME,
        generation_id,
    )


def rebuild_active_generation(
    *,
    seed: int = 17,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Fit, persist, validate, and atomically activate a complete v2 generation."""

    def report(**details: Any) -> None:
        if progress is not None:
            progress(details)

    report(phase="loading_examples", eligible_partitions=0, completed_partitions=0)
    raw_examples = training_service.list_training_examples_with_embeddings()
    prepared = _prepare_rows(raw_examples)
    partitions: dict[str, list[dict[str, Any]]] = {}
    for row in prepared:
        partitions.setdefault(row["partition_key"], []).append(row)
    eligible = {
        key: value
        for key, value in partitions.items()
        if len(value) >= MIN_PARTITION_EXAMPLES
    }
    if not eligible:
        raise ValueError(
            f"No profile/HDR partition has {MIN_PARTITION_EXAMPLES} valid examples"
        )
    report(
        phase="fitting_partitions",
        eligible_partitions=len(eligible),
        completed_partitions=0,
    )

    connection = policy_store.connect_policy_store(_database_path())
    generation_id = uuid4().hex
    artifact_directory = _artifact_directory(generation_id)
    os.makedirs(artifact_directory, exist_ok=False)
    artifacts: list[PartitionPolicyArtifact] = []
    try:
        policy_store.create_generation(
            connection,
            generation_id=generation_id,
            algorithm_version=POLICY_ALGORITHM_VERSION,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            target_schema_version=TARGET_SCHEMA_VERSION,
        )
        persisted_examples: list[dict[str, Any]] = []
        all_memberships: list[dict[str, Any]] = []
        validation_rows: list[dict[str, Any]] = []
        selector_rows: list[dict[str, Any]] = []
        for partition_index, partition_key in enumerate(sorted(eligible)):
            started = perf_counter()
            curated, weights = _curate_bursts(eligible[partition_key])
            if len(curated) < MIN_PARTITION_EXAMPLES:
                logger.info(
                    "Skipping partition %s after burst curation: %d examples",
                    partition_key,
                    len(curated),
                )
                continue
            artifact = _fit_partition(
                curated,
                weights,
                generation_id=generation_id,
                seed=seed + partition_index * 100,
            )
            artifacts.append(artifact)
            selector_rows.extend(curated)
            logger.info(
                "Built policy partition %s in %.2fs with %d curated examples and %d policy(s)",
                partition_key,
                perf_counter() - started,
                len(curated),
                len(artifact.policy_ids),
            )
            report(
                phase="fitting_partitions",
                eligible_partitions=len(eligible),
                completed_partitions=len(artifacts),
            )
            artifact_name = (
                f"{MODEL_DIRECTORY_NAME}/{generation_id}/"
                f"{_partition_slug(partition_key)}.joblib"
            )
            artifact_path = os.path.join(_database_path(), artifact_name)
            temporary_path = f"{artifact_path}.tmp"
            joblib.dump(artifact, temporary_path)
            os.replace(temporary_path, artifact_path)

            entropy = -np.sum(
                np.where(
                    artifact.mixture.training_responsibilities_ > 0,
                    artifact.mixture.training_responsibilities_
                    * np.log(
                        np.maximum(
                            artifact.mixture.training_responsibilities_,
                            1e-12,
                        )
                    ),
                    0.0,
                ),
                axis=1,
            )
            if len(artifact.policy_ids) > 1:
                entropy /= math.log(len(artifact.policy_ids))
            for row_index, row in enumerate(curated):
                persisted_examples.append(
                    {
                        "photo_id": row["photo_id"],
                        "source_provenance": str(
                            row["metadata"].get("source_provenance") or "raw_preview"
                        ),
                        "feature_schema_version": FEATURE_SCHEMA_VERSION,
                        "source_features": row["source"].tolist(),
                        "feature_mask": [],
                        "target_schema_version": TARGET_SCHEMA_VERSION,
                        "target_values": row["canonical_target"],
                        "burst_group_id": row.get("burst_group_id"),
                        "sample_weight": float(weights[row_index]),
                        "metadata": row["metadata"],
                    }
                )
                for policy_index, policy_id in enumerate(artifact.policy_ids):
                    all_memberships.append(
                        {
                            "policy_id": policy_id,
                            "photo_id": row["photo_id"],
                            "responsibility": float(
                                artifact.mixture.training_responsibilities_[
                                    row_index, policy_index
                                ]
                            ),
                            "assignment_entropy": float(entropy[row_index]),
                        }
                    )
            for policy_index, policy_id in enumerate(artifact.policy_ids):
                effective_count = float(
                    np.sum(
                        weights
                        * artifact.mixture.training_responsibilities_[:, policy_index]
                    )
                )
                policy_store.add_policy_model(
                    connection,
                    generation_id=generation_id,
                    policy_id=policy_id,
                    hard_partition_key=partition_key,
                    expert_index=policy_index,
                    estimator_type=f"hierarchical_{artifact.estimator_name}",
                    artifact_name=artifact_name,
                    preprocessing={
                        "feature_names": list(artifact.feature_names),
                        "target_keys": list(artifact.target_keys),
                    },
                    validation=artifact.validation,
                    effective_sample_count=effective_count,
                )
                policy_store.replace_policy_descriptors(
                    connection,
                    generation_id=generation_id,
                    policy_id=policy_id,
                    descriptors=artifact.descriptors[policy_index],
                )
                coverage_rows = [
                    {
                        "dimension_key": record.dimension_key,
                        "bucket_key": record.bucket_key,
                        "effective_count": record.effective_count,
                        "coverage_score": record.coverage_score,
                    }
                    for record in artifact.coverage.records()
                    if record.policy_index == policy_index
                ]
                policy_store.replace_policy_coverage(
                    connection,
                    generation_id=generation_id,
                    policy_id=policy_id,
                    coverage=coverage_rows,
                )
            for policy_count, metrics in artifact.validation.get(
                "candidates", {}
            ).items():
                validation_rows.append(
                    {
                        "validation_scope": partition_key,
                        "metric_key": f"policy_count_{policy_count}_nrmse",
                        "metric_value": metrics["normalized_rmse"],
                        "details": metrics,
                    }
                )
        if not artifacts:
            raise ValueError(
                "No partition retained enough examples after burst curation"
            )
        selector = fit_rendering_selector(selector_rows, generation_id=generation_id)
        selector_path = os.path.join(artifact_directory, "rendering_selector.joblib")
        selector_temporary_path = f"{selector_path}.tmp"
        joblib.dump(selector, selector_temporary_path)
        os.replace(selector_temporary_path, selector_path)
        validation_rows.extend(
            {
                "validation_scope": "rendering_selector",
                "metric_key": key,
                "metric_value": None,
                "details": value,
            }
            for key, value in selector.validation.items()
        )
        policy_store.upsert_policy_examples(connection, persisted_examples)
        policy_store.replace_policy_memberships(
            connection,
            generation_id=generation_id,
            memberships=all_memberships,
        )
        policy_store.replace_validation_results(
            connection,
            generation_id=generation_id,
            results=validation_rows,
        )
        policy_store.activate_generation(connection, generation_id)
        pruned_generation_ids = policy_store.prune_inactive_generations(
            connection,
            retain_retired=0,
        )
    except Exception:
        policy_store.fail_generation(connection, generation_id)
        shutil.rmtree(artifact_directory, ignore_errors=True)
        raise
    finally:
        connection.close()
    for pruned_generation_id in pruned_generation_ids:
        shutil.rmtree(
            _artifact_directory(pruned_generation_id),
            ignore_errors=True,
        )
    invalidate_runtime_cache()
    result = {
        "generation_id": generation_id,
        "partition_count": len(artifacts),
        "policy_count": sum(len(artifact.policy_ids) for artifact in artifacts),
        "example_count": sum(artifact.example_count for artifact in artifacts),
    }
    report(
        phase="activating",
        eligible_partitions=len(eligible),
        completed_partitions=len(artifacts),
    )
    return result


def invalidate_runtime_cache() -> None:
    global \
        _cached_generation_id, \
        _cached_artifacts, \
        _cached_custom_names, \
        _cached_rendering_selector
    with _runtime_lock:
        _cached_generation_id = None
        _cached_artifacts = {}
        _cached_custom_names = None
        _cached_rendering_selector = None


def _load_active_rendering_selector() -> RenderingSelectorArtifact | None:
    global _cached_rendering_selector
    artifacts = _load_active_artifacts()
    if not artifacts:
        return None
    generation_id = next(iter(artifacts.values())).generation_id
    with _runtime_lock:
        if (
            _cached_rendering_selector is not None
            and _cached_rendering_selector.generation_id == generation_id
        ):
            return _cached_rendering_selector
    path = os.path.join(_artifact_directory(generation_id), "rendering_selector.joblib")
    if not os.path.exists(path):
        return None
    selector = joblib.load(path)
    if (
        not isinstance(selector, RenderingSelectorArtifact)
        or selector.generation_id != generation_id
    ):
        raise ValueError("active rendering selector artifact is invalid or stale")
    with _runtime_lock:
        _cached_rendering_selector = selector
    return selector


def _load_active_artifacts() -> dict[str, PartitionPolicyArtifact]:
    global _cached_generation_id, _cached_artifacts
    with _runtime_lock:
        if _cached_generation_id is not None:
            return dict(_cached_artifacts)
    connection = policy_store.connect_policy_store(_database_path())
    try:
        rows = policy_store.list_active_policy_models(connection)
    finally:
        connection.close()
    if not rows:
        return {}
    generation_id = rows[0]["generation_id"]
    with _runtime_lock:
        if generation_id == _cached_generation_id:
            return dict(_cached_artifacts)
        artifacts: dict[str, PartitionPolicyArtifact] = {}
        loaded_names: set[str] = set()
        for row in rows:
            artifact_name = row["artifact_name"]
            if artifact_name in loaded_names:
                continue
            loaded_names.add(artifact_name)
            artifact_path = os.path.abspath(
                os.path.join(_database_path(), artifact_name)
            )
            expected_root = os.path.abspath(
                os.path.join(_database_path(), MODEL_DIRECTORY_NAME)
            )
            if os.path.commonpath((artifact_path, expected_root)) != expected_root:
                raise ValueError("active policy artifact escaped the model directory")
            artifact = joblib.load(artifact_path)
            if (
                not isinstance(artifact, PartitionPolicyArtifact)
                or artifact.generation_id != generation_id
            ):
                raise ValueError("active policy artifact is invalid or stale")
            artifacts[artifact.partition_key] = artifact
        _cached_generation_id = generation_id
        _cached_artifacts = artifacts
        return dict(artifacts)


def has_active_generation() -> bool:
    try:
        return bool(_load_active_artifacts())
    except (RuntimeError, OSError, ValueError):
        return False


def _custom_policy_names() -> dict[str, str]:
    global _cached_custom_names
    with _runtime_lock:
        if _cached_custom_names is not None:
            return dict(_cached_custom_names)
    connection = policy_store.connect_policy_store(_database_path())
    try:
        names = policy_store.list_policy_custom_names(connection)
    finally:
        connection.close()
    with _runtime_lock:
        _cached_custom_names = names
    return dict(names)


def _active_policy_rows(*, include_examples: bool) -> list[dict[str, Any]]:
    policies = []
    custom_names = _custom_policy_names()
    for artifact in _load_active_artifacts().values():
        responsibilities = artifact.mixture.training_responsibilities_
        for policy_index, policy_id in enumerate(artifact.policy_ids):
            count = int(np.sum(np.argmax(responsibilities, axis=1) == policy_index))
            local_correctors = getattr(
                artifact,
                "local_correctors",
                [None] * len(artifact.policy_ids),
            )
            local_enabled = local_correctors[policy_index] is not None
            policy = {
                "recommendation_version": "policy-v2",
                "policy_id": policy_id,
                "policy_name": custom_names.get(
                    policy_id,
                    artifact.policy_names[policy_index],
                ),
                "style_id": policy_id,
                "style_name": custom_names.get(
                    policy_id,
                    artifact.policy_names[policy_index],
                ),
                "camera_profile": artifact.camera_profile,
                "hard_partition_key": artifact.partition_key,
                "example_count": count,
                "current_count": count,
                "policy_descriptors": artifact.descriptors[policy_index],
                "training_status": (
                    "Global + validated local refinement"
                    if local_enabled
                    else "Global conditional policy"
                ),
                "estimator_name": artifact.estimator_name,
                "local_correction_enabled": local_enabled,
            }
            if include_examples:
                policy["example_photo_ids"] = list(
                    artifact.example_photo_ids[policy_index]
                )
            policies.append(policy)
    return sorted(
        policies,
        key=lambda item: (item["camera_profile"], item["policy_name"]),
    )


def list_active_policies() -> list[dict[str, Any]]:
    return _active_policy_rows(include_examples=False)


def get_active_policy(policy_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in _active_policy_rows(include_examples=True)
            if item["policy_id"] == policy_id
        ),
        None,
    )


def list_active_policies_with_examples() -> list[dict[str, Any]]:
    return _active_policy_rows(include_examples=True)


def rename_active_policy(policy_id: str, custom_name: str) -> bool:
    connection = policy_store.connect_policy_store(_database_path())
    try:
        renamed = policy_store.rename_policy(
            connection,
            policy_id=policy_id,
            custom_name=custom_name,
        )
    finally:
        connection.close()
    if renamed:
        global _cached_custom_names
        with _runtime_lock:
            _cached_custom_names = None
    return renamed


def reset_policy_state() -> int:
    """Remove derived v2 generations and artifacts, preserving training examples."""
    removed = len(list_active_policies())
    connection = policy_store.connect_policy_store(_database_path())
    try:
        policy_store.reset_policy_v2(connection)
    finally:
        connection.close()
    shutil.rmtree(
        os.path.join(_database_path(), MODEL_DIRECTORY_NAME),
        ignore_errors=True,
    )
    invalidate_runtime_cache()
    return removed


def get_upgrade_recommendations(
    *,
    top_policies_limit: int = 100,
    target_examples_per_policy: int = 50,
) -> list[dict[str, Any]]:
    """Retrieve and rank bounded, untrained catalog neighbors for each policy."""
    from services import chroma as chroma_service
    from services.policy_feedback import capture_recommendation_review

    chroma_service._ensure_initialized()
    collection = chroma_service.collection
    if collection is None:
        return []
    artifacts = _load_active_artifacts()
    custom_names = _custom_policy_names()
    existing_photo_ids = {
        photo_id
        for artifact in artifacts.values()
        for policy_ids in artifact.example_photo_ids
        for photo_id in policy_ids
    }
    payloads: list[dict[str, Any]] = []
    policy_budget = max(0, int(top_policies_limit))
    for artifact in sorted(artifacts.values(), key=lambda item: item.partition_key):
        remaining_budget = policy_budget - len(payloads)
        if remaining_budget <= 0:
            return payloads
        policy_indices = list(range(min(len(artifact.policy_ids), remaining_budget)))
        neighbor_sets = retrieve_policy_neighbor_sets(
            collection,
            [artifact.image_anchors[index] for index in policy_indices],
            existing_photo_ids=existing_photo_ids,
        )
        neighbor_ids = list(
            dict.fromkeys(
                neighbor.photo_id
                for neighbors in neighbor_sets
                for neighbor in neighbors
            )
        )
        candidate_rows: list[tuple[str, dict[str, Any], np.ndarray, np.ndarray]] = []
        identities: dict[str, dict[str, str]] = {}
        for offset in range(0, len(neighbor_ids), 250):
            response = collection.get(
                ids=neighbor_ids[offset : offset + 250],
                include=["metadatas", "embeddings"],
            )
            response_ids = response.get("ids") or []
            response_metadata = response.get("metadatas") or []
            response_embeddings = response.get("embeddings")
            if response_embeddings is None:
                response_embeddings = []
            for row_index, photo_id in enumerate(response_ids):
                metadata = (
                    dict(response_metadata[row_index])
                    if row_index < len(response_metadata)
                    and response_metadata[row_index]
                    else {}
                )
                if hard_partition_key(metadata) != artifact.partition_key:
                    continue
                if is_stitched_panorama(metadata):
                    continue
                embedding = (
                    response_embeddings[row_index]
                    if row_index < len(response_embeddings)
                    else None
                )
                if embedding is None:
                    continue
                try:
                    source, feature_names = _source_row(metadata, embedding)
                except ValueError:
                    continue
                if feature_names != artifact.feature_names:
                    continue
                candidate_rows.append(
                    (
                        str(photo_id),
                        metadata,
                        np.asarray(embedding, dtype=np.float64),
                        source,
                    )
                )
                identities[str(photo_id)] = {
                    "lr_uuid": str(
                        metadata.get("lr_uuid") or metadata.get("uuid") or ""
                    )
                }

        candidate_data: dict[str, dict[str, Any]] = {}
        if candidate_rows:
            source_matrix = np.stack([row[3] for row in candidate_rows])
            assignments = artifact.mixture.assignments(source_matrix)
            responsibilities = np.asarray(
                [assignment.responsibilities for assignment in assignments],
                dtype=np.float64,
            )
            coverage_gains = artifact.coverage.score_candidate_gain(
                source_matrix[:, artifact.mixture.gate_feature_indices_],
                responsibilities,
                categories=[_categories(row[1]) for row in candidate_rows],
            )
            for row_index, (
                photo_id,
                metadata,
                embedding,
                _,
            ) in enumerate(candidate_rows):
                candidate_data[photo_id] = {
                    "metadata": metadata,
                    "embedding": embedding,
                    "assignment": assignments[row_index],
                    "responsibilities": responsibilities[row_index],
                    "coverage_gains": coverage_gains[row_index],
                }

        hard_labels = np.argmax(
            artifact.mixture.training_responsibilities_,
            axis=1,
        )
        for neighbor_set_index, policy_index in enumerate(policy_indices):
            policy_id = artifact.policy_ids[policy_index]
            local_correctors = getattr(
                artifact,
                "local_correctors",
                [None] * len(artifact.policy_ids),
            )
            local_enabled = local_correctors[policy_index] is not None
            current_count = int(np.sum(hard_labels == policy_index))
            needed_count = max(0, target_examples_per_policy - current_count)
            candidates: list[PolicyCandidate] = []
            for neighbor in neighbor_sets[neighbor_set_index]:
                data = candidate_data.get(neighbor.photo_id)
                if data is None:
                    continue
                assignment = data["assignment"]
                candidates.append(
                    PolicyCandidate(
                        photo_id=neighbor.photo_id,
                        embedding=data["embedding"],
                        metadata=data["metadata"],
                        responsibilities=data["responsibilities"],
                        assignment_entropy=assignment.entropy,
                        coverage_gain=float(data["coverage_gains"][policy_index]),
                        hard_partition_key=artifact.partition_key,
                        source_ambiguous=assignment.ambiguous,
                    )
                )
            ranked, diagnostics = rank_policy_candidates(
                candidates,
                policy_index=policy_index,
                target_count=needed_count,
                existing_embeddings=artifact.example_embeddings[policy_index],
                hard_partition_key=artifact.partition_key,
            )
            review_id = capture_recommendation_review(
                db_path=_database_path(),
                generation_id=artifact.generation_id,
                policy_id=policy_id,
                policy_index=policy_index,
                hard_partition_key=artifact.partition_key,
                target_count=needed_count,
                existing_photo_ids=artifact.example_photo_ids[policy_index],
                candidates=candidates,
                ranked_candidates=ranked,
                algorithm_version=POLICY_ALGORITHM_VERSION,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
            )
            payload = build_policy_recommendation_payload(
                policy_id=policy_id,
                policy_name=custom_names.get(
                    policy_id,
                    artifact.policy_names[policy_index],
                ),
                camera_profile=artifact.camera_profile,
                current_count=current_count,
                needed_count=needed_count,
                ranked_candidates=ranked,
                diagnostics=diagnostics,
                policy_descriptors=artifact.descriptors[policy_index],
                photo_identities=identities,
                training_status=(
                    "Global + validated local refinement"
                    if local_enabled
                    else "Global conditional policy"
                ),
                estimator_name=artifact.estimator_name,
                local_correction_enabled=local_enabled,
            )
            payload["generation_id"] = artifact.generation_id
            payload["policy_index"] = policy_index
            payload["review_id"] = review_id
            payloads.append(payload)
    return payloads


def record_upgrade_feedback(
    *,
    review_id: str,
    policy_id: str,
    labels: list[dict[str, Any]],
) -> dict[str, int]:
    """Persist explicit Lightroom review labels without changing active models."""
    from services.policy_feedback import record_feedback

    return record_feedback(
        db_path=_database_path(),
        review_id=review_id,
        policy_id=policy_id,
        labels=labels,
    )


def predict_absolute_edit(
    *,
    embedding: Any,
    metadata: dict[str, Any],
    current_settings: dict[str, Any] | None,
    strength: float = 1.0,
    policy_override: str | None = None,
    profile_mode: str = "suggest",
    hdr_mode: str = "suggest",
    source_provenance: str = "unknown",
) -> PolicyPrediction | None:
    artifacts = _load_active_artifacts()
    current_state = rendering_state_from_metadata(metadata)
    rendering_intent = {
        "schema_version": "rendering-state-v1",
        "selector_algorithm_version": None,
        "selector_feature_schema_version": None,
        "current": current_state,
        "proposed": current_state,
        "effective": current_state,
        "profile_mode": profile_mode,
        "hdr_mode": hdr_mode,
        "profile_confidence": 0.0,
        "profile_entropy": 1.0,
        "hdr_confidence": 0.0,
        "hdr_entropy": 1.0,
        "abstention_reason": "selector_unavailable",
    }
    selector = _load_active_rendering_selector()
    if selector is not None and (profile_mode != "off" or hdr_mode != "off"):
        rendering_intent = selector.select(
            embedding=embedding,
            current_state=current_state,
            camera_make=metadata.get("camera_make"),
            camera_model=metadata.get("camera_model"),
            profile_mode=profile_mode,
            hdr_mode=hdr_mode,
            source_provenance=source_provenance,
        )
    effective_metadata = dict(metadata)
    effective_metadata["rendering_state"] = rendering_intent["effective"]
    artifact = artifacts.get(hard_partition_key(effective_metadata))
    if artifact is None and not (
        metadata.get("rendering_state") or metadata.get("rendering_state_json")
    ):
        artifact = artifacts.get(hard_partition_key(metadata))
    if artifact is None:
        # A categorical decision may only ship with a validated continuous
        # artifact for the exact state Lightroom will use. Fall back atomically.
        rendering_intent["effective"] = current_state
        rendering_intent["abstention_reason"] = ",".join(
            filter(
                None,
                [
                    rendering_intent.get("abstention_reason"),
                    "target_policy_unavailable",
                ],
            )
        )
        effective_metadata["rendering_state"] = current_state
        artifact = artifacts.get(hard_partition_key(effective_metadata))
        if artifact is None and not (
            metadata.get("rendering_state") or metadata.get("rendering_state_json")
        ):
            artifact = artifacts.get(hard_partition_key(metadata))
    if artifact is None:
        return None
    source, feature_names = _source_row(effective_metadata, embedding)
    if feature_names != artifact.feature_names:
        logger.warning("Policy feature schema/dimension mismatch during inference")
        return None
    artifact_prediction = predict_partition_artifact(
        artifact,
        source=source,
        metadata=effective_metadata,
        embedding=embedding,
        policy_override=policy_override,
    )
    if artifact_prediction is None:
        return None
    policy_index = artifact_prediction.policy_index
    target = unflatten_absolute_target(artifact_prediction.flat_target)
    absolute_target = AbsoluteTarget(
        schema_version=TARGET_SCHEMA_VERSION,
        process_version=str(metadata.get("process_version") or "Version 6"),
        values=target,
        modeled_paths=artifact.target_keys,
    )
    absolute_target.validate()
    canonical_current = training_service.normalize_develop_settings_for_style(
        current_settings or {}
    )
    applied = interpolate_absolute_target(
        canonical_current,
        absolute_target,
        strength=strength,
    )
    return PolicyPrediction(
        generation_id=artifact.generation_id,
        policy_id=artifact.policy_ids[policy_index],
        policy_name=_custom_policy_names().get(
            artifact.policy_ids[policy_index],
            artifact.policy_names[policy_index],
        ),
        hard_partition_key=artifact.partition_key,
        confidence=artifact_prediction.confidence,
        entropy=artifact_prediction.entropy,
        target=target,
        applied=applied,
        example_count=len(artifact.example_photo_ids[policy_index]),
        rendering_intent=rendering_intent,
    )


def predict_partition_artifact(
    artifact: PartitionPolicyArtifact,
    *,
    source: np.ndarray,
    metadata: dict[str, Any],
    embedding: Any,
    policy_override: str | None = None,
) -> PartitionArtifactPrediction | None:
    """Run production inference against an in-memory partition artifact."""
    source = np.asarray(source, dtype=np.float64).reshape(-1)
    if source.shape != (len(artifact.feature_names),) or not np.all(
        np.isfinite(source)
    ):
        return None
    assignments = artifact.mixture.assignments(source[np.newaxis, :])
    assignment = assignments[0]
    if policy_override:
        if policy_override not in artifact.policy_ids:
            return None
        policy_index = artifact.policy_ids.index(policy_override)
        confidence = assignment.responsibilities[policy_index]
        if confidence < 0.40:
            return None
    else:
        if assignment.ambiguous or assignment.policy_index is None:
            return None
        policy_index = assignment.policy_index
        confidence = assignment.confidence
    predicted = artifact.calibrators[policy_index].predict(
        source[np.newaxis, :],
        categories=[_categories(metadata)],
    )[0]
    local_correctors = getattr(
        artifact,
        "local_correctors",
        [None] * len(artifact.policy_ids),
    )
    local_corrector = local_correctors[policy_index]
    if local_corrector is not None:
        correction = local_corrector.predict(np.asarray(embedding, dtype=np.float64))
        if correction is not None:
            predicted = predicted + correction
    flat_prediction: dict[str, float] = {}
    for target_index, key in enumerate(artifact.target_keys):
        lower, upper = artifact.slider_bounds[policy_index][key]
        flat_prediction[key] = float(np.clip(predicted[target_index], lower, upper))
    return PartitionArtifactPrediction(
        policy_index=policy_index,
        confidence=float(confidence),
        entropy=assignment.entropy,
        flat_target=flat_prediction,
    )


def request_rebuild() -> dict[str, Any]:
    """Coalesce and expose one non-blocking rebuild job for the active catalog."""
    global _rebuild_requested, _rebuild_worker, _rebuild_status
    with _rebuild_lock:
        _rebuild_requested += 1
        _rebuild_status = {
            "status": "queued",
            "phase": "queued",
            "requested_at": time(),
            "started_at": None,
            "completed_at": None,
            "generation": None,
            "error": None,
            "eligible_partitions": 0,
            "completed_partitions": 0,
        }
        if _rebuild_worker is not None and _rebuild_worker.is_alive():
            _rebuild_status["status"] = "running"
            _rebuild_status["phase"] = "waiting_for_current_rebuild"
            return dict(_rebuild_status)
        _rebuild_worker = threading.Thread(
            target=_rebuild_loop,
            name="PolicyV2Rebuild",
            daemon=True,
        )
        _rebuild_worker.start()
        return dict(_rebuild_status)


def schedule_rebuild() -> None:
    """Coalesce training mutations into one non-blocking v2 rebuild."""
    request_rebuild()


def discovery_status() -> dict[str, Any]:
    """Return compact, in-memory progress for the catalog-local rebuild job."""
    with _rebuild_lock:
        return dict(_rebuild_status)


def _rebuild_loop() -> None:
    global _rebuild_worker, _rebuild_status
    while True:
        with _rebuild_lock:
            requested = _rebuild_requested
            _rebuild_status.update(
                {
                    "status": "running",
                    "phase": "loading_examples",
                    "started_at": time(),
                    "completed_at": None,
                    "generation": None,
                    "error": None,
                    "eligible_partitions": 0,
                    "completed_partitions": 0,
                }
            )

        def update_progress(details: dict[str, Any]) -> None:
            with _rebuild_lock:
                if requested == _rebuild_requested:
                    _rebuild_status.update(details)

        result: dict[str, Any] | None = None
        error: str | None = None
        try:
            result = rebuild_active_generation(progress=update_progress)
        except ValueError as exc:
            logger.info("Policy v2 rebuild deferred: %s", exc)
            error = str(exc)
        except Exception as exc:
            logger.error("Policy v2 rebuild failed: %s", exc, exc_info=True)
            error = str(exc)
        with _rebuild_lock:
            if requested != _rebuild_requested:
                continue
            _rebuild_status.update(
                {
                    "status": "succeeded" if result is not None else "failed",
                    "phase": "complete" if result is not None else "failed",
                    "completed_at": time(),
                    "generation": result,
                    "error": error,
                }
            )
            _rebuild_worker = None
            return
