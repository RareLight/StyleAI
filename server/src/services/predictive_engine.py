import os
import json
import numpy as np
import joblib

from config import logger, DB_PATH
from . import training as training_service
from . import style_catalog as catalog_service

from sklearn.pipeline import Pipeline
from sklearn.linear_model import ElasticNet
from sklearn.cross_decomposition import PLSRegression
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.compose import ColumnTransformer


class WeightedPLSRegression(PLSRegression):
    """PLSRegression with sample weight support via row scaling."""

    def fit(self, X, Y, sample_weight=None):
        if sample_weight is not None:
            sw_sqrt = np.sqrt(np.asarray(sample_weight))[:, None]
            X = X * sw_sqrt
            Y = Y * sw_sqrt
        return super().fit(X, Y)


if DB_PATH:
    MODEL_DIR = os.path.join(DB_PATH, "predictive_models")
    os.makedirs(MODEL_DIR, exist_ok=True)
else:
    MODEL_DIR = None


# Minimum examples required to train a supervised PLS regression pipeline
MIN_PCA_EXAMPLES = 15
MIN_PLS_EXAMPLES = MIN_PCA_EXAMPLES
# Minimum examples required to train an ElasticNet / direct Ridge pipeline
MIN_DIRECT_EXAMPLES = 50

# Features to extract from metadata
METADATA_FEATURES = [
    "exp_luminance_mean",
    "exp_contrast",
    "exp_warmth_proxy",
    "zone_deep_shadows",
    "zone_shadows",
    "zone_midtones",
    "zone_highlights",
    "zone_bright_highlights",
]

# We will predict all numeric canonical settings.
# We'll dynamically determine the target keys during training.


def _get_model_path(style_id: str) -> str:
    return os.path.join(MODEL_DIR, f"{style_id}_model.joblib")


def _get_metadata_path(style_id: str) -> str:
    return os.path.join(MODEL_DIR, f"{style_id}_meta.json")


def _extract_features(embedding: list[float], metadata: dict) -> list:
    """Combines profile, SigLIP embedding, and exposure metrics into a single object list."""
    profile = metadata.get("camera_profile") or "Default"
    features = [profile]
    features.extend(list(embedding))
    for key in METADATA_FEATURES:
        val = metadata.get(key, 0.5)
        # Handle string floats just in case
        try:
            features.append(float(val))
        except (ValueError, TypeError):
            features.append(0.5)
    return features


def train_style_models():
    """Iterates through styles and trains regression models for those with enough data."""
    import time

    logger.info("Starting background training of predictive models...")

    styles = catalog_service.list_styles()
    success_count = 0
    start_time = time.time()

    for style in styles:
        style_id = style["style_id"]
        style_full = catalog_service.get_style(style_id)
        if not style_full:
            continue
        example_ids = style_full.get("example_photo_ids", [])
        n_examples = len(example_ids)

        if n_examples < MIN_PCA_EXAMPLES:
            # Not enough data for ML, skip or delete old model
            model_path = _get_model_path(style_id)
            if os.path.exists(model_path):
                try:
                    os.remove(model_path)
                    os.remove(_get_metadata_path(style_id))
                    logger.info(
                        f"Removed outdated model for style {style_id} (only {n_examples} examples)."
                    )
                except OSError:
                    pass
            continue

        logger.info(
            f"Initiating training for style {style_id} with {n_examples} examples."
        )
        _train_single_style(style_id, example_ids)
        success_count += 1

    duration = time.time() - start_time
    logger.info(
        f"Background training complete. Trained {success_count} models in {duration:.1f}s."
    )


