"""
Photographer Style Engine — LLM-free AI Edit backend.

Produces a Lightroom edit recipe that reproduces the photographer's personal
editing style by:

  1. Retrieving the top-N visually similar training examples via CLIP.
  2. Re-scoring candidates with a multi-criteria composite score that adds:
       * Exposure proximity  (luminance/contrast match)
       * Scene-type overlap  (genre match)
       * Time-of-day proximity  (light-quality cue)
  3. Interpolating the develop settings of the top-K winners weighted by
     their composite score.
  4. Applying a small RAW-adaptive compensation layer that adjusts the
     interpolated recipe to account for exposure differences between the
     training examples and the new photo.
  5. Computing a confidence score so the plugin can show quality feedback.

When confidence is below the threshold and LLM fallback is enabled, the
caller should fall back to the usual LLM path with training examples as
few-shot context.
"""

from __future__ import annotations

import json
import math
import numpy as np
from typing import Any

from config import logger
from . import training as training_service

# ---------------------------------------------------------------------------
# Tunable weights (can be made user-configurable later via config / prefs)
# ---------------------------------------------------------------------------

WEIGHT_CLIP = 0.50  # CLIP visual similarity
WEIGHT_EXPOSURE = 0.25  # Exposure state proximity
WEIGHT_SCENE = 0.15  # Scene-type tag overlap
WEIGHT_TIME_OF_DAY = 0.10  # Time-of-day / lighting cue

# Confidence thresholds
CONFIDENCE_GOOD = 0.55  # ≥ this → style engine output direct, no warning
CONFIDENCE_LOW = 0.30  # < this → LLM fallback recommended

# Interpolation: number of top examples to blend
TOP_K_BLEND = 3

# Candidate pool: how many CLIP-similar examples to fetch before re-scoring
CANDIDATE_POOL = 20

# Minimum training examples required for style engine to activate
MIN_TRAINING_EXAMPLES = 5

# ---------------------------------------------------------------------------
# Distance → similarity conversion
# ---------------------------------------------------------------------------


def _clip_distance_to_similarity(distance: float) -> float:
    """Convert ChromaDB L2 distance (squared) to cosine similarity proxy.

    ChromaDB uses squared L2 distance for normalized vectors.
    For unit vectors: ||a - b||² = 2 - 2·cos(θ)  →  cos(θ) = 1 - d/2
    """
    return max(0.0, min(1.0, 1.0 - distance / 2.0))


# ---------------------------------------------------------------------------
# Exposure proximity scoring
# ---------------------------------------------------------------------------


def _exposure_proximity(
    query: dict[str, float],
    candidate: dict[str, float],
) -> float:
    """Score how closely the candidate's exposure state matches the query.

    Compares luminance mean and contrast.  Returns 0..1 where 1 = identical.
    """
    deltas: list[float] = []

    for key in ("exp_luminance_mean", "exp_contrast", "exp_warmth_proxy"):
        q_val = query.get(key)
        c_val = candidate.get(key)
        if q_val is not None and c_val is not None:
            deltas.append(abs(float(q_val) - float(c_val)))

    if not deltas:
        return 0.5  # neutral when no data available

    # Average absolute delta, scaled so delta=0.3 → score ≈ 0
    mean_delta = sum(deltas) / len(deltas)
    return max(0.0, 1.0 - mean_delta / 0.3)


# ---------------------------------------------------------------------------
# Scene-type tag overlap scoring
# ---------------------------------------------------------------------------


def _scene_overlap(
    query_tags: list[str],
    candidate_tags: list[str],
) -> float:
    """Jaccard-style overlap between two sets of scene tags.

    Returns 0..1.  When both sets are empty, returns 0.5 (neutral).
    """
    q_set = set(query_tags or [])
    c_set = set(candidate_tags or [])
    if not q_set and not c_set:
        return 0.5
    if not q_set or not c_set:
        return 0.3  # one side has no tags — mild penalise
    intersection = q_set & c_set
    union = q_set | c_set
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Time-of-day proximity scoring
# ---------------------------------------------------------------------------

_TOD_ORDER = ["dawn", "morning", "afternoon", "evening", "night", "unknown"]


