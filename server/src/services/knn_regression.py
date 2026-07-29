"""Experimental bounded local regression for editing-policy residual research."""

from typing import Any

import numpy as np

from config import logger
from . import policy_targets
from . import style_engine
from . import training as training_service
from .policy_models import ReducedRankRidge


def _finite_capture_time(neighbor: dict[str, Any]) -> float | None:
    try:
        value = float(neighbor.get("capture_time"))
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) and value > 0 else None


def _normalized_embedding(neighbor: dict[str, Any]) -> np.ndarray | None:
    embedding = neighbor.get("embedding")
    if embedding is None:
        return None
    vector = np.asarray(embedding, dtype=np.float64).reshape(-1)
    if not len(vector) or not np.all(np.isfinite(vector)):
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


def _training_quality(neighbor: dict[str, Any]) -> tuple[int, bool, int, str]:
    settings = neighbor.get("canonical_settings") or {}
    complexity = len(policy_targets.flatten_absolute_target(settings))
    return (
        int(neighbor.get("rating", 0) or 0),
        int(neighbor.get("pick_status", 0) or 0) == 1,
        complexity,
        str(neighbor.get("photo_id") or ""),
    )


def _curate_bursts(
    neighbors: list[dict[str, Any]],
    *,
    maximum_seconds: float = 10.0,
    maximum_distance: float = 0.05,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Select one weighted hero from each temporal-and-visual burst."""
    if not neighbors:
        return [], np.empty(0, dtype=np.float64)
    parents = list(range(len(neighbors)))
    embeddings = [_normalized_embedding(item) for item in neighbors]

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
        (capture_time, index)
        for index, item in enumerate(neighbors)
        if (capture_time := _finite_capture_time(item)) is not None
    )
    for position, (first_time, first_index) in enumerate(timed):
        for second_time, second_index in timed[position + 1 :]:
            if second_time - first_time > maximum_seconds:
                break
            first_embedding = embeddings[first_index]
            second_embedding = embeddings[second_index]
            if first_embedding is None or second_embedding is None:
                continue
            if 1.0 - float(first_embedding @ second_embedding) <= maximum_distance:
                union(first_index, second_index)

    clusters: dict[int, list[int]] = {}
    for index in range(len(neighbors)):
        clusters.setdefault(find(index), []).append(index)
    curated = []
    weights = []
    for indices in clusters.values():
        hero_index = max(indices, key=lambda index: _training_quality(neighbors[index]))
        curated.append(neighbors[hero_index])
        weights.append(1.0 / len(indices))
    order = np.argsort([float(item.get("distance", 1.0)) for item in curated])
    return (
        [curated[int(index)] for index in order],
        np.asarray([weights[int(index)] for index in order], dtype=np.float64),
    )


def predict_knn_local_regression(
    query_embedding: list[float],
    metadata: dict[str, Any],
    current_settings: dict[str, Any] | None,
    strength: float = 1.0,
    k_neighbors: int = 100,
    max_distance: float = 0.15,
    min_neighbors: int = 10,
    max_exposure_std: float = 1.25,
) -> style_engine.StyleEngineResult | None:
    """Retrieve visual neighbors and fit a local linear model on the fly.

    Returns None if the neighborhood is too sparse, redundant, or conflicted.
    """
    if query_embedding is None:
        return None

    # Step 1: Retrieve K nearest neighbors from ChromaDB
    camera_profile = metadata.get("camera_profile")
    neighbors = training_service.query_similar_training_examples(
        query_embedding=query_embedding,
        n_results=k_neighbors,
        camera_profile=camera_profile,
    )
    if not neighbors:
        return None

    # Step 2: Distance Gating (Sparsity Cutoff)
    # Filter out photos that are visually dissimilar
    close_neighbors = [n for n in neighbors if n.get("distance", 1.0) <= max_distance]

    # Step 3: Burst curation requires both temporal and visual proximity.
    deduplicated, sample_weights = _curate_bursts(close_neighbors)

    # Step 4: Minimum Count Check
    if len(deduplicated) < min_neighbors:
        logger.info(
            "KNN regression aborted: only %d viable neighbors after deduplication.",
            len(deduplicated),
        )
        return None

    # Step 5: Extract Flattened Targets & Apply Variance Defense
    X_train_list = []
    Y_train_list = []

    # Track the keys for unflattening
    target_keys = set()
    for n in deduplicated:
        if n.get("embedding") is None:
            return None
        X_train_list.append(n["embedding"])
        flat_target = policy_targets.flatten_absolute_target(
            n.get("canonical_settings", {})
        )
        Y_train_list.append(flat_target)
        target_keys.update(flat_target.keys())
    sorted_keys = sorted(target_keys)
    if not sorted_keys:
        return None

    # Build the numpy arrays
    X_train = np.array(X_train_list, dtype=np.float64)
    Y_train = np.zeros((len(deduplicated), len(sorted_keys)), dtype=np.float64)
    for row_idx, target_dict in enumerate(Y_train_list):
        for col_idx, key in enumerate(sorted_keys):
            Y_train[row_idx, col_idx] = target_dict.get(
                key,
                policy_targets.default_flat_target_value(key),
            )

    # Variance check on Exposure (if present) to detect conflicting edits in identical photos
    if "exposure" in sorted_keys:
        exp_idx = sorted_keys.index("exposure")
        exp_std = np.std(Y_train[:, exp_idx])
        if exp_std > max_exposure_std:
            logger.info(
                "KNN regression aborted: Target exposure variance too high (%.2f).",
                exp_std,
            )
            return None

    # Step 6: Fit the Local Linear Model
    try:
        model = ReducedRankRidge(alpha=1.0, rank=6)
        model.fit(X_train, Y_train, sample_weight=sample_weights)
    except Exception as e:
        logger.error("KNN local regression fit failed: %s", e, exc_info=True)
        return None

    # Step 7: Predict for the query photo
    try:
        query_X = np.array([query_embedding], dtype=np.float64)
        pred_Y = model.predict(query_X)[0]
    except Exception as e:
        logger.error("KNN local regression predict failed: %s", e, exc_info=True)
        return None

    # Build the flat prediction dictionary
    predicted_flat = {
        key: float(
            np.clip(pred_Y[index], np.min(Y_train[:, index]), np.max(Y_train[:, index]))
        )
        for index, key in enumerate(sorted_keys)
    }

    # Unflatten to canonical target schema
    predicted_canonical = policy_targets.unflatten_absolute_target(predicted_flat)

    absolute_target = policy_targets.AbsoluteTarget(
        schema_version=policy_targets.TARGET_SCHEMA_VERSION,
        process_version=str(metadata.get("process_version") or "Version 6"),
        values=predicted_canonical,
        modeled_paths=tuple(sorted_keys),
    )
    absolute_target.validate()

    canonical_current = training_service.normalize_develop_settings_for_style(
        current_settings or {}
    )

    # Interpolate strength with current settings
    applied = policy_targets.interpolate_absolute_target(
        canonical_current,
        absolute_target,
        strength=strength,
    )

    summary = f"KNN Local Neighborhood (N={len(deduplicated)})"
    mean_distance = float(
        np.mean([float(item.get("distance", max_distance)) for item in deduplicated])
    )
    confidence = float(np.clip(1.0 - mean_distance / max_distance, 0.0, 0.99))
    return style_engine.StyleEngineResult(
        recipe=style_engine._canonical_to_edit_recipe(applied, summary),
        confidence=confidence,
        matched_count=len(deduplicated),
        engine="knn_regression",
    )
