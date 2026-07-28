"""On-the-fly locally linear regression for massively scaled editing policies."""

import math
from typing import Any

import numpy as np

from config import logger
from . import policy_runtime
from . import policy_targets
from . import style_engine
from . import training as training_service
from .policy_models import ReducedRankRidge


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

    # Step 3: Burst Deduplication
    # Group by capture time to avoid a singular local matrix from 30 identical burst frames
    close_neighbors.sort(key=lambda n: n.get("capture_time", 0.0))
    deduplicated: list[dict[str, Any]] = []
    last_time = -9999.0
    for n in close_neighbors:
        capture_time = n.get("capture_time", 0.0)
        # If capture time is missing (0.0) or far enough from the last accepted photo
        if capture_time == 0.0 or (capture_time - last_time) > 10.0:
            deduplicated.append(n)
            last_time = capture_time

    # Step 4: Minimum Count Check
    if len(deduplicated) < min_neighbors:
        logger.info(
            "KNN regression aborted: only %d viable neighbors after deduplication.",
            len(deduplicated)
        )
        return None

    # Step 5: Extract Flattened Targets & Apply Variance Defense
    X_train_list = []
    Y_train_list = []
    
    # Track the keys for unflattening
    target_keys = set()
    for n in deduplicated:
        X_train_list.append(n["embedding"])
        flat_target = policy_targets.flatten_absolute_target(n.get("canonical_settings", {}))
        Y_train_list.append(flat_target)
        target_keys.update(flat_target.keys())
        
    sorted_keys = sorted(list(target_keys))
    
    # Build the numpy arrays
    X_train = np.array(X_train_list, dtype=np.float64)
    Y_train = np.zeros((len(deduplicated), len(sorted_keys)), dtype=np.float64)
    
    for row_idx, target_dict in enumerate(Y_train_list):
        for col_idx, key in enumerate(sorted_keys):
            Y_train[row_idx, col_idx] = target_dict.get(key, 0.0)
            
    # Variance check on Exposure (if present) to detect conflicting edits in identical photos
    if "Exposure2012" in sorted_keys:
        exp_idx = sorted_keys.index("Exposure2012")
        exp_std = np.std(Y_train[:, exp_idx])
        if exp_std > max_exposure_std:
            logger.info("KNN regression aborted: Target exposure variance too high (%.2f).", exp_std)
            return None

    # Step 6: Fit the Local Linear Model
    try:
        model = ReducedRankRidge(alpha=1.0, rank=6)
        model.fit(X_train, Y_train)
    except Exception as e:
        logger.error("KNN local regression fit failed: %s", e)
        return None

    # Step 7: Predict for the query photo
    try:
        query_X = np.array([query_embedding], dtype=np.float64)
        pred_Y = model.predict(query_X)[0]
    except Exception as e:
        logger.error("KNN local regression predict failed: %s", e)
        return None
        
    # Build the flat prediction dictionary
    predicted_flat = {key: float(pred_Y[i]) for i, key in enumerate(sorted_keys)}
    
    # Unflatten to canonical target schema
    predicted_canonical = policy_targets.unflatten_absolute_target(predicted_flat)
    
    if camera_profile and camera_profile.casefold() != "default":
        predicted_canonical["CameraProfile"] = camera_profile
        
    absolute_target = policy_targets.AbsoluteTarget(
        schema_version=policy_targets.TARGET_SCHEMA_VERSION,
        process_version=str(metadata.get("process_version") or "Version 6"),
        values=predicted_canonical,
        modeled_paths=tuple(sorted_keys),
    )
    
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
    return style_engine.StyleEngineResult(
        recipe=style_engine._canonical_to_edit_recipe(applied, summary),
        confidence=1.0,  # KNN is considered highly confident if it passed all defenses
        matched_count=len(deduplicated),
        engine="knn_regression",
    )