def _tod_proximity(query_tod: str, candidate_tod: str) -> float:
    """Score proximity of time-of-day buckets.  Adjacent buckets score 0.5, same = 1.0."""
    if query_tod == "unknown" or candidate_tod == "unknown":
        return 0.5
    if query_tod == candidate_tod:
        return 1.0
    try:
        q_idx = _TOD_ORDER.index(query_tod)
        c_idx = _TOD_ORDER.index(candidate_tod)
        diff = abs(q_idx - c_idx)
        # Circular distance over 5 slots (dawn → night wrap)
        diff = min(diff, 5 - diff)
        return max(0.0, 1.0 - diff * 0.35)
    except ValueError:
        return 0.5


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


def calculate_composite_score(
    clip_sim: float,
    query_exposure: dict[str, float],
    candidate: dict[str, Any],
    query_scene_tags: list[str],
    query_tod: str,
) -> float:
    """Compute weighted composite match score for one candidate."""
    exp_score = _exposure_proximity(
        query_exposure,
        {
            k: candidate.get(k, 0.0)
            for k in (
                "exp_luminance_mean",
                "exp_contrast",
                "exp_warmth_proxy",
            )
        },
    )
    scene_score = _scene_overlap(query_scene_tags, candidate.get("scene_tags", []))
    tod_score = _tod_proximity(
        query_tod, candidate.get("time_of_day_bucket", "unknown")
    )

    return (
        WEIGHT_CLIP * clip_sim
        + WEIGHT_EXPOSURE * exp_score
        + WEIGHT_SCENE * scene_score
        + WEIGHT_TIME_OF_DAY * tod_score
    )


# ---------------------------------------------------------------------------
# Recipe interpolation
# ---------------------------------------------------------------------------


def _circular_mean(angles_and_weights: list[tuple[float, float]]) -> float:
    sum_sin = 0.0
    sum_cos = 0.0
    total_w = sum(w for _, w in angles_and_weights)
    if total_w <= 0:
        return 0.0
    for angle, weight in angles_and_weights:
        rad = math.radians(angle)
        sum_sin += (weight / total_w) * math.sin(rad)
        sum_cos += (weight / total_w) * math.cos(rad)
    return round((math.degrees(math.atan2(sum_sin, sum_cos)) + 360) % 360, 1)


def _interpolate_point_curve(
    curves_and_weights: list[tuple[list[float], float]],
) -> list[float]:
    x_eval = np.linspace(0, 255, 16)  # 16 control points
    y_sum = np.zeros_like(x_eval)
    total_w = sum(w for _, w in curves_and_weights)
    if total_w <= 0:
        return [0.0, 0.0, 255.0, 255.0]
    for curve, weight in curves_and_weights:
        xs = curve[::2]
        ys = curve[1::2]
        y_eval = np.interp(x_eval, xs, ys)
        y_sum += (weight / total_w) * y_eval

    # Flatten back to [x1, y1, x2, y2...]
    result = []
    for x, y in zip(x_eval, y_sum):
        result.extend([round(float(x), 1), round(float(y), 1)])
    return result


def _prune_neutral_tools(recipe: dict[str, Any]) -> dict[str, Any]:
    # Prune HSL if all values are 0
    if "hsl" in recipe:
        all_zero = True
        for color, vals in recipe["hsl"].items():
            if any(
                abs(v) > 0.1
                for k, v in vals.items()
                if k in ("saturation", "luminance", "hue")
            ):
                all_zero = False
                break
        if all_zero:
            del recipe["hsl"]

    # Prune Color Grading if all saturations are 0 and no blending/balance
    if "color_grading" in recipe:
        cg = recipe["color_grading"]
        sat_zero = all(
            abs(cg.get(r, {}).get("saturation", 0)) < 0.1
            for r in ["shadows", "midtones", "highlights", "global"]
        )
        if (
            sat_zero
            and abs(cg.get("balance", 0)) < 0.1
            and abs(cg.get("blending", 50) - 50) < 0.1
        ):
            del recipe["color_grading"]

    # Prune linear point curves
    if "tone_curve" in recipe and "point_curve" in recipe["tone_curve"]:
        pc = recipe["tone_curve"]["point_curve"]
        all_linear = True
        for chan in ["master", "red", "green", "blue"]:
            curve = pc.get(chan)
            if curve:
                # evaluate curve at 0, 128, 255 to see if it's strictly y=x
                xs = curve[::2]
                ys = curve[1::2]
                if any(abs(x - y) > 1.0 for x, y in zip(xs, ys)):
                    all_linear = False
                    break
        if all_linear:
            del recipe["tone_curve"]["point_curve"]
            if not recipe["tone_curve"]:
                del recipe["tone_curve"]

    return recipe


