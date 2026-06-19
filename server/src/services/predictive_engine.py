import os
import json
import numpy as np
import joblib

from config import logger, DB_PATH
from . import training as training_service
from . import style_catalog as catalog_service

from sklearn.pipeline import Pipeline
from sklearn.linear_model import Ridge
from sklearn.decomposition import PCA

if DB_PATH:
    MODEL_DIR = os.path.join(DB_PATH, "predictive_models")
    os.makedirs(MODEL_DIR, exist_ok=True)
else:
    MODEL_DIR = None


# Minimum examples required to train a PCA + Ridge pipeline
MIN_PCA_EXAMPLES = 20
# Minimum examples required to train a direct Ridge pipeline
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


def _extract_features(embedding: list[float], metadata: dict) -> np.ndarray:
    """Combines SigLIP embedding with exposure metrics."""
    features = list(embedding)
    for key in METADATA_FEATURES:
        val = metadata.get(key, 0.5)
        # Handle string floats just in case
        try:
            features.append(float(val))
        except (ValueError, TypeError):
            features.append(0.5)
    return np.array(features, dtype=np.float32)


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

            # If the normalized crop deviates from a square by more than 2% of the image size, abandon it
            if abs(crop_width - crop_height) > 0.02:
                del canonical["crop"]
            else:
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

    for emb, meta, canonical in valid_examples:
        X_list.append(_extract_features(emb, meta))
        # For missing targets in an example, use 0.0 (or we could use mean, but 0.0 is Lightroom's typical default)
        Y_row = [canonical.get(k, 0.0) for k in target_keys]
        Y_list.append(Y_row)

    X = np.array(X_list)
    Y = np.array(Y_list)

    n_samples = len(X)
    if n_samples < MIN_PCA_EXAMPLES:
        return

    logger.info(
        f"Training predictive model for style '{style_id}' with {n_samples} examples."
    )

    # Select architecture based on data volume
    if n_samples >= MIN_DIRECT_EXAMPLES:
        # Enough data for direct Ridge regression over 768+ dimensions
        model = Ridge(alpha=1.0)
        tier = "ml_direct"
    else:
        # 20-50 samples: Use PCA to prevent overfitting the 768d space
        n_components = min(10, n_samples // 2)
        model = Pipeline(
            [("pca", PCA(n_components=n_components)), ("ridge", Ridge(alpha=1.0))]
        )
        tier = "ml_pca"

    try:
        model.fit(X, Y)
        joblib.dump(model, _get_model_path(style_id))

        meta_info = {"tier": tier, "target_keys": target_keys, "n_samples": n_samples}
        with open(_get_metadata_path(style_id), "w") as f:
            json.dump(meta_info, f)

        logger.info(f"Successfully trained {tier} model for {style_id}.")
    except Exception as exc:
        logger.error(f"Error training model for {style_id}: {exc}")


def predict_edits(
    style_id: str, embedding: list[float], metadata: dict
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

        X = _extract_features(embedding, metadata).reshape(1, -1)
        Y_pred = model.predict(X)[0]

        # Zip with keys and round for neatness
        flat_predictions = {k: round(float(v), 4) for k, v in zip(target_keys, Y_pred)}

        # Unflatten back to nested Lightroom JSON structure
        predictions = unflatten_canonical_settings(flat_predictions)

        # Add the tier info into a special key so the caller knows it was ML-predicted
        predictions["_ml_tier"] = meta_info.get("tier", "unknown")

        return predictions
    except Exception as exc:
        logger.error(f"Failed to run predictive inference for {style_id}: {exc}")
        return None
