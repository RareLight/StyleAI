"""
Edit style training service.

Manages the `edit_training` ChromaDB collection that stores the user's own
Lightroom develop settings as few-shot examples.  When the AI generates a new
edit recipe it queries this collection by CLIP visual similarity and injects
the closest matches as style examples into the LLM prompt.

Enhanced with source exposure metrics, standardized EXIF evidence, and an
authoritative readiness contract for the editing-policy UI.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np

from config import logger

try:
    from chromadb.errors import InternalError as _ChromaInternalError
except Exception:
    _ChromaInternalError = Exception

# Lazy ChromaDB globals – initialized on first use.
_chroma_client = None
_training_collection = None

COLLECTION_NAME = "edit_training"
TRAINING_PAGE_SIZE = 1000
EMBEDDING_DIM = 1152

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_initialized() -> None:
    global _chroma_client, _training_collection
    if _training_collection is not None:
        return

    import config

    if not config.DB_PATH:
        logger.debug("edit_training initialization skipped: DB_PATH not set yet.")
        return

    import chromadb
    from chromadb.config import Settings

    logger.info(
        "Initializing edit_training ChromaDB collection (lazy at %s)...", config.DB_PATH
    )
    _chroma_client = chromadb.PersistentClient(
        path=config.DB_PATH,
        settings=Settings(anonymized_telemetry=False),
    )
    _training_collection = _chroma_client.get_or_create_collection(
        name=COLLECTION_NAME, embedding_function=None
    )
    logger.info("Initialized edit_training collection.")


def unload_collections() -> None:
    """Release the training Chroma client before a catalog database restore."""
    global _chroma_client, _training_collection
    _training_collection = None
    _chroma_client = None


def _iter_training_pages(include):
    """Yield bounded Chroma pages without a fixed collection-size ceiling."""
    if _training_collection is None:
        return
    offset = 0
    while True:
        page = _training_collection.get(
            include=include, limit=TRAINING_PAGE_SIZE, offset=offset
        )
        ids = page.get("ids")
        if ids is None:
            ids = []
        if not ids:
            break
        yield page
        offset += len(ids)
        if len(ids) < TRAINING_PAGE_SIZE:
            break


def _safe_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


# ---------------------------------------------------------------------------
# Image analysis at ~3MP (2048px long edge) for source exposure evidence
# ---------------------------------------------------------------------------

_THUMBNAIL_LONG_EDGE = 2048  # ~3MP sweet spot for analysis speed vs accuracy


def _load_thumbnail(image_bytes: bytes) -> tuple[np.ndarray, tuple[int, int]]:
    """Load image and downscale to ~3MP for efficient analysis.

    Returns (rgb_array, original_size).
    """
    from PIL import Image
    import io

    with Image.open(io.BytesIO(image_bytes)) as source_image:
        orig_size = source_image.size
        source_image.thumbnail(
            (_THUMBNAIL_LONG_EDGE, _THUMBNAIL_LONG_EDGE), Image.Resampling.LANCZOS
        )
        image = source_image.convert("RGB")
    try:
        rgb = np.asarray(image, dtype=np.float32) / 255.0
        return rgb, orig_size
    finally:
        image.close()


# ---------------------------------------------------------------------------
# Exposure metrics (computed from downscaled thumbnail)
# ---------------------------------------------------------------------------


def compute_exposure_metrics(image_bytes: bytes) -> dict[str, float]:
    """Compute proxy RAW exposure characteristics from an image.

    Returns a dict of float metrics (all 0..1 normalized) suitable for
    storage as ChromaDB metadata and multi-criteria matching.
    """
    try:
        rgb, _ = _load_thumbnail(image_bytes)
        gray = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]

        lum_mean = float(np.mean(gray))
        lum_std = float(np.std(gray))

        # Basic Highlights/Shadows
        highlight_ratio = float(np.mean(gray >= 0.92))
        shadow_ratio = float(np.mean(gray <= 0.08))
        midtone_ratio = float(np.mean((gray > 0.2) & (gray < 0.8)))

        # 5-Zone Luminance Mapping
        zone_deep_shadows = float(np.mean(gray <= 0.1))
        zone_shadows = float(np.mean((gray > 0.1) & (gray <= 0.35)))
        zone_midtones = float(np.mean((gray > 0.35) & (gray <= 0.65)))
        zone_highlights = float(np.mean((gray > 0.65) & (gray <= 0.9)))
        zone_bright_highlights = float(np.mean(gray > 0.9))

        # Colorfulness: mean chroma in rg-yb space
        rg = np.abs(rgb[:, :, 0] - rgb[:, :, 1])
        yb = np.abs(0.5 * (rgb[:, :, 0] + rgb[:, :, 1]) - rgb[:, :, 2])
        colorfulness = _safe_unit(float(np.mean(np.sqrt(rg**2 + yb**2))) / 0.35)

        # Warm/cool proxy: ratio of red channel mean to blue channel mean in highlights
        highlight_mask = gray > 0.7
        if np.any(highlight_mask):
            r_mean = float(np.mean(rgb[:, :, 0][highlight_mask]))
            b_mean = float(np.mean(rgb[:, :, 2][highlight_mask]))
            warmth_proxy = _safe_unit((r_mean - b_mean + 1.0) / 2.0)
        else:
            warmth_proxy = 0.5

        # Contrast via Michelson
        lum_max = float(np.percentile(gray, 97))
        lum_min = float(np.percentile(gray, 3))
        if (lum_max + lum_min) > 0:
            contrast = _safe_unit((lum_max - lum_min) / (lum_max + lum_min))
        else:
            contrast = 0.0

        # Headroom (1st and 99th percentiles)
        shadow_headroom = float(np.percentile(gray, 1))
        highlight_headroom = float(np.percentile(gray, 99))

        return {
            "exp_luminance_mean": round(lum_mean, 4),
            "exp_luminance_std": round(lum_std, 4),
            "exp_highlight_ratio": round(highlight_ratio, 4),
            "exp_shadow_ratio": round(shadow_ratio, 4),
            "exp_midtone_ratio": round(midtone_ratio, 4),
            "exp_colorfulness": round(colorfulness, 4),
            "exp_warmth_proxy": round(warmth_proxy, 4),
            "exp_contrast": round(contrast, 4),
            "zone_deep_shadows": round(zone_deep_shadows, 4),
            "zone_shadows": round(zone_shadows, 4),
            "zone_midtones": round(zone_midtones, 4),
            "zone_highlights": round(zone_highlights, 4),
            "zone_bright_highlights": round(zone_bright_highlights, 4),
            "shadow_headroom": round(shadow_headroom, 4),
            "highlight_headroom": round(highlight_headroom, 4),
        }
    except Exception as exc:
        logger.warning("compute_exposure_metrics failed: %s", exc)
        return {
            "exp_luminance_mean": 0.5,
            "exp_luminance_std": 0.0,
            "exp_highlight_ratio": 0.0,
            "exp_shadow_ratio": 0.0,
            "exp_midtone_ratio": 0.0,
            "exp_colorfulness": 0.0,
            "exp_warmth_proxy": 0.5,
            "exp_contrast": 0.0,
            "zone_deep_shadows": 0.0,
            "zone_shadows": 0.0,
            "zone_midtones": 0.0,
            "zone_highlights": 0.0,
            "zone_bright_highlights": 0.0,
            "shadow_headroom": 0.0,
            "highlight_headroom": 1.0,
        }


def _get_clip_tokenize():
    """Retrieve open_clip tokenizer (lazy import)."""
    try:
        import open_clip

        return open_clip.get_tokenizer("ViT-L-14")
    except Exception:
        try:
            import clip

            return clip.tokenize
        except Exception:
            return None


# ---------------------------------------------------------------------------
# EXIF / catalog field bucketing
# ---------------------------------------------------------------------------


def focal_length_bucket(focal_length_mm: float | None) -> str:
    """Map focal length in mm to a categorical bucket."""
    if focal_length_mm is None:
        return "unknown"
    fl = float(focal_length_mm)
    if fl < 20:
        return "ultra_wide"
    if fl < 35:
        return "wide"
    if fl < 70:
        return "normal"
    if fl < 135:
        return "short_tele"
    if fl < 300:
        return "tele"
    return "super_tele"


def time_of_day_bucket(capture_unix: float | None) -> str:
    """Map a Unix timestamp to a categorical time-of-day bucket (local hour)."""
    if capture_unix is None:
        return "unknown"
    try:
        dt = datetime.fromtimestamp(capture_unix)
        hour = dt.hour
        if 5 <= hour < 8:
            return "dawn"
        if 8 <= hour < 12:
            return "morning"
        if 12 <= hour < 17:
            return "afternoon"
        if 17 <= hour < 20:
            return "evening"
        return "night"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Develop settings normalisation for interpolation
# ---------------------------------------------------------------------------

# Mapping from Lightroom develop keys to canonical recipe key names used
# by edit_recipe.GLOBAL_FIELD_RANGES.  Only numeric sliders that are safe
# to interpolate are listed here.
_LR_TO_CANONICAL: dict[str, str] = {
    "Exposure2012": "exposure",
    "Contrast2012": "contrast",
    "Highlights2012": "highlights",
    "Shadows2012": "shadows",
    "Whites2012": "whites",
    "Blacks2012": "blacks",
    "Temp": "temperature",
    "Temperature": "temperature",
    "Tint": "tint",
    "Texture": "texture",
    "Clarity2012": "clarity",
    "Dehaze": "dehaze",
    "Vibrance": "vibrance",
    "Saturation": "saturation",
    "Sharpness": "sharpening",
    "SharpenRadius": "sharpen_radius",
    "SharpenDetail": "sharpen_detail",
    "SharpenEdgeMasking": "sharpen_masking",
    "LuminanceSmoothing": "noise_reduction",
    "LuminanceNoiseReductionDetail": "noise_reduction_detail",
    "LuminanceNoiseReductionContrast": "noise_reduction_contrast",
    "ColorNoiseReduction": "color_noise_reduction",
    "ColorNoiseReductionDetail": "color_noise_reduction_detail",
    "ColorNoiseReductionSmoothness": "color_noise_reduction_smoothness",
    "PostCropVignetteAmount": "vignette",
    "PostCropVignetteMidpoint": "vignette_midpoint",
    "PostCropVignetteRoundness": "vignette_roundness",
    "PostCropVignetteFeather": "vignette_feather",
    "PostCropVignetteHighlightContrast": "vignette_highlights",
    "GrainAmount": "grain",
    "GrainSize": "grain_size",
    "GrainFrequency": "grain_roughness",
    "DefringePurpleAmount": "defringe_purple_amount",
    "DefringePurpleHueLo": "defringe_purple_hue_lo",
    "DefringePurpleHueHi": "defringe_purple_hue_hi",
    "DefringeGreenAmount": "defringe_green_amount",
    "DefringeGreenHueLo": "defringe_green_hue_lo",
    "DefringeGreenHueHi": "defringe_green_hue_hi",
    "LensManualDistortionAmount": "manual_distortion",
    "LensManualVignetteAmount": "manual_vignette_amount",
    "LensManualVignetteMidpoint": "manual_vignette_midpoint",
    "ParametricHighlights": "tone_curve_highlights",
    "ParametricLights": "tone_curve_lights",
    "ParametricDarks": "tone_curve_darks",
    "ParametricShadows": "tone_curve_shadows",
}


def normalize_develop_settings_for_style(
    develop_settings: dict[str, Any],
) -> dict[str, Any]:
    """Convert raw LR develop settings dict to canonical dict for interpolation."""
    canonical: dict[str, Any] = {}
    white_balance = develop_settings.get("WhiteBalance")
    if isinstance(white_balance, str) and white_balance.strip():
        canonical["white_balance"] = white_balance.strip()
    for lr_key, canon_key in _LR_TO_CANONICAL.items():
        raw = develop_settings.get(lr_key)
        if raw is not None and isinstance(raw, (int, float)):
            canonical[canon_key] = round(float(raw), 4)

    # Extract HSL
    hsl = {}
    colors = ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]
    for color in colors:
        c_lower = color.lower()
        hue = develop_settings.get(f"HueAdjustment{color}")
        sat = develop_settings.get(f"SaturationAdjustment{color}")
        lum = develop_settings.get(f"LuminanceAdjustment{color}")
        if hue is not None or sat is not None or lum is not None:
            hsl[c_lower] = {
                "hue": round(float(hue if hue is not None else 0.0), 2),
                "saturation": round(float(sat if sat is not None else 0.0), 2),
                "luminance": round(float(lum if lum is not None else 0.0), 2),
            }
    if hsl:
        canonical["hsl"] = hsl

    # Extract Color Grading
    cg = {}
    for region, lr_prefixes in [
        ("shadows", ("Shadow", "Shadows")),
        ("midtones", ("Midtone", "Midtones")),
        ("highlights", ("Highlight", "Highlights")),
        ("global", ("Global",)),
    ]:
        h = next(
            (
                develop_settings.get(f"ColorGrade{prefix}Hue")
                for prefix in lr_prefixes
                if develop_settings.get(f"ColorGrade{prefix}Hue") is not None
            ),
            None,
        )
        if h is None:
            h = next(
                (
                    develop_settings.get(f"SplitToning{prefix}Hue")
                    for prefix in lr_prefixes
                    if develop_settings.get(f"SplitToning{prefix}Hue") is not None
                ),
                None,
            )
        s = next(
            (
                develop_settings.get(f"ColorGrade{prefix}Sat")
                for prefix in lr_prefixes
                if develop_settings.get(f"ColorGrade{prefix}Sat") is not None
            ),
            None,
        )
        if s is None:
            s = next(
                (
                    develop_settings.get(f"SplitToning{prefix}Saturation")
                    for prefix in lr_prefixes
                    if develop_settings.get(f"SplitToning{prefix}Saturation")
                    is not None
                ),
                None,
            )
        l = next(
            (
                develop_settings.get(f"ColorGrade{prefix}Lum")
                for prefix in lr_prefixes
                if develop_settings.get(f"ColorGrade{prefix}Lum") is not None
            ),
            None,
        )
        if h is not None or s is not None or l is not None:
            cg_part = {
                "hue": round(float(h if h is not None else 0.0), 2),
                "saturation": round(float(s if s is not None else 0.0), 2),
                "luminance": round(float(l if l is not None else 0.0), 2),
            }
            cg[region] = cg_part

    blending = develop_settings.get("ColorGradeBlending")
    balance = develop_settings.get("ColorGradeBalance")
    if balance is None:
        balance = develop_settings.get("SplitToningBalance")

    if cg:
        cg["blending"] = round(float(blending if blending is not None else 50.0), 2)
        cg["balance"] = round(float(balance if balance is not None else 0.0), 2)
        canonical["color_grading"] = cg

    # Extract Point Curves
    point_curve = {}
    for curve_key, lr_key in [
        ("master", "ToneCurvePV2012"),
        ("red", "ToneCurvePV2012Red"),
        ("green", "ToneCurvePV2012Green"),
        ("blue", "ToneCurvePV2012Blue"),
    ]:
        raw_curve = develop_settings.get(lr_key)
        if isinstance(raw_curve, list) and len(raw_curve) >= 4:
            # Flatten or ensure it's a flat array of numbers
            point_curve[curve_key] = [float(x) for x in raw_curve]
    if point_curve:
        if "tone_curve" not in canonical:
            canonical["tone_curve"] = {}
        canonical["tone_curve"]["point_curve"] = point_curve

    # Extract Crop Settings
    crop = {}
    for canon_key, lr_key in [
        ("top", "CropTop"),
        ("bottom", "CropBottom"),
        ("left", "CropLeft"),
        ("right", "CropRight"),
        ("angle", "CropAngle"),
    ]:
        val = develop_settings.get(lr_key)
        if val is not None and isinstance(val, (int, float)):
            crop[canon_key] = round(float(val), 5)

    if crop:
        canonical["crop"] = crop

    return canonical


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_training_example(
    photo_id: str,
    develop_settings: dict[str, Any],
    embedding: list[float] | None,
    *,
    label: str | None = None,
    filename: str | None = None,
    summary: str | None = None,
    image_bytes: bytes | None = None,
    focal_length: float | None = None,
    lens: str | None = None,
    capture_time_unix: float | None = None,
    camera_make: str | None = None,
    camera_model: str | None = None,
    camera_profile: str | None = None,
    user_keywords: list[str] | None = None,
    iso: float | None = None,
    aperture: float | None = None,
    shutter_speed: str | None = None,
    rating: int = 0,
    pick_status: int = 0,
    skip_discovery: bool = False,
    force_retrain: bool = True,
    source_provenance: str = "unknown",
    source_stamp: dict[str, Any] | None = None,
) -> None:
    """Store or overwrite a training example.

    Args:
        photo_id:         Stable photo identifier (same as main collection).
        develop_settings: Raw Lightroom develop settings dict captured from the photo.
        embedding:        Finite CLIP embedding from a neutral RAW preview.
        label:            Optional user-facing style label (e.g. "Wedding").
        filename:         Original filename for display purposes.
        summary:          Optional short description of the edit style.
        image_bytes:      Raw image bytes for exposure metric computation.
        focal_length:     Focal length in mm from EXIF.
        lens:             Lens model string from EXIF.
        capture_time_unix: Capture time as Unix timestamp.
        camera_make:      Camera manufacturer string.
        camera_model:     Camera model string.
        camera_profile:   Camera profile name (e.g. "Nikon Z7 Linear").
        user_keywords:    User-provided keywords (e.g. ["macro", "nature"]).
        iso:              ISO value.
        aperture:         Aperture f-number.
        shutter_speed:    Shutter speed string (e.g. "1/250").
    """
    _ensure_initialized()
    if _training_collection is None:
        logger.warning(
            "add_training_example skipped: service not initialized (DB_PATH missing)."
        )
        return
    if not photo_id:
        raise ValueError("photo_id is required")
    if embedding is None:
        raise ValueError("A neutral source embedding is required")
    embedding_array = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if (
        not len(embedding_array)
        or not np.all(np.isfinite(embedding_array))
        or float(np.linalg.norm(embedding_array)) <= 0
    ):
        raise ValueError("The neutral source embedding must be finite and nonzero")
    if source_provenance != "raw_preview":
        raise ValueError("Training requires target-independent RAW-preview evidence")
    from services import source_embeddings

    if (
        not source_embeddings.metadata_has_current_contract(source_stamp)
        or (source_stamp or {}).get("source_embedding_provenance")
        != source_embeddings.RAW_PREVIEW_PROVENANCE
        or not (source_stamp or {}).get("source_embedding_fingerprint")
    ):
        raise ValueError(
            "Training source evidence has a missing or stale contract stamp"
        )

    try:
        existing = _training_collection.get(ids=[photo_id], include=[])
    except _ChromaInternalError:
        existing = None

    if existing and existing.get("ids"):
        if not force_retrain:
            raise ValueError(f"Skipped {photo_id}: Already trained")

    from services.photo_constraints import is_stitched_panorama

    is_panorama = is_stitched_panorama(
        {
            "filename": filename or "",
            "user_keywords": user_keywords or [],
            "camera_profile": camera_profile or "",
        }
    )
    if is_panorama:
        raise ValueError("Stitched panoramas are not eligible for learned editing")

    metadata: dict[str, Any] = {
        "photo_id": photo_id,
        "develop_settings": json.dumps(develop_settings, ensure_ascii=False),
        "canonical_settings": json.dumps(
            normalize_develop_settings_for_style(develop_settings), ensure_ascii=False
        ),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "has_embedding": True,
        "is_panorama": False,
        "source_provenance": str(source_provenance or "unknown")[:64],
    }
    metadata.update(dict(source_stamp or {}))
    if not filename:
        try:
            from services import chroma as chroma_service

            chroma_service._ensure_initialized()
            if chroma_service.collection is not None:
                res = chroma_service.collection.get(
                    ids=[photo_id], include=["metadatas"]
                )
                if res and res.get("metadatas") and res["metadatas"][0]:
                    mm = res["metadatas"][0]
                    if not filename and mm.get("filename"):
                        filename = mm.get("filename")
        except Exception as exc:
            logger.debug(
                "Could not enrich new training example from main index: %s", exc
            )

    if filename:
        metadata["filename"] = filename
    if summary:
        metadata["summary"] = summary
    if user_keywords:
        # User-authored, open-vocabulary descriptors remain explanatory evidence.
        normalized = [
            k.strip().lower().replace(" ", "_") for k in user_keywords if k.strip()
        ]
        metadata["user_keywords"] = json.dumps(normalized, ensure_ascii=False)
        metadata["content_tags"] = json.dumps(normalized, ensure_ascii=False)

    # EXIF categorical fields
    metadata["focal_length_bucket"] = focal_length_bucket(focal_length)
    metadata["time_of_day_bucket"] = time_of_day_bucket(capture_unix=capture_time_unix)
    if focal_length is not None:
        metadata["focal_length"] = float(focal_length)
    if lens:
        metadata["lens"] = str(lens)[:128]
    if capture_time_unix is not None:
        metadata["capture_time"] = float(capture_time_unix)
    if camera_make:
        metadata["camera_make"] = camera_make[:64]
    if camera_model:
        metadata["camera_model"] = camera_model[:64]
    if camera_profile:
        metadata["camera_profile"] = camera_profile[:128]
    from services.rendering_state import rendering_state_from_settings

    rendering_state = rendering_state_from_settings(
        develop_settings,
        camera_make=camera_make,
        camera_model=camera_model,
        legacy_profile=camera_profile,
    )
    metadata["rendering_state_json"] = json.dumps(
        rendering_state, sort_keys=True, separators=(",", ":")
    )
    metadata["is_hdr"] = bool(rendering_state["is_hdr"])
    if iso is not None:
        metadata["iso"] = float(iso)
    if aperture is not None:
        metadata["aperture"] = float(aperture)
    if shutter_speed:
        metadata["shutter_speed"] = str(shutter_speed)[:16]
    metadata["rating"] = int(rating)
    metadata["pick_status"] = int(pick_status)

    # Exposure metrics from JPEG preview
    if image_bytes:
        exp_metrics = compute_exposure_metrics(image_bytes)
        metadata.update(exp_metrics)

    metadata["label"] = label if label and label.strip() else "Uncategorized"

    emb = embedding_array.tolist()

    # Upsert: update if already present, add otherwise.
    if existing and existing.get("ids"):
        _training_collection.update(
            ids=[photo_id], embeddings=[emb], metadatas=[metadata]
        )
        logger.debug("Updated training example photo_id=%s", photo_id)
    else:
        _training_collection.add(ids=[photo_id], embeddings=[emb], metadatas=[metadata])
        logger.debug("Added training example photo_id=%s", photo_id)

    # Update style catalog
    if not skip_discovery:
        try:
            from services import policy_runtime

            policy_runtime.schedule_rebuild()
        except Exception as exc:
            logger.warning("Editing-policy rebuild trigger failed: %s", exc)


def update_training_example_labels(
    photo_ids: list[str], label: str, summary: str | None = None
) -> None:
    _ensure_initialized()
    if _training_collection is None or not photo_ids:
        return

    try:
        existing = _training_collection.get(ids=photo_ids, include=["metadatas"])
    except Exception as exc:
        logger.warning(f"Failed to fetch training examples for label update: {exc}")
        return

    ids = existing.get("ids", [])
    metadatas = existing.get("metadatas", [])

    if not ids or not metadatas:
        return

    updated_metadatas = []
    for i, meta in enumerate(metadatas):
        m = dict(meta) if meta else {}
        if label:
            m["label"] = label
        if summary:
            m["summary"] = summary
        updated_metadatas.append(m)

    try:
        _training_collection.update(ids=ids, metadatas=updated_metadatas)
        logger.info(f"Updated label for {len(ids)} training examples")
    except Exception as exc:
        logger.warning(f"Failed to update training example labels: {exc}")


def delete_training_example(photo_id: str) -> bool:
    """Delete a training example by photo_id."""
    _ensure_initialized()
    if _training_collection is None:
        return False
    try:
        existing = _training_collection.get(ids=[photo_id], include=[])
        if not existing or not existing.get("ids"):
            return False
    except _ChromaInternalError:
        return False
    _training_collection.delete(ids=[photo_id])
    logger.debug("Deleted training example photo_id=%s", photo_id)

    try:
        from services import policy_runtime

        policy_runtime.schedule_rebuild()
    except Exception as exc:
        logger.warning("Editing-policy rebuild after deletion failed: %s", exc)
    return True


def get_training_count() -> int:
    """Return the total number of stored training examples."""
    _ensure_initialized()
    if _training_collection is None:
        return 0
    return _training_collection.count()


def _enrich_and_sync_metadatas_from_main_index(
    ids: list[str], metadatas: list[Any]
) -> None:
    """Backfill missing identity/EXIF fields from the searchable image index.

    Generated captions, keywords, and scene labels are deliberately excluded:
    they are not user-authored evidence and must not overwrite the independent
    training analysis.
    """
    if not ids or not metadatas:
        return
    try:
        from services import chroma as chroma_service

        chroma_service._ensure_initialized()
        if chroma_service.collection is None:
            return
        main_res = chroma_service.collection.get(ids=ids, include=["metadatas"])
        main_ids = main_res.get("ids") or []
        main_metas = main_res.get("metadatas") or []
        main_map = {
            main_ids[j]: main_metas[j]
            for j in range(len(main_ids))
            if j < len(main_metas) and main_metas[j]
        }

        updated_ids = []
        updated_metas = []
        enrichment_keys = {
            "filename",
            "lr_uuid",
            "uuid",
            "width",
            "height",
            "aspect_ratio",
            "camera_make",
            "camera_model",
            "camera_profile",
            "lens",
            "focal_length",
            "capture_time",
            "iso",
            "aperture",
            "shutter_speed",
        }
        for i, pid in enumerate(ids):
            if pid in main_map and i < len(metadatas) and metadatas[i]:
                mm = main_map[pid]
                meta = (
                    dict(metadatas[i])
                    if not isinstance(metadatas[i], dict)
                    else metadatas[i]
                )
                changed = False
                for k in enrichment_keys:
                    val_main = mm.get(k)
                    if (
                        val_main is not None
                        and val_main
                        not in (
                            "",
                            "[]",
                            "null",
                            "None",
                        )
                        and meta.get(k) in (None, "", "unknown")
                    ):
                        meta[k] = val_main
                        changed = True
                if changed:
                    metadatas[i] = meta
                    updated_ids.append(pid)
                    updated_metas.append(meta)

        if updated_ids and _training_collection is not None:
            try:
                _training_collection.update(ids=updated_ids, metadatas=updated_metas)
                logger.info(
                    "Enriched and synced %d training examples with metadata from main search index.",
                    len(updated_ids),
                )
            except Exception as exc:
                logger.debug("Failed to sync enriched metadata to ChromaDB: %s", exc)
    except Exception as exc:
        logger.debug(
            "Could not enrich missing training keywords from main index: %s", exc
        )


def list_training_examples() -> list[dict[str, Any]]:
    """Return all training examples as a list of dicts (no embeddings)."""
    _ensure_initialized()
    if _training_collection is None:
        return []
    ids: list[str] = []
    metadatas: list[Any] = []
    try:
        for page in _iter_training_pages(["metadatas"]):
            ids.extend(page.get("ids") or [])
            metadatas.extend(page.get("metadatas") or [])
    except _ChromaInternalError:
        return []
    _enrich_and_sync_metadatas_from_main_index(ids, metadatas)
    examples = []
    for i, pid in enumerate(ids):
        meta = dict(metadatas[i]) if i < len(metadatas) else {}
        ex = dict(meta)
        ex.update(
            {
                "photo_id": pid,
                "lr_uuid": meta.get("lr_uuid") or meta.get("uuid") or "",
                "filename": meta.get("filename", "") or "",
                "label": meta.get("label", ""),
                "summary": meta.get("summary", ""),
                "captured_at": meta.get("captured_at", ""),
                "has_embedding": bool(meta.get("has_embedding", False)),
                "focal_length_bucket": meta.get("focal_length_bucket", "unknown"),
                "time_of_day_bucket": meta.get("time_of_day_bucket", "unknown"),
                "content_tags": meta.get("content_tags") or "[]",
                "user_keywords": meta.get("user_keywords") or "[]",
                "camera_make": meta.get("camera_make", ""),
                "camera_model": meta.get("camera_model", ""),
                "camera_profile": meta.get("camera_profile", ""),
                "canonical_settings": meta.get("canonical_settings", "{}"),
                "develop_settings": meta.get("develop_settings", "{}"),
                "exp_luminance_mean": meta.get("exp_luminance_mean", "0.5"),
                "exp_contrast": meta.get("exp_contrast", "0.0"),
                "zone_deep_shadows": meta.get("zone_deep_shadows", "0.0"),
                "zone_shadows": meta.get("zone_shadows", "0.0"),
                "zone_midtones": meta.get("zone_midtones", "0.0"),
                "zone_highlights": meta.get("zone_highlights", "0.0"),
                "zone_bright_highlights": meta.get("zone_bright_highlights", "0.0"),
            }
        )
        examples.append(ex)
    examples.sort(key=lambda x: x["captured_at"], reverse=True)
    return examples


def list_training_examples_with_embeddings(
    *,
    cancel_requested: Any | None = None,
) -> list[dict[str, Any]]:
    """Return bounded-page training metadata with source embeddings for v2 fitting."""
    _ensure_initialized()
    if _training_collection is None:
        return []
    examples: list[dict[str, Any]] = []
    for page in _iter_training_pages(["metadatas", "embeddings"]):
        if cancel_requested is not None and cancel_requested():
            raise InterruptedError(
                "editing-policy rebuild canceled while loading examples"
            )
        ids = page.get("ids") or []
        metadatas = page.get("metadatas") or []
        # Chroma returns embeddings as a NumPy array in current releases.
        # Boolean-coercing a multi-row array raises an ambiguous truth-value
        # error, so only substitute the empty fallback for an absent field.
        embeddings = page.get("embeddings")
        if embeddings is None:
            embeddings = []
        _enrich_and_sync_metadatas_from_main_index(ids, metadatas)
        for index, photo_id in enumerate(ids):
            metadata = (
                dict(metadatas[index])
                if index < len(metadatas) and metadatas[index]
                else {}
            )
            embedding = embeddings[index] if index < len(embeddings) else None
            examples.append(
                {
                    "photo_id": photo_id,
                    "metadata": metadata,
                    "embedding": (
                        np.asarray(embedding, dtype=np.float32).copy()
                        if embedding is not None
                        else None
                    ),
                }
            )
    examples.sort(key=lambda item: item["photo_id"])
    return examples


def get_existing_training_ids(photo_ids: list[str]) -> set[str]:
    """Return current, neutral examples that need no Lightroom re-export."""
    from services import source_embeddings

    _ensure_initialized()
    if _training_collection is None:
        return set()
    normalized = list(
        dict.fromkeys(str(photo_id or "").strip() for photo_id in photo_ids)
    )
    normalized = [photo_id for photo_id in normalized if photo_id]
    existing: set[str] = set()
    for offset in range(0, len(normalized), 500):
        try:
            result = _training_collection.get(
                ids=normalized[offset : offset + 500],
                include=["metadatas"],
            )
        except _ChromaInternalError:
            continue
        result_ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        for index, photo_id in enumerate(result_ids):
            metadata = (
                metadatas[index]
                if index < len(metadatas) and isinstance(metadatas[index], dict)
                else {}
            )
            if (
                metadata.get("has_embedding") is True
                and metadata.get("source_provenance") == "raw_preview"
                and metadata.get("source_embedding_provenance") == "raw_preview"
                and metadata.get("source_embedding_fingerprint")
                and source_embeddings.metadata_has_current_contract(metadata)
            ):
                existing.add(str(photo_id))
    return existing


def get_training_stats() -> dict[str, Any]:
    """Return policy-eligible readiness plus aggregate explanatory statistics."""
    from services.photo_constraints import is_stitched_panorama
    from services.policy_targets import flatten_absolute_target
    from services import policy_runtime
    from services import source_embeddings

    minimum_partition_examples = policy_runtime.MIN_PARTITION_EXAMPLES

    def empty_payload() -> dict[str, Any]:
        return {
            "count": 0,
            "eligible_count": 0,
            "excluded_count": 0,
            "exclusions": {},
            "eligible_partitions": {},
            "minimum_partition_examples": minimum_partition_examples,
            "has_enough_examples": False,
            "has_active_generation": False,
            "active_generation_id": None,
            "readiness": "cold_start",
            "next_action": "learn_from_my_edits",
            "descriptor_distribution": {},
            "focal_buckets": {},
            "time_of_day": {},
            "camera_distribution": {},
            "exposure": {},
            "top_signature_styles": [],
        }

    _ensure_initialized()
    if _training_collection is None:
        return empty_payload()
    count = get_training_count()

    descriptor_dist: dict[str, int] = {}
    focal_dist: dict[str, int] = {}
    tod_dist: dict[str, int] = {}
    camera_dist: dict[str, int] = {}
    exp_means: list[float] = []
    exp_contrasts: list[float] = []
    exp_colorfulness: list[float] = []
    exclusions: dict[str, int] = {}
    partition_counts: dict[str, int] = {}

    def exclude(reason: str) -> None:
        exclusions[reason] = exclusions.get(reason, 0) + 1

    try:
        for page in _iter_training_pages(["metadatas", "embeddings"]):
            metadatas = page.get("metadatas") or []
            embeddings = page.get("embeddings")
            if embeddings is None:
                embeddings = []
            for index, meta in enumerate(metadatas):
                if not isinstance(meta, dict):
                    exclude("invalid_metadata")
                    continue
                tags = _safe_json_list(
                    meta.get("content_tags") or meta.get("user_keywords") or "[]"
                )
                for tag in tags:
                    descriptor_dist[tag] = descriptor_dist.get(tag, 0) + 1

                fb = meta.get("focal_length_bucket", "unknown")
                focal_dist[fb] = focal_dist.get(fb, 0) + 1

                tod = meta.get("time_of_day_bucket", "unknown")
                tod_dist[tod] = tod_dist.get(tod, 0) + 1

                cam = meta.get("camera_model", meta.get("camera_make", "unknown"))
                camera_dist[cam] = camera_dist.get(cam, 0) + 1

                if "exp_luminance_mean" in meta:
                    exp_means.append(float(meta["exp_luminance_mean"]))
                if "exp_contrast" in meta:
                    exp_contrasts.append(float(meta["exp_contrast"]))
                if "exp_colorfulness" in meta:
                    exp_colorfulness.append(float(meta["exp_colorfulness"]))

                embedding = embeddings[index] if index < len(embeddings) else None
                if embedding is None or not bool(meta.get("has_embedding", False)):
                    exclude("missing_embedding")
                    continue
                vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
                if (
                    not len(vector)
                    or not np.all(np.isfinite(vector))
                    or float(np.linalg.norm(vector)) <= 0
                ):
                    exclude("invalid_embedding")
                    continue
                if meta.get("source_provenance") != "raw_preview":
                    exclude("source_not_neutral")
                    continue
                if (
                    meta.get("source_embedding_provenance") != "raw_preview"
                    or not source_embeddings.metadata_has_current_contract(meta)
                    or not meta.get("source_embedding_fingerprint")
                ):
                    exclude("stale_source_stamp")
                    continue
                if is_stitched_panorama(meta):
                    exclude("panorama")
                    continue
                try:
                    canonical = json.loads(meta.get("canonical_settings") or "{}")
                except (TypeError, ValueError):
                    canonical = {}
                if not flatten_absolute_target(canonical, include_applicability=True):
                    exclude("missing_target")
                    continue
                partition = policy_runtime.hard_partition_key(meta)
                partition_counts[partition] = partition_counts.get(partition, 0) + 1
    except _ChromaInternalError:
        logger.warning("Training statistics scan stopped on a Chroma error")

    exposure_stats: dict[str, Any] = {}
    if exp_means:
        exposure_stats["mean_luminance"] = round(sum(exp_means) / len(exp_means), 3)
    if exp_contrasts:
        exposure_stats["mean_contrast"] = round(
            sum(exp_contrasts) / len(exp_contrasts), 3
        )
    if exp_colorfulness:
        exposure_stats["mean_colorfulness"] = round(
            sum(exp_colorfulness) / len(exp_colorfulness), 3
        )

    top_styles: list[dict[str, Any]] = []
    active_generation_id = None
    try:
        styles = policy_runtime.list_active_policies()
        artifacts = policy_runtime._load_active_artifacts()
        if artifacts:
            active_generation_id = next(iter(artifacts.values())).generation_id
        styles.sort(key=lambda s: s.get("example_count", 0), reverse=True)
        for s in styles[:5]:
            top_styles.append(
                {
                    "name": s.get("style_name") or s.get("style_id") or "Unknown",
                    "count": s.get("example_count", 0),
                }
            )
    except Exception:
        pass

    eligible_count = sum(partition_counts.values())
    trainable = any(
        partition_count >= minimum_partition_examples
        for partition_count in partition_counts.values()
    )
    if active_generation_id:
        readiness = "active"
        next_action = "apply_my_style"
    elif trainable:
        readiness = "ready_to_rebuild"
        next_action = "rebuild"
    elif eligible_count:
        readiness = "collecting"
        next_action = "learn_from_my_edits"
    else:
        readiness = "cold_start"
        next_action = "learn_from_my_edits"

    return {
        "count": count,
        "eligible_count": eligible_count,
        "excluded_count": sum(exclusions.values()),
        "exclusions": exclusions,
        "eligible_partitions": partition_counts,
        "minimum_partition_examples": minimum_partition_examples,
        "has_enough_examples": trainable,
        "has_active_generation": active_generation_id is not None,
        "active_generation_id": active_generation_id,
        "readiness": readiness,
        "next_action": next_action,
        "descriptor_distribution": descriptor_dist,
        "focal_buckets": focal_dist,
        "time_of_day": tod_dist,
        "camera_distribution": camera_dist,
        "exposure": exposure_stats,
        "top_signature_styles": top_styles,
    }


def query_similar_training_examples(
    query_embedding: list[float],
    n_results: int = 5,
    camera_profile: str | None = None,
) -> list[dict[str, Any]]:
    """Return up to n_results training examples closest to query_embedding.

    Each result dict contains:
        photo_id, develop_settings (dict), canonical_settings (dict),
        label, filename, summary, distance, content_tags, exp_luminance_mean,
        exp_contrast, focal_length_bucket, time_of_day_bucket.

    Returns an empty list when no training examples exist or embedding is None.
    """
    _ensure_initialized()
    if _training_collection is None or query_embedding is None:
        return []

    count = get_training_count()
    if count == 0:
        return []

    n_results = min(n_results, count)

    where = None
    if camera_profile:
        where = {"camera_profile": camera_profile}

    try:
        result = _training_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["metadatas", "distances", "embeddings"],
        )
    except Exception as exc:
        logger.error("query_similar_training_examples failed: %s", exc, exc_info=True)
        return []

    ids = (result.get("ids") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    embeddings = (result.get("embeddings") or [[]])[0]

    examples = []
    for i, pid in enumerate(ids):
        meta = dict(metadatas[i]) if i < len(metadatas) else {}
        dev_settings_raw = meta.get("develop_settings", "{}")
        try:
            dev_settings = json.loads(dev_settings_raw)
        except (ValueError, TypeError):
            dev_settings = {}

        canonical_raw = meta.get("canonical_settings", "{}")
        try:
            canonical_settings = json.loads(canonical_raw)
        except (ValueError, TypeError):
            canonical_settings = {}

        examples.append(
            {
                "photo_id": pid,
                "embedding": embeddings[i] if i < len(embeddings) else None,
                "capture_time": float(meta.get("capture_time", 0.0)),
                "rating": int(meta.get("rating", 0) or 0),
                "pick_status": int(meta.get("pick_status", 0) or 0),
                "develop_settings": dev_settings,
                "canonical_settings": canonical_settings,
                "label": meta.get("label", ""),
                "filename": meta.get("filename", ""),
                "summary": meta.get("summary", ""),
                "distance": float(distances[i]) if i < len(distances) else 1.0,
                "content_tags": _safe_json_list(
                    meta.get("content_tags") or meta.get("user_keywords") or "[]"
                ),
                "exp_luminance_mean": float(meta.get("exp_luminance_mean", 0.5)),
                "exp_contrast": float(meta.get("exp_contrast", 0.0)),
                "exp_colorfulness": float(meta.get("exp_colorfulness", 0.0)),
                "exp_warmth_proxy": float(meta.get("exp_warmth_proxy", 0.5)),
                "exp_highlight_ratio": float(meta.get("exp_highlight_ratio", 0.0)),
                "exp_shadow_ratio": float(meta.get("exp_shadow_ratio", 0.0)),
                "focal_length_bucket": meta.get("focal_length_bucket", "unknown"),
                "time_of_day_bucket": meta.get("time_of_day_bucket", "unknown"),
            }
        )
    return examples


def clear_all_training_examples() -> int:
    """Delete every training example. Returns the number removed."""
    _ensure_initialized()
    if _training_collection is None:
        return 0
    removed = 0
    while True:
        try:
            result = _training_collection.get(
                include=[], limit=TRAINING_PAGE_SIZE, offset=0
            )
        except _ChromaInternalError:
            break
        ids = result.get("ids") or []
        if not ids:
            break
        _training_collection.delete(ids=ids)
        removed += len(ids)
    logger.info("Cleared all %d training examples.", removed)
    return removed


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _safe_json_list(value: Any) -> list[str]:
    """Safely decode a JSON string to a list, returning [] on failure."""
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value or "[]")
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except Exception:
        pass
    return []