def interpolate_recipes(
    winners: list[tuple[dict[str, Any], float]],
) -> dict[str, Any]:
    """Weighted blend of canonical develop settings from the top-K winners.

    Args:
        winners: List of (example_dict, composite_score) pairs.

    Returns:
        Interpolated canonical recipe dict.
    """
    total_weight = sum(score for _, score in winners)
    if total_weight <= 0:
        return {}

    # Gather data across winners
    num_fields = {}
    hsl_fields = {}
    cg_fields = {}
    pc_fields = {}

    for example, score in winners:
        weight = score / total_weight
        canonical = example.get("canonical_settings", {})
        if not isinstance(canonical, dict):
            try:
                canonical = json.loads(canonical)
            except Exception:
                canonical = {}
        for key, value in canonical.items():
            if isinstance(value, (int, float)):
                num_fields[key] = num_fields.get(key, 0.0) + weight * float(value)
            elif key == "hsl" and isinstance(value, dict):
                for color, vals in value.items():
                    if color not in hsl_fields:
                        hsl_fields[color] = {"hues": [], "sat": 0.0, "lum": 0.0}
                    if "hue" in vals:
                        hsl_fields[color]["hues"].append((float(vals["hue"]), weight))
                    hsl_fields[color]["sat"] += weight * float(
                        vals.get("saturation", 0)
                    )
                    hsl_fields[color]["lum"] += weight * float(vals.get("luminance", 0))
            elif key == "color_grading" and isinstance(value, dict):
                for region, vals in value.items():
                    if isinstance(vals, dict):
                        if region not in cg_fields:
                            cg_fields[region] = {"hues": [], "sat": 0.0, "lum": 0.0}
                        if "hue" in vals:
                            cg_fields[region]["hues"].append(
                                (float(vals["hue"]), weight)
                            )
                        cg_fields[region]["sat"] += weight * float(
                            vals.get("saturation", 0)
                        )
                        if "luminance" in vals:
                            cg_fields[region]["lum"] += weight * float(
                                vals["luminance"]
                            )
                    else:
                        # global balance/blending
                        cg_fields[region] = cg_fields.get(region, 0.0) + weight * float(
                            vals
                        )
            elif (
                key == "tone_curve"
                and isinstance(value, dict)
                and "point_curve" in value
            ):
                pc = value["point_curve"]
                for chan, curve in pc.items():
                    if chan not in pc_fields:
                        pc_fields[chan] = []
                    pc_fields[chan].append((curve, weight))
            elif key == "crop" and isinstance(value, dict):
                for crop_key, crop_val in value.items():
                    if isinstance(crop_val, (int, float)):
                        composite_key = f"crop_{crop_key}"
                        num_fields[composite_key] = num_fields.get(
                            composite_key, 0.0
                        ) + weight * float(crop_val)
            elif key == "white_balance":
                is_custom = 1.0 if str(value).lower() != "as shot" else 0.0
                num_fields["white_balance_is_custom"] = (
                    num_fields.get("white_balance_is_custom", 0.0) + weight * is_custom
                )

    blended: dict[str, Any] = {k: round(v, 1) for k, v in num_fields.items()}

    if hsl_fields:
        blended["hsl"] = {}
        for color, data in hsl_fields.items():
            blended["hsl"][color] = {
                "hue": _circular_mean(data["hues"]),
                "saturation": round(data["sat"], 1),
                "luminance": round(data["lum"], 1),
            }

    if cg_fields:
        blended["color_grading"] = {}
        for region, data in cg_fields.items():
            if isinstance(data, dict):  # shadow, midtone, highlight, global
                blended["color_grading"][region] = {
                    "hue": _circular_mean(data["hues"]),
                    "saturation": round(data["sat"], 1),
                }
                if region != "global":
                    blended["color_grading"][region]["luminance"] = round(
                        data["lum"], 1
                    )
            else:
                blended["color_grading"][region] = round(data, 1)

    if pc_fields:
        blended["tone_curve"] = {"point_curve": {}}
        for chan, curves in pc_fields.items():
            blended["tone_curve"]["point_curve"][chan] = _interpolate_point_curve(
                curves
            )

    # Reconstruct crop and enforce original aspect ratio constraint
    crop_keys = [k for k in blended if k.startswith("crop_")]
    if crop_keys:
        crop = {}
        for k in crop_keys:
            crop[k.replace("crop_", "")] = blended.pop(k)

        left = crop.get("left")
        right = crop.get("right")
        top = crop.get("top")
        bottom = crop.get("bottom")

        if (
            all(v is not None for v in (left, right, top, bottom))
            and right > left
            and bottom > top
        ):
            crop_width = right - left
            crop_height = bottom - top

            if abs(crop_width - crop_height) <= 0.02:
                avg_dim = (crop_width + crop_height) / 2.0
                center_x = (left + right) / 2.0
                center_y = (top + bottom) / 2.0
                crop["left"] = max(0.0, center_x - avg_dim / 2.0)
                crop["right"] = min(1.0, center_x + avg_dim / 2.0)
                crop["top"] = max(0.0, center_y - avg_dim / 2.0)
                crop["bottom"] = min(1.0, center_y + avg_dim / 2.0)
                if "angle" in crop:
                    crop["angle"] = max(-45.0, min(45.0, crop["angle"]))
                blended["crop"] = crop
        elif "angle" in crop and not any(
            k in crop for k in ("left", "right", "top", "bottom")
        ):
            # It's just a rotation
            crop["angle"] = max(-45.0, min(45.0, crop["angle"]))
            blended["crop"] = crop

    if "white_balance_is_custom" in blended:
        is_custom = blended.pop("white_balance_is_custom")
        blended["white_balance"] = "Custom" if is_custom >= 0.7 else "As Shot"

    return _prune_neutral_tools(blended)