def flatten_canonical_settings(canonical: dict) -> dict[str, float]:
    """Flattens nested HSL, Color Grading, and Tone Curve arrays into scalar ML targets."""
    flat = {}

    # 1. Global scalars (top level)
    for k, v in canonical.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            flat[k] = float(v)

    # 2. HSL
    hsl = canonical.get("hsl", {})
    for color, props in hsl.items():
        if isinstance(props, dict):
            for prop, val in props.items():
                if isinstance(val, (int, float)):
                    flat[f"hsl_{color}_{prop}"] = float(val)

    # 3. Color Grading
    cg = canonical.get("color_grading", {})
    for region, props in cg.items():
        if isinstance(props, dict):
            for prop, val in props.items():
                if isinstance(val, (int, float)):
                    flat[f"cg_{region}_{prop}"] = float(val)
        elif isinstance(props, (int, float)):
            flat[f"cg_{region}"] = float(props)

    # 4. Tone Curves (Standardize all variable-length curves to 16 points)
    tc = canonical.get("tone_curve", {}).get("point_curve", {})
    for chan, curve in tc.items():
        if isinstance(curve, list) and len(curve) >= 4:
            xs = curve[::2]
            ys = curve[1::2]
            # Use same 16-point mathematical sampler as style_engine interpolation
            x_eval = np.linspace(0, 255, 16)
            y_eval = np.interp(x_eval, xs, ys)
            for i, y in enumerate(y_eval):
                flat[f"curve_{chan}_y_{i}"] = float(y)

    # 5. Crop
    crop = canonical.get("crop", {})
    if isinstance(crop, dict) and crop:
        for prop in ["left", "right", "top", "bottom", "angle"]:
            if prop in crop and isinstance(crop[prop], (int, float)):
                flat[f"crop_{prop}"] = float(crop[prop])

    # 6. Categorical Overrides (White Balance)
    wb = canonical.get("white_balance", "As Shot")
    flat["white_balance_is_custom"] = 1.0 if str(wb).lower() != "as shot" else 0.0

    return flat


def unflatten_canonical_settings(flat: dict[str, float]) -> dict:
    """Rebuilds the standard Lightroom canonical nested JSON structure from flat ML predictions."""
    canonical = {"hsl": {}, "color_grading": {}, "tone_curve": {"point_curve": {}}}

    curve_data = {}

    for k, v in flat.items():
        # Clamp standard Hue predictions back to safe boundaries
        if k.endswith("_hue"):
            if k.startswith("cg_"):
                v = max(0.0, min(360.0, v))
            else:
                v = max(-100.0, min(100.0, v))

        if k.startswith("hsl_"):
            parts = k.split("_")
            if len(parts) == 3:  # hsl_red_hue
                _, color, prop = parts
                canonical["hsl"].setdefault(color, {})[prop] = v
        elif k.startswith("cg_"):
            parts = k.split("_")
            if len(parts) == 3:  # cg_shadows_hue
                _, region, prop = parts
                canonical["color_grading"].setdefault(region, {})[prop] = v
            elif len(parts) == 2:  # cg_balance
                _, region = parts
                canonical["color_grading"][region] = v
        elif k.startswith("curve_"):
            parts = k.split("_")
            if len(parts) == 4:  # curve_rgb_y_0
                _, chan, _, idx = parts
                curve_data.setdefault(chan, {})[int(idx)] = v
        elif k.startswith("crop_"):
            parts = k.split("_")
            if len(parts) == 2:
                canonical.setdefault("crop", {})[parts[1]] = v
        else:
            canonical[k] = v

    # Reconstruct curves using fixed x-axis spacing
    x_eval = np.linspace(0, 255, 16)
    for chan, y_dict in curve_data.items():
        if len(y_dict) == 16:
            curve = []
            for i in range(16):
                curve.extend([round(float(x_eval[i]), 1), round(float(y_dict[i]), 1)])
            canonical["tone_curve"]["point_curve"][chan] = curve

    # Enforce original aspect ratio constraint on crops
    if "crop" in canonical:
        crop = canonical["crop"]
        left = crop.get("left")
        right = crop.get("right")
        top = crop.get("top")
        bottom = crop.get("bottom")

        # We only care about aspect ratio if all 4 boundaries exist and form a valid box
        if (
            all(v is not None for v in (left, right, top, bottom))
            and right > left
            and bottom > top
        ):
            crop_width = right - left
            crop_height = bottom - top

            # Force perfect aspect ratio preservation by averaging width/height
            avg_dim = (crop_width + crop_height) / 2.0
            center_x = (left + right) / 2.0
            center_y = (top + bottom) / 2.0
            crop["left"] = max(0.0, center_x - avg_dim / 2.0)
            crop["right"] = min(1.0, center_x + avg_dim / 2.0)
            crop["top"] = max(0.0, center_y - avg_dim / 2.0)
            crop["bottom"] = min(1.0, center_y + avg_dim / 2.0)
            if "angle" in crop:
                crop["angle"] = max(-45.0, min(45.0, crop["angle"]))
        elif "angle" in crop and not any(
            k in crop for k in ("left", "right", "top", "bottom")
        ):
            # It's just a rotation
            crop["angle"] = max(-45.0, min(45.0, crop["angle"]))
        else:
            del canonical["crop"]

    # Categorical Overrides
    if "white_balance_is_custom" in canonical:
        is_custom = canonical.pop("white_balance_is_custom")
        canonical["white_balance"] = "Custom" if is_custom >= 0.7 else "As Shot"

    if not canonical.get("hsl"):
        canonical.pop("hsl", None)
    if not canonical.get("color_grading"):
        canonical.pop("color_grading", None)
    if not canonical.get("tone_curve", {}).get("point_curve"):
        canonical.pop("tone_curve", None)

    return canonical