# ---------------------------------------------------------------------------
# RAW-adaptive exposure compensation
# ---------------------------------------------------------------------------


def adaptive_compensation(
    recipe: dict[str, Any],
    query_exposure: dict[str, float],
    winners: list[tuple[dict[str, Any], float]],
) -> dict[str, Any]:
    """Adjust the interpolated recipe to compensate for exposure differences.

    Example: if the new photo is 0.2 EV brighter than the training examples,
    reduce exposure to reach an equivalent tonal foundation.
    """
    if not winners:
        return recipe

    # Weighted average training luminance
    total_weight = sum(score for _, score in winners)
    if total_weight <= 0:
        return recipe

    avg_train_lum = sum(
        float(ex.get("exp_luminance_mean", 0.5)) * (score / total_weight)
        for ex, score in winners
    )
    avg_train_contrast = sum(
        float(ex.get("exp_contrast", 0.5)) * (score / total_weight)
        for ex, score in winners
    )

    query_lum = float(query_exposure.get("exp_luminance_mean", 0.5))
    query_contrast = float(query_exposure.get("exp_contrast", 0.5))

    # Luminance delta → small exposure correction
    lum_delta = query_lum - avg_train_lum
    # Scale: 0.1 luminance unit ≈ 0.5 EV
    exposure_correction = (
        -lum_delta * 5.0
    )  # subtract because brighter photo needs less exposure push
    exposure_correction = max(-1.5, min(1.5, exposure_correction))

    # Contrast delta → small contrast correction
    contrast_delta = query_contrast - avg_train_contrast
    contrast_correction = -contrast_delta * 20.0
    contrast_correction = max(-15.0, min(15.0, contrast_correction))

    # Apply corrections additively on top of interpolated recipe
    result = dict(recipe)
    if abs(exposure_correction) > 0.05:
        current_exp = result.get("exposure", 0.0)
        result["exposure"] = round(current_exp + exposure_correction, 2)
        logger.debug(
            "Style engine adaptive: lum_delta=%.3f → exposure correction %+.2f",
            lum_delta,
            exposure_correction,
        )
    if abs(contrast_correction) > 1.0:
        current_con = result.get("contrast", 0.0)
        result["contrast"] = round(current_con + contrast_correction, 1)
        logger.debug(
            "Style engine adaptive: contrast_delta=%.3f → contrast correction %+.1f",
            contrast_delta,
            contrast_correction,
        )

    return result


# ---------------------------------------------------------------------------
# Canonical recipe → LLM-compatible edit recipe dict
# ---------------------------------------------------------------------------

# Reverse mapping from canonical key → edit_recipe global fields
_CANONICAL_TO_RECIPE_FIELDS = {
    "exposure": "exposure",
    "contrast": "contrast",
    "highlights": "highlights",
    "shadows": "shadows",
    "whites": "whites",
    "blacks": "blacks",
    "texture": "texture",
    "clarity": "clarity",
    "dehaze": "dehaze",
    "vibrance": "vibrance",
    "saturation": "saturation",
    "sharpening": "sharpening",
    "noise_reduction": "noise_reduction",
    "color_noise_reduction": "color_noise_reduction",
    "vignette": "vignette",
    "grain": "grain",
}

# Parametric tone curve keys extracted from training
_TONE_CURVE_KEYS = {
    "tone_curve_highlights": "highlights",
    "tone_curve_lights": "lights",
    "tone_curve_darks": "darks",
    "tone_curve_shadows": "shadows",
}


def _canonical_to_edit_recipe(
    canonical: dict[str, Any], summary: str = ""
) -> dict[str, Any]:
    """Convert canonical key/value dict to the edit recipe format used by the plugin."""
    global_settings: dict[str, Any] = {}

    for canon_key, recipe_key in _CANONICAL_TO_RECIPE_FIELDS.items():
        if canon_key in canonical:
            global_settings[recipe_key] = canonical[canon_key]

    if "hsl" in canonical:
        global_settings["hsl"] = canonical["hsl"]
    if "color_grading" in canonical:
        global_settings["color_grading"] = canonical["color_grading"]

    # Build tone_curve
    tone_curve: dict[str, Any] = canonical.get("tone_curve", {})
    for canon_key, tc_key in _TONE_CURVE_KEYS.items():
        if canon_key in canonical:
            tone_curve[tc_key] = canonical[canon_key]
    if tone_curve:
        global_settings["tone_curve"] = tone_curve

    return {
        "summary": summary or "Style-matched edit by StyleAI Style Engine",
        "global": global_settings,
        "masks": [],
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


class StyleEngineResult:
    """Result from the style engine."""

    def __init__(
        self,
        recipe: dict[str, Any],
        confidence: float,
        matched_count: int,
        engine: str = "style",
        warning: str | None = None,
        error: str | None = None,
        matched_filenames: list[str] | None = None,
    ) -> None:
        self.recipe = recipe
        self.confidence = confidence
        self.matched_count = matched_count
        self.engine = engine
        self.warning = warning
        self.error = error
        self.matched_filenames = matched_filenames or []


def _finalize_recipe(
    recipe: dict[str, Any],
    query_exposure: dict[str, float],
    current_settings: dict[str, Any] | None,
    style_strength: float | None,
) -> dict[str, Any]:
    """Apply style strength scaling and auto white balance overrides additively."""
    # Use Lightroom absolute defaults as the baseline for computing the style's delta
    # so the slider is stateless and works predictably even if the photo is already edited.
    lr_defaults = {
        "exposure": 0.0,
        "contrast": 0.0,
        "highlights": 0.0,
        "shadows": 0.0,
        "whites": 0.0,
        "blacks": 0.0,
        "texture": 0.0,
        "clarity": 0.0,
        "dehaze": 0.0,
        "vibrance": 0.0,
        "saturation": 0.0,
        "sharpening": 40.0,
        "noise_reduction": 0.0,
        "color_noise_reduction": 25.0,
        "vignette": 0.0,
        "grain": 0.0,
    }

    # Map Lightroom SDK keys to our canonical schema to extract current slider positions
    lr_to_canonical = {
        "Exposure2012": "exposure",
        "Contrast2012": "contrast",
        "Highlights2012": "highlights",
        "Shadows2012": "shadows",
        "Whites2012": "whites",
        "Blacks2012": "blacks",
        "Texture": "texture",
        "Clarity2012": "clarity",
        "Dehaze": "dehaze",
        "Vibrance": "vibrance",
        "Saturation": "saturation",
        "Sharpness": "sharpening",
        "LuminanceSmoothing": "noise_reduction",
        "ColorNoiseReduction": "color_noise_reduction",
        "PostCropVignetteAmount": "vignette",
        "GrainAmount": "grain",
    }
    canonical_current = {}
    if current_settings:
        for lr_key, canon_key in lr_to_canonical.items():
            if lr_key in current_settings:
                canonical_current[canon_key] = current_settings[lr_key]

    global_settings = recipe.get("global", {})
    for key, target_val in list(global_settings.items()):
        if isinstance(target_val, (int, float)) and not isinstance(target_val, bool):
            try:
                # The style targets are absolute values relative to LR's default zero points.
                baseline = lr_defaults.get(key, 0.0)
                style_delta = float(target_val) - baseline

                # Scale the intended delta by the user's strength preference
                scaled_delta = style_delta * (
                    style_strength if style_strength is not None else 1.0
                )

                # Additively apply the delta to the photo's actual current setting
                current_val = float(canonical_current.get(key, baseline))
                interpolated = current_val + scaled_delta

                global_settings[key] = round(interpolated, 2)
            except (ValueError, TypeError):
                pass

    warmth_proxy = query_exposure.get("exp_warmth_proxy", 0.5)
    if warmth_proxy < 0.2 or warmth_proxy > 0.8:
        recipe["white_balance"] = "Auto"
        logger.info(
            "Style engine detected extreme color cast (warmth_proxy=%.3f), engaging Auto white balance",
            warmth_proxy,
        )

    return recipe


def generate_style_edit(
    photo_id: str,
    image_bytes: bytes,
    *,
    focal_length: float | None = None,
    capture_time_unix: float | None = None,
    clip_embedding: list[float] | None = None,
    camera_make: str | None = None,
    camera_model: str | None = None,
    camera_profile: str | None = None,
    user_keywords: list[str] | None = None,
    min_confidence: float = CONFIDENCE_LOW,
    current_settings: dict[str, Any] | None = None,
    style_strength: float | None = None,
    style_override: str | None = None,
    do_not_clip: bool = True,
) -> StyleEngineResult:
    """Generate a style-matched edit recipe without an LLM.

    Args:
        photo_id:           Stable photo identifier.
        image_bytes:        JPEG/PNG preview for exposure metric extraction.
        focal_length:       Focal length in mm from EXIF (optional).
        capture_time_unix:  Capture Unix timestamp (optional).
        clip_embedding:     Pre-computed CLIP embedding (re-used from index if available).
        camera_make:        Camera manufacturer string (optional).
        camera_model:       Camera model string (optional).
        camera_profile:     Camera profile name (optional).
        user_keywords:      User-provided keywords for style matching (optional).
        min_confidence:     Below this confidence the caller should fall back to LLM.

    Returns:
        StyleEngineResult with recipe, confidence score, and metadata.
    """
    training_count = training_service.get_training_count()
    if training_count < MIN_TRAINING_EXAMPLES:
        return StyleEngineResult(
            recipe={},
            confidence=0.0,
            matched_count=0,
            engine="none",
            warning=(
                f"Style engine inactive: only {training_count} training example(s) available "
                f"(minimum {MIN_TRAINING_EXAMPLES} required). Please save more AI training examples."
            ),
        )

    # -----------------------------------------------------------------------
    # Step 1: Compute query-side features
    # -----------------------------------------------------------------------
    query_exposure = training_service.compute_exposure_metrics(image_bytes)
    query_scene_tags = training_service.compute_scene_tags(clip_embedding)
    query_tod = training_service.time_of_day_bucket(capture_time_unix)
    focal_bucket = training_service.focal_length_bucket(focal_length)

    logger.info(
        "Style engine query: photo_id=%s lum=%.3f contrast=%.3f tags=%s tod=%s focal=%s",
        photo_id,
        query_exposure.get("exp_luminance_mean", -1),
        query_exposure.get("exp_contrast", -1),
        query_scene_tags,
        query_tod,
        focal_bucket,
    )

    # -----------------------------------------------------------------------
    # Step 2: Try style catalog match first (structured style groups)
    # -----------------------------------------------------------------------
    from services import style_catalog as style_catalog_service

    style_matches = []
    if style_override:
        override_style = style_catalog_service.get_style(style_override)
        if override_style:
            logger.info("Using explicit style_override=%s", style_override)
            style_matches = [(override_style, 1.0)]
        else:
            logger.warning("Requested style_override=%s not found.", style_override)

    if not style_matches:
        try:
            style_matches = style_catalog_service.find_matching_styles(
                camera_make=camera_make,
                camera_model=camera_model,
                scene_tags=query_scene_tags,
                exposure_metrics=query_exposure,
                camera_profile=camera_profile,
                user_keywords=user_keywords,
                top_k=3,
            )
        except Exception as exc:
            logger.debug("Style catalog lookup failed: %s", exc)

    if style_matches:
        best_style, best_confidence = style_matches[0]
        if best_confidence >= CONFIDENCE_GOOD:
            # Check for Predictive ML Model first
            from services import predictive_engine

            # Combine query metadata for the predictor
            query_metadata = dict(query_exposure)
            # Add any other metadata needed by the predictive engine (e.g., tags)
            # Actually, predictive_engine just needs exposure metrics currently

            ml_prediction = None
            if clip_embedding:
                ml_prediction = predictive_engine.predict_edits(
                    best_style["style_id"],
                    clip_embedding,
                    query_metadata,
                    do_not_clip=do_not_clip,
                )

            if ml_prediction:
                ml_tier = ml_prediction.pop("_ml_tier", "unknown")
                summary = (
                    f"Style: {best_style.get('camera_profile', 'default')} • {best_style.get('style_name', 'Unknown')} "
                    f"[{ml_tier.upper()}] (confidence {best_confidence:.0%})"
                )
                recipe = _canonical_to_edit_recipe(ml_prediction, summary=summary)
                recipe = _finalize_recipe(
                    recipe, query_exposure, current_settings, style_strength
                )
                logger.info(
                    "Style engine catalog ML match: photo_id=%s style=%s tier=%s confidence=%.3f",
                    photo_id,
                    best_style.get("style_name", "?"),
                    ml_tier,
                    best_confidence,
                )
                return StyleEngineResult(
                    recipe=recipe,
                    confidence=round(best_confidence, 3),
                    matched_count=best_style.get("example_count", 0),
                    engine=ml_tier,
                    matched_filenames=[best_style.get("style_name", "")],
                )
            else:
                # Check if we should have had a predictive model
                example_count = best_style.get("example_count", 0)
                if example_count >= predictive_engine.MIN_PCA_EXAMPLES:
                    err_msg = f"Predictive ML engine failed to run for style '{best_style.get('style_name', 'Unknown')}' despite having {example_count} training examples (model file missing or inference error)."
                    logger.error(err_msg)
                    return StyleEngineResult(
                        recipe={},
                        confidence=round(best_confidence, 3),
                        matched_count=example_count,
                        engine="error",
                        matched_filenames=[best_style.get("style_name", "")],
                        error=err_msg,
                    )

                # High confidence: use style recipe directly (fallback to KNN averaging)
                recipe_settings = style_catalog_service.get_style_recipe(
                    best_style["style_id"]
                )
                if recipe_settings:
                    summary = (
                        f"Style: {best_style.get('camera_profile', 'default')} • {best_style.get('style_name', 'Unknown')} "
                        f"(confidence {best_confidence:.0%})"
                    )
                    recipe = _canonical_to_edit_recipe(recipe_settings, summary=summary)
                    recipe = _finalize_recipe(
                        recipe, query_exposure, current_settings, style_strength
                    )
                    logger.info(
                        "Style engine catalog match: photo_id=%s style=%s confidence=%.3f",
                        photo_id,
                        best_style.get("style_name", "?"),
                        best_confidence,
                    )
                    return StyleEngineResult(
                        recipe=recipe,
                        confidence=round(best_confidence, 3),
                        matched_count=best_style.get("example_count", 0),
                        engine="style_catalog",
                        matched_filenames=[best_style.get("style_name", "")],
                    )
        elif best_confidence >= CONFIDENCE_LOW and len(style_matches) >= 2:
            # Medium confidence: blend top 2 styles
            style1, conf1 = style_matches[0]
            style2, conf2 = style_matches[1]
            total_conf = conf1 + conf2
            if total_conf > 0:
                w1 = conf1 / total_conf
                w2 = conf2 / total_conf
                recipe1 = style_catalog_service.get_style_recipe(style1["style_id"])
                recipe2 = style_catalog_service.get_style_recipe(style2["style_id"])
                if recipe1 and recipe2:
                    blended: dict[str, float] = {}
                    all_keys = set(recipe1.keys()) | set(recipe2.keys())
                    for key in all_keys:
                        v1 = recipe1.get(key, 0.0)
                        v2 = recipe2.get(key, 0.0)
                        blended[key] = round(w1 * v1 + w2 * v2, 4)
                    summary = (
                        f"Blend: {style1.get('camera_profile', 'default')} • {style1.get('style_name', '?')} + "
                        f"{style2.get('camera_profile', 'default')} • {style2.get('style_name', '?')} (confidence {best_confidence:.0%})"
                    )
                    recipe = _canonical_to_edit_recipe(blended, summary=summary)
                    recipe = _finalize_recipe(
                        recipe, query_exposure, current_settings, style_strength
                    )
                    logger.info(
                        "Style engine catalog blend: photo_id=%s styles=%s+%s confidence=%.3f",
                        photo_id,
                        style1.get("style_name", "?"),
                        style2.get("style_name", "?"),
                        best_confidence,
                    )
                    return StyleEngineResult(
                        recipe=recipe,
                        confidence=round(best_confidence, 3),
                        matched_count=style1.get("example_count", 0)
                        + style2.get("example_count", 0),
                        engine="style_catalog_blend",
                        matched_filenames=[
                            style1.get("style_name", ""),
                            style2.get("style_name", ""),
                        ],
                    )

    # -----------------------------------------------------------------------
    # Step 3: Fall back to per-example interpolation (legacy path)
    # -----------------------------------------------------------------------
    if clip_embedding is not None:
        candidates = training_service.query_similar_training_examples(
            clip_embedding,
            n_results=min(CANDIDATE_POOL, training_count),
        )
    else:
        # No embedding available – fetch recent examples as fallback
        all_examples = training_service.list_training_examples()
        candidates = all_examples[:CANDIDATE_POOL]
        for c in candidates:
            c["distance"] = 0.5  # neutral distance when embedding unavailable

    if not candidates:
        return StyleEngineResult(
            recipe={},
            confidence=0.0,
            matched_count=0,
            engine="none",
            warning="No training examples could be retrieved from the database.",
        )

    # -----------------------------------------------------------------------
    # Step 4: Re-score candidates with composite criteria
    # -----------------------------------------------------------------------
    scored: list[tuple[dict[str, Any], float]] = []
    for candidate in candidates:
        clip_sim = _clip_distance_to_similarity(candidate.get("distance", 1.0))
        score = calculate_composite_score(
            clip_sim=clip_sim,
            query_exposure=query_exposure,
            candidate=candidate,
            query_scene_tags=query_scene_tags,
            query_tod=query_tod,
        )
        scored.append((candidate, score))

    # Sort descending by composite score
    scored.sort(key=lambda x: x[1], reverse=True)

    # -----------------------------------------------------------------------
    # Step 5: Compute confidence from best candidate scores
    # -----------------------------------------------------------------------
    best_score = scored[0][1] if scored else 0.0
    confidence = round(best_score, 3)

    # -----------------------------------------------------------------------
    # Step 6: Interpolate the top-K winners
    # -----------------------------------------------------------------------
    winners = scored[:TOP_K_BLEND]
    matched_filenames = [
        ex.get("filename") or ex.get("label") or ex.get("photo_id", "")
        for ex, _ in winners
    ]

    blended_recipe = interpolate_recipes(winners)

    # -----------------------------------------------------------------------
    # Step 7: RAW-adaptive compensation
    # -----------------------------------------------------------------------
    blended_recipe = adaptive_compensation(blended_recipe, query_exposure, winners)

    # -----------------------------------------------------------------------
    # Step 8: Build summary from top example labels
    # -----------------------------------------------------------------------
    labels = list(
        set(
            ex.get("label") or ex.get("summary") or ""
            for ex, _ in winners
            if (ex.get("label") or ex.get("summary"))
        )
    )
    summary_parts = []
    if labels:
        summary_parts.append("Style: " + " / ".join(labels[:2]))
    summary_parts.append(
        f"Matched {len(winners)} of {training_count} examples (confidence {confidence:.0%})"
    )
    summary = " — ".join(summary_parts)

    recipe = _canonical_to_edit_recipe(blended_recipe, summary=summary)
    recipe = _finalize_recipe(recipe, query_exposure, current_settings, style_strength)

    # -----------------------------------------------------------------------
    # Step 9: Attach appropriate warning for low confidence
    # -----------------------------------------------------------------------
    warning: str | None = None
    if confidence < CONFIDENCE_LOW:
        warning = (
            f"Low style match confidence ({confidence:.0%}). "
            "Results may not match your editing style precisely. "
            "Consider adding more training examples for this type of photo."
        )
    elif confidence < CONFIDENCE_GOOD:
        warning = (
            f"Moderate style match confidence ({confidence:.0%}). "
            "Review the result before applying."
        )

    logger.info(
        "Style engine result: photo_id=%s confidence=%.3f matched=%d winners=%s",
        photo_id,
        confidence,
        len(winners),
        [f.get("filename", "?") for f, _ in winners],
    )

    return StyleEngineResult(
        recipe=recipe,
        confidence=confidence,
        matched_count=len(winners),
        engine="style",
        warning=warning,
        matched_filenames=matched_filenames,
    )