def _get_default_val(key: str) -> float:
    """Return the true Lightroom mathematical default for a missing flattened target key."""
    if key in ("crop_right", "crop_bottom"):
        return 1.0
    if key == "cg_blending" or (key.startswith("cg_") and key.endswith("_blending")):
        return 50.0
    if key.startswith("curve_") and "_y_" in key:
        try:
            idx = int(key.split("_")[-1])
            return float(np.linspace(0, 255, 16)[idx])
        except (ValueError, IndexError):
            return 0.0
    return 0.0


def _curate_training_cluster(
    valid_examples: list[tuple],
) -> tuple[list[tuple], list[float]]:
    """Cluster training examples into bursts and select relative hero shots with density weighting.

    Returns:
        curated_examples: List of (emb, meta, canonical) tuples.
        sample_weights: List of float weights corresponding to each curated example.
    """
    if not valid_examples:
        return [], []

    n = len(valid_examples)
    visited = [False] * n
    clusters = []

    # Extract timestamps and embeddings for clustering
    timestamps = []
    for _, meta, _ in valid_examples:
        ct = meta.get("capture_time")
        if ct is not None:
            try:
                timestamps.append(float(ct))
                continue
            except (ValueError, TypeError):
                pass
        # Fallback to captured_at string parsing if possible
        cat_str = meta.get("captured_at")
        if cat_str and isinstance(cat_str, str):
            try:
                from datetime import datetime

                dt = datetime.strptime(cat_str[:19], "%Y-%m-%d %H:%M:%S")
                timestamps.append(dt.timestamp())
                continue
            except Exception:
                pass
        timestamps.append(None)

    # Precompute normalized embeddings and pairwise cosine similarity matrix via BLAS
    embs_mat = np.array([ex[0] for ex in valid_examples], dtype=np.float32)
    if embs_mat.ndim == 1:
        embs_mat = embs_mat.reshape(1, -1)
    norms = np.linalg.norm(embs_mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normed_embs = embs_mat / norms
    sim_matrix = normed_embs @ normed_embs.T

    for i in range(n):
        if visited[i]:
            continue
        cluster_indices = [i]
        visited[i] = True
        t_i = timestamps[i]

        for j in range(i + 1, n):
            if visited[j]:
                continue
            t_j = timestamps[j]

            # Check temporal window (delta <= 10 seconds) if both timestamps are present
            time_close = False
            if t_i is not None and t_j is not None:
                if abs(t_i - t_j) <= 10.0:
                    time_close = True
            else:
                # If timestamp is missing, rely strictly on visual similarity threshold
                time_close = True

            if not time_close:
                continue

            # Check cosine distance <= 0.05 (cosine similarity >= 0.95)
            cos_sim = float(sim_matrix[i, j])
            if cos_sim >= 0.95:
                cluster_indices.append(j)
                visited[j] = True

        clusters.append(cluster_indices)

    curated_examples = []
    sample_weights = []

    for cluster_indices in clusters:
        cluster_size = len(cluster_indices)
        if cluster_size == 1:
            curated_examples.append(valid_examples[cluster_indices[0]])
            sample_weights.append(1.0)
            continue

        # Evaluate relative star ratings within the cluster
        ratings = [
            int(valid_examples[idx][1].get("rating", 0) or 0) for idx in cluster_indices
        ]
        max_rating = max(ratings)

        # Filter to candidates matching the relative maximum rating
        top_candidates = [
            idx
            for idx in cluster_indices
            if int(valid_examples[idx][1].get("rating", 0) or 0) == max_rating
        ]

        if len(top_candidates) > 1:
            # Tie-breaker 1: Pick Status == 1 (Picked/Flagged)
            picked = [
                idx
                for idx in top_candidates
                if int(valid_examples[idx][1].get("pick_status", 0) or 0) == 1
            ]
            if picked:
                top_candidates = picked

        if len(top_candidates) > 1:
            # Tie-breaker 2: Develop setting variance / edit complexity
            def _edit_complexity(idx):
                canonical = valid_examples[idx][2]
                return len(
                    [
                        k
                        for k, v in canonical.items()
                        if v != 0 and v != 0.0 and str(v) != ""
                    ]
                )

            top_candidates.sort(key=lambda idx: _edit_complexity(idx), reverse=True)

        weight_per_hero = 1.0 / len(top_candidates)
        for idx in top_candidates:
            curated_examples.append(valid_examples[idx])
            sample_weights.append(weight_per_hero)

    return curated_examples, sample_weights


def _train_single_style(style_id: str, example_ids: list[str]):
    # Fetch embeddings and metadata from chroma
    try:
        result = training_service._training_collection.get(
            ids=example_ids, include=["metadatas", "embeddings"]
        )
    except Exception as exc:
        logger.warning(f"Failed to fetch data for style {style_id}: {exc}")
        return

    ids = result.get("ids", [])
    metadatas = result.get("metadatas", [])
    embeddings = result.get("embeddings", [])

    if len(ids) < MIN_PCA_EXAMPLES:
        return

    # Build X (features) and Y (targets)
    X_list = []
    Y_list = []

    # We need to find all unique slider keys across all examples to ensure consistent Y dimensions.
    all_target_keys = set()
    valid_examples = []

    for i, pid in enumerate(ids):
        meta = metadatas[i]
        emb = embeddings[i]

        canonical_raw = meta.get("canonical_settings", "{}")
        try:
            canonical = (
                json.loads(canonical_raw)
                if isinstance(canonical_raw, str)
                else dict(canonical_raw)
            )
        except Exception:
            continue

        # Flatten dictionary to handle nested HSL, CG, and Tone Curves
        numeric_canonical = flatten_canonical_settings(canonical)

        if not numeric_canonical:
            continue

        all_target_keys.update(numeric_canonical.keys())
        valid_examples.append((emb, meta, numeric_canonical))

    target_keys = sorted(list(all_target_keys))

    low_headroom_blacks = []
    high_headroom_whites = []

    for emb, meta, canonical in valid_examples:
        is_hdr = "HDR" in str(meta.get("camera_profile", ""))
        if not is_hdr:
            sh = float(meta.get("shadow_headroom", 0.5))
            hh = float(meta.get("highlight_headroom", 0.5))

            if sh <= 0.05 and "blacks" in canonical:
                low_headroom_blacks.append(canonical["blacks"])
            if hh >= 0.95 and "whites" in canonical:
                high_headroom_whites.append(canonical["whites"])

    safety_bounds = {}
    if low_headroom_blacks:
        safety_bounds["blacks_min"] = min(low_headroom_blacks)
    if high_headroom_whites:
        safety_bounds["whites_max"] = max(high_headroom_whites)

    slider_bounds = {}
    for k in target_keys:
        vals = [
            float(canonical[k])
            for _, _, canonical in valid_examples
            if k in canonical and isinstance(canonical[k], (int, float))
        ]
        if vals:
            slider_bounds[k] = {
                "min": min(vals),
                "max": max(vals),
            }

    # Apply Pillar 1: Burst Curation & Relative Hero Shot Weighting
    curated_examples, sample_weights = _curate_training_cluster(valid_examples)
    if not curated_examples or len(curated_examples) < MIN_PLS_EXAMPLES:
        logger.info(
            f"Skipping model training for style '{style_id}': only {len(curated_examples)} examples after burst curation (need {MIN_PLS_EXAMPLES})."
        )
        return

    for emb, meta, canonical in curated_examples:
        X_list.append(_extract_features(emb, meta))
        Y_row = [canonical.get(k, _get_default_val(k)) for k in target_keys]
        Y_list.append(Y_row)

    X = np.array(X_list, dtype=object)
    Y = np.array(Y_list)
    sample_weights = np.array(sample_weights, dtype=float)

    n_samples = len(X)
    logger.info(
        f"Training predictive model for style '{style_id}' with {n_samples} curated hero examples (from {len(valid_examples)} total)."
    )

    # Preprocessor: OneHotEncode the camera profile (column 0), Scale the rest
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [0]),
            ("num", StandardScaler(), slice(1, None)),
        ],
        remainder="passthrough",
    )

    # Select architecture based on curated data volume
    if n_samples >= MIN_DIRECT_EXAMPLES:
        # Pillar 3: N >= 50: Use ElasticNet (l1_ratio=0.2) for sparse feature selection over high-dimensional vision space
        model = Pipeline(
            [
                ("preprocessor", preprocessor),
                ("elasticnet", ElasticNet(alpha=0.1, l1_ratio=0.2, max_iter=2000)),
            ]
        )
        tier = "ml_direct"
        fit_params = {"elasticnet__sample_weight": sample_weights}
    else:
        # Pillar 2: 15 <= N < 50: Use WeightedPLSRegression to project collinear features into latent components
        n_components = min(10, n_samples // 2)
        model = Pipeline(
            [
                ("preprocessor", preprocessor),
                ("pls", WeightedPLSRegression(n_components=n_components, scale=False)),
            ]
        )
        tier = "ml_pca"
        fit_params = {"pls__sample_weight": sample_weights}

    try:
        model.fit(X, Y, **fit_params)
        joblib.dump(model, _get_model_path(style_id))

        meta_info = {
            "tier": tier,
            "target_keys": target_keys,
            "n_samples": n_samples,
            "safety_bounds": safety_bounds,
            "slider_bounds": slider_bounds,
        }
        with open(_get_metadata_path(style_id), "w") as f:
            json.dump(meta_info, f)

        logger.info(f"Successfully trained {tier} model for {style_id}.")
    except Exception as exc:
        logger.error(f"Error training model for {style_id}: {exc}")


def predict_edits(
    style_id: str, embedding: list[float], metadata: dict, do_not_clip: bool = True
) -> dict[str, float] | None:
    """
    Given a new image's embedding and metadata, predict the develop settings.
    Returns None if no model is trained for this style.
    """
    model_path = _get_model_path(style_id)
    meta_path = _get_metadata_path(style_id)

    if not os.path.exists(model_path) or not os.path.exists(meta_path):
        return None

    try:
        with open(meta_path, "r") as f:
            meta_info = json.load(f)

        target_keys = meta_info.get("target_keys", [])
        if not target_keys:
            return None

        model = joblib.load(model_path)

        X_feat = _extract_features(embedding, metadata)
        X = np.array([X_feat], dtype=object)
        Y_pred = model.predict(X)[0]

        # Zip with keys and round for neatness
        flat_predictions = {k: float(v) for k, v in zip(target_keys, Y_pred)}

        # 1. Clamp ALL predicted sliders to their learned training boundaries to prevent linear extrapolation
        slider_bounds = meta_info.get("slider_bounds", {})
        for k, b in slider_bounds.items():
            if k in flat_predictions and isinstance(b, dict):
                min_v = b.get("min")
                max_v = b.get("max")
                if min_v is not None and max_v is not None:
                    flat_predictions[k] = max(
                        float(min_v), min(float(max_v), flat_predictions[k])
                    )

        # 2. Apply specific headroom clipping prevention if requested
        is_hdr = "HDR" in str(metadata.get("camera_profile", ""))
        if do_not_clip and not is_hdr:
            bounds = meta_info.get("safety_bounds", {})
            sh = float(metadata.get("shadow_headroom", 0.5))
            hh = float(metadata.get("highlight_headroom", 0.5))

            if sh <= 0.05 and "blacks_min" in bounds and "blacks" in flat_predictions:
                flat_predictions["blacks"] = max(
                    bounds["blacks_min"], flat_predictions["blacks"]
                )

            if hh >= 0.95 and "whites_max" in bounds and "whites" in flat_predictions:
                flat_predictions["whites"] = min(
                    bounds["whites_max"], flat_predictions["whites"]
                )

        flat_predictions = {k: round(v, 4) for k, v in flat_predictions.items()}

        # Unflatten back to nested Lightroom JSON structure
        predictions = unflatten_canonical_settings(flat_predictions)

        # Add the tier info into a special key so the caller knows it was ML-predicted
        predictions["_ml_tier"] = meta_info.get("tier", "unknown")

        return predictions
    except Exception as exc:
        logger.error(f"Failed to run predictive inference for {style_id}: {exc}")
        return None
