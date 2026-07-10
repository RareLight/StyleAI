"""
Edit style training service.

Manages the `edit_training` ChromaDB collection that stores the user's own
Lightroom develop settings as few-shot examples.  When the AI generates a new
edit recipe it queries this collection by CLIP visual similarity and injects
the closest matches as style examples into the LLM prompt.

Enhanced with multi-criteria features:
  - Exposure metrics (luminance, contrast, highlight/shadow ratios)
  - Scene-type tags via CLIP zero-shot text probing
  - EXIF-based categorical fields (focal-length bucket, time-of-day, camera)
  - Statistics endpoint for the style-profile UI
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
EMBEDDING_DIM = (
    1152  # CLIP ViT-L/14 dimension used by the main image_embeddings collection
)

# ---------------------------------------------------------------------------
# Scene-type probe texts for CLIP zero-shot classification
# ---------------------------------------------------------------------------

_SCENE_PROBES: dict[str, str] = {
    # Subject Matter
    "scene_portrait": "a portrait photograph of a human face, person, couple, family, or pet where the subject is clearly the main focus",
    "scene_group": "a photograph of a group of people or crowd",
    "scene_landscape": "a scenic landscape or nature photograph of mountains, valleys, oceans, forests, or natural terrain without people or urban structures",
    "scene_architecture": "an architectural photograph of a building facade, house, interior room, bridge, or structural design",
    "scene_wildlife": "a wildlife photograph of wild animals or birds in nature",
    "scene_event": "an event photograph of a wedding, concert, ceremony, or celebration party",
    "scene_street": "a street photography or urban scene photograph of city streets, ferris wheels, amusement parks, carnival rides, or city life",
    "scene_macro": "an extreme close-up macro photograph of a tiny insect, bug, beetle, spider, flower stamen, or water droplet with high magnification and shallow depth of field",
    "scene_flowers": "a botanical close-up photograph of flowers or garden plants",
    "scene_interior": "an interior design or indoor room photograph",
    "scene_exterior": "an outdoor architectural or exterior scene photograph",
    "scene_golden_hour": "a photograph taken at golden hour, sunset, or sunrise",
    "scene_night": "a photograph taken at night, evening, or after dark",
    "scene_astrophotography": "an astrophotography photograph of the night sky, milky way, stars, or aurora borealis",
    "scene_studio": "a commercial studio tabletop photograph of a product, food dish, lego, or toy shot against a seamless studio backdrop under artificial studio flash lighting",
    "scene_action": "an action photograph of sports, athletics, or fast motion",
    # Aesthetics and Style
    "style_high_key": "a bright, airy, high-key photograph with soft light",
    "style_low_key": "a dark, moody, low-key photograph with deep shadows",
    "style_minimalist": "a minimalist photograph with negative space",
    "style_vintage": "a vintage, retro, or film-like photograph",
    "style_cinematic": "a cinematic or dramatic photograph",
    "style_neon": "a cyberpunk or neon-lit photograph",
}

_SCENE_THRESHOLD = 0.15  # cosine similarity threshold for a tag to be "present"

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
    _training_collection = _chroma_client.get_or_create_collection(name=COLLECTION_NAME)
    logger.info("Initialized edit_training collection.")


def _dummy_embedding() -> list[float]:
    return np.zeros(EMBEDDING_DIM, dtype=np.float32).tolist()


def _safe_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


# ---------------------------------------------------------------------------
# Image analysis at ~3MP (2048px long edge) for histograms & scene features
# ---------------------------------------------------------------------------

_THUMBNAIL_LONG_EDGE = 2048  # ~3MP sweet spot for analysis speed vs accuracy


def _load_thumbnail(image_bytes: bytes) -> tuple[np.ndarray, tuple[int, int]]:
    """Load image and downscale to ~3MP for efficient analysis.

    Returns (rgb_array, original_size).
    """
    from PIL import Image
    import io

    image = Image.open(io.BytesIO(image_bytes))
    orig_size = image.size
    image.thumbnail(
        (_THUMBNAIL_LONG_EDGE, _THUMBNAIL_LONG_EDGE), Image.Resampling.LANCZOS
    )
    image = image.convert("RGB")
    rgb = np.asarray(image, dtype=np.float32) / 255.0
    return rgb, orig_size


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


# ---------------------------------------------------------------------------
# Dominant Color Palette Extraction
# ---------------------------------------------------------------------------


def compute_dominant_colors(image_bytes: bytes, n_colors: int = 5) -> list[str]:
    """Extract the dominant colors from the image using K-Means clustering.
    Returns a list of HEX color strings.
    """
    try:
        from sklearn.cluster import KMeans
        import io
        from PIL import Image

        # Load a very small thumbnail for extremely fast clustering
        image = Image.open(io.BytesIO(image_bytes))
        image.thumbnail((100, 100), Image.Resampling.LANCZOS)
        image = image.convert("RGB")

        # Reshape the image to be a list of pixels
        pixels = np.asarray(image)
        pixels = pixels.reshape(-1, 3)

        # Cluster the pixels
        kmeans = KMeans(n_clusters=n_colors, n_init="auto", random_state=42)
        kmeans.fit(pixels)

        # Get the colors and convert to hex
        colors = kmeans.cluster_centers_.astype(int)

        # Sort by frequency (labels)
        labels = kmeans.labels_
        counts = np.bincount(labels)
        sorted_indices = np.argsort(counts)[::-1]

        hex_colors = []
        for idx in sorted_indices:
            r, g, b = colors[idx]
            hex_colors.append(f"#{r:02x}{g:02x}{b:02x}")

        return hex_colors
    except Exception as exc:
        logger.warning("compute_dominant_colors failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Perceptual histogram signature for style grouping
# ---------------------------------------------------------------------------

_L_BINS = 16
_AB_BINS = 8


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB [0,1] to LAB using D65 white point (simplified)."""
    # Gamma correction (sRGB to linear)
    mask = rgb > 0.04045
    linear = np.where(mask, ((rgb + 0.055) / 1.055) ** 2.4, rgb / 12.92)

    # XYZ conversion (D65)
    x = (
        0.4124564 * linear[:, :, 0]
        + 0.3575761 * linear[:, :, 1]
        + 0.1804375 * linear[:, :, 2]
    )
    y = (
        0.2126729 * linear[:, :, 0]
        + 0.7151522 * linear[:, :, 1]
        + 0.0721750 * linear[:, :, 2]
    )
    z = (
        0.0193339 * linear[:, :, 0]
        + 0.1191920 * linear[:, :, 1]
        + 0.9503041 * linear[:, :, 2]
    )

    # Normalize for D65
    xn, yn, zn = 0.95047, 1.00000, 1.08883
    x, y, z = x / xn, y / yn, z / zn

    # F function
    def _f(t):
        return np.where(t > 0.008856, t ** (1.0 / 3.0), 7.787 * t + 16.0 / 116.0)

    fx, fy, fz = _f(x), _f(y), _f(z)

    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)

    return np.stack([L, a, b], axis=-1)


def compute_histogram_signature(image_bytes: bytes) -> dict[str, Any]:
    """Compute a compact perceptual histogram signature for style grouping.

    Uses LAB color space with 16 L bins + 8 a bins + 8 b bins.
    Returns a dict with normalized histograms and derived statistics.
    """
    try:
        rgb, orig_size = _load_thumbnail(image_bytes)
        lab = _rgb_to_lab(rgb)

        # Compute histograms
        L = lab[:, :, 0].ravel()
        a = lab[:, :, 1].ravel()
        b = lab[:, :, 2].ravel()

        # L: 0-100 → 16 bins
        L_hist, _ = np.histogram(L, bins=_L_BINS, range=(0, 100))
        L_hist = L_hist.astype(np.float32)
        L_hist = L_hist / (L_hist.sum() + 1e-8)

        # a: -128 to 128 → 8 bins
        a_hist, _ = np.histogram(a, bins=_AB_BINS, range=(-128, 128))
        a_hist = a_hist.astype(np.float32)
        a_hist = a_hist / (a_hist.sum() + 1e-8)

        # b: -128 to 128 → 8 bins
        b_hist, _ = np.histogram(b, bins=_AB_BINS, range=(-128, 128))
        b_hist = b_hist.astype(np.float32)
        b_hist = b_hist / (b_hist.sum() + 1e-8)

        # Compact summary statistics for quick comparison
        L_mean = float(np.mean(L))
        L_std = float(np.std(L))
        a_mean = float(np.mean(a))
        b_mean = float(np.mean(b))
        chroma_mean = float(np.mean(np.sqrt(a**2 + b**2)))

        # Tonal distribution (percentile-based, profile-independent)
        L_sorted = np.sort(L)
        n = len(L_sorted)
        shadow_level = float(L_sorted[int(n * 0.10)])  # 10th percentile
        mid_level = float(L_sorted[int(n * 0.50)])  # median
        highlight_level = float(L_sorted[int(n * 0.90)])  # 90th percentile

        return {
            "hist_L": L_hist.tolist(),
            "hist_a": a_hist.tolist(),
            "hist_b": b_hist.tolist(),
            "hist_L_mean": round(L_mean / 100.0, 4),  # normalize to 0..1
            "hist_L_std": round(L_std / 100.0, 4),
            "hist_a_mean": round((a_mean + 128.0) / 256.0, 4),  # normalize to 0..1
            "hist_b_mean": round((b_mean + 128.0) / 256.0, 4),
            "hist_chroma": round(chroma_mean / 128.0, 4),
            "hist_shadow_level": round(shadow_level / 100.0, 4),
            "hist_mid_level": round(mid_level / 100.0, 4),
            "hist_highlight_level": round(highlight_level / 100.0, 4),
            "hist_orig_width": orig_size[0],
            "hist_orig_height": orig_size[1],
        }
    except Exception as exc:
        logger.warning("compute_histogram_signature failed: %s", exc)
        return {
            "hist_L": [1.0 / _L_BINS] * _L_BINS,
            "hist_a": [1.0 / _AB_BINS] * _AB_BINS,
            "hist_b": [1.0 / _AB_BINS] * _AB_BINS,
            "hist_L_mean": 0.5,
            "hist_L_std": 0.0,
            "hist_a_mean": 0.5,
            "hist_b_mean": 0.5,
            "hist_chroma": 0.0,
            "hist_shadow_level": 0.1,
            "hist_mid_level": 0.5,
            "hist_highlight_level": 0.9,
        }


def histogram_distance(sig1: dict[str, Any], sig2: dict[str, Any]) -> float:
    """Compute distance between two histogram signatures.

    Returns 0..1 where 0 = identical, 1 = completely different.
    Uses chi-square on histogram bins + euclidean on summary stats.
    """
    try:
        # Chi-square on histogram bins
        chi_sq = 0.0
        for key in ("hist_L", "hist_a", "hist_b"):
            h1 = np.array(sig1.get(key, []), dtype=np.float32)
            h2 = np.array(sig2.get(key, []), dtype=np.float32)
            if len(h1) == 0 or len(h2) == 0 or len(h1) != len(h2):
                continue
            # Add epsilon to avoid division by zero
            denom = h1 + h2 + 1e-8
            diff = h1 - h2
            chi_sq += float(np.sum(diff**2 / denom))

        # Normalize chi-square (empirical: max useful value ~4.0 for these bin counts)
        chi_component = min(1.0, chi_sq / 4.0)

        # Euclidean on summary stats (all normalized 0..1)
        stat_keys = [
            "hist_L_mean",
            "hist_L_std",
            "hist_chroma",
            "hist_shadow_level",
            "hist_mid_level",
            "hist_highlight_level",
        ]
        stat_diffs = []
        for key in stat_keys:
            v1 = sig1.get(key, 0.5)
            v2 = sig2.get(key, 0.5)
            stat_diffs.append((float(v1) - float(v2)) ** 2)
        stat_dist = np.sqrt(sum(stat_diffs) / len(stat_diffs)) if stat_diffs else 0.0

        # Weighted combination (histogram shape matters more than exact levels)
        return 0.6 * chi_component + 0.4 * stat_dist
    except Exception as exc:
        logger.debug("histogram_distance failed: %s", exc)
        return 1.0


# ---------------------------------------------------------------------------
# Scene-type tagging via CLIP zero-shot probing
# ---------------------------------------------------------------------------


def compute_scene_tags(image_embedding: list[float] | None) -> list[str]:
    """Return list of scene-type tag strings present in the image.

    Uses the image CLIP embedding compared against pre-computed text embeddings
    for each scene probe.  Returns tags whose cosine similarity exceeds
    ``_SCENE_THRESHOLD``.  Gracefully returns [] if CLIP is unavailable.
    """
    if image_embedding is None:
        return []

    try:
        import torch
        import torch.nn.functional as F
        import server_lifecycle
        from config import TORCH_DEVICE

        clip_model = server_lifecycle.get_model()
        clip_processor = server_lifecycle.get_processor()
        if clip_model is None or clip_processor is None:
            return []

        img_vec = (
            torch.tensor(image_embedding, dtype=torch.float32)
            .unsqueeze(0)
            .to(TORCH_DEVICE)
        )
        img_vec = F.normalize(img_vec, p=2, dim=1)

        tags_with_scores: list[tuple[float, str]] = []
        tokenize_fn = (
            getattr(clip_model, "tokenize", None) or server_lifecycle.get_tokenizer()
        )
        if tokenize_fn is None:
            return []

        with torch.no_grad():
            has_siglip = hasattr(clip_model, "logit_bias")
            if has_siglip:
                scale = clip_model.logit_scale.exp().item()
                bias = clip_model.logit_bias.item()

            for tag_name, probe_text in _SCENE_PROBES.items():
                try:
                    tokens = tokenize_fn([probe_text]).to(TORCH_DEVICE)
                    text_features = clip_model.encode_text(tokens)
                    text_vec = F.normalize(text_features, p=2, dim=1)
                    sim = float((img_vec * text_vec).sum().cpu())

                    if has_siglip:
                        logit = sim * scale + bias
                        prob = float(torch.sigmoid(torch.tensor(logit)))
                        is_match = prob >= 0.20 or sim >= _SCENE_THRESHOLD
                    else:
                        is_match = sim >= _SCENE_THRESHOLD

                    if is_match:
                        tags_with_scores.append((sim, tag_name))
                except Exception:
                    pass

        tags_with_scores.sort(key=lambda x: x[0], reverse=True)
        if not tags_with_scores:
            return []
        top_score = tags_with_scores[0][0]
        return [t[1] for t in tags_with_scores if top_score - t[0] <= 0.08]

    except Exception as exc:
        logger.debug("compute_scene_tags failed (non-critical): %s", exc)
        return []


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
    for region, lr_prefix in [
        ("shadows", "Shadows"),
        ("midtones", "Midtones"),
        ("highlights", "Highlights"),
        ("global", "Global"),
    ]:
        h = develop_settings.get(f"ColorGrade{lr_prefix}Hue") or develop_settings.get(
            f"SplitToning{lr_prefix}Hue"
        )
        s = develop_settings.get(f"ColorGrade{lr_prefix}Sat") or develop_settings.get(
            f"SplitToning{lr_prefix}Saturation"
        )
        l = develop_settings.get(f"ColorGrade{lr_prefix}Lum")
        if h is not None or s is not None or l is not None:
            cg_part = {
                "hue": round(float(h if h is not None else 0.0), 2),
                "saturation": round(float(s if s is not None else 0.0), 2),
            }
            if region != "global":
                cg_part["luminance"] = round(float(l if l is not None else 0.0), 2)
            cg[region] = cg_part

    blending = (
        develop_settings.get("ColorGradeBlending")
        or develop_settings.get("SplitToningBalance")
    )  # Balance used as fallback blending sometimes? Actually Lightroom has SplitToningBalance and ColorGradeBlending.
    balance = develop_settings.get("ColorGradeBalance") or develop_settings.get(
        "SplitToningBalance"
    )

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
) -> None:
    """Store or overwrite a training example.

    Args:
        photo_id:         Stable photo identifier (same as main collection).
        develop_settings: Raw Lightroom develop settings dict captured from the photo.
        embedding:        CLIP embedding for the source photo (1152-d float list).
                          Falls back to a zero-dummy when None.
        label:            Optional user-facing style label (e.g. "Wedding").
        filename:         Original filename for display purposes.
        summary:          Optional short description of the edit style.
        image_bytes:      Raw image bytes for exposure metric computation.
        focal_length:     Focal length in mm from EXIF.
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

    try:
        existing = _training_collection.get(ids=[photo_id], include=[])
    except _ChromaInternalError:
        existing = None

    if existing and existing.get("ids"):
        if not force_retrain:
            raise ValueError(f"Skipped {photo_id}: Already trained")

    metadata: dict[str, Any] = {
        "photo_id": photo_id,
        "develop_settings": json.dumps(develop_settings, ensure_ascii=False),
        "canonical_settings": json.dumps(
            normalize_develop_settings_for_style(develop_settings), ensure_ascii=False
        ),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "has_embedding": embedding is not None,
    }
    if not user_keywords or not filename:
        try:
            from services import chroma as chroma_service

            chroma_service._ensure_initialized()
            if chroma_service.collection is not None:
                res = chroma_service.collection.get(
                    ids=[photo_id], include=["metadatas"]
                )
                if res and res.get("metadatas") and res["metadatas"][0]:
                    mm = res["metadatas"][0]
                    if not user_keywords or user_keywords in (
                        "",
                        "[]",
                        "null",
                        "None",
                    ):
                        for k in ("user_keywords", "keywords", "flattened_keywords"):
                            kw_val = mm.get(k)
                            if kw_val and kw_val not in ("", "[]", "null", "None"):
                                if isinstance(kw_val, str):
                                    try:
                                        user_keywords = json.loads(kw_val)
                                    except Exception:
                                        user_keywords = [
                                            s.strip()
                                            for s in kw_val.split(",")
                                            if s.strip()
                                        ]
                                elif isinstance(kw_val, (list, tuple, set)):
                                    user_keywords = [str(s) for s in kw_val]
                                break
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
        # Store as comma-separated, normalized (lowercase, no spaces)
        normalized = [
            k.strip().lower().replace(" ", "_") for k in user_keywords if k.strip()
        ]
        metadata["user_keywords"] = json.dumps(normalized, ensure_ascii=False)

    # EXIF categorical fields
    metadata["focal_length_bucket"] = focal_length_bucket(focal_length)
    metadata["time_of_day_bucket"] = time_of_day_bucket(capture_unix=capture_time_unix)
    if capture_time_unix is not None:
        metadata["capture_time"] = float(capture_time_unix)
    if camera_make:
        metadata["camera_make"] = camera_make[:64]
    if camera_model:
        metadata["camera_model"] = camera_model[:64]
    if camera_profile:
        metadata["camera_profile"] = camera_profile[:128]
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

        # Perceptual histogram signature for style grouping (profile-independent)
        hist_sig = compute_histogram_signature(image_bytes)
        metadata["histogram_signature"] = json.dumps(hist_sig, ensure_ascii=False)

        # Dominant Colors
        dom_colors = compute_dominant_colors(image_bytes, n_colors=5)
        metadata["dominant_colors"] = json.dumps(dom_colors, ensure_ascii=False)

    # Scene-type tags from CLIP zero-shot
    scene_tags = compute_scene_tags(embedding)
    metadata["scene_tags"] = json.dumps(scene_tags, ensure_ascii=False)

    # Auto-assign label from top scene tag if missing or Uncategorized
    if not label or label == "Uncategorized" or label.strip() == "":
        if scene_tags:
            top_tag = scene_tags[0]
            auto_label = (
                top_tag.replace("scene_", "")
                .replace("style_", "")
                .replace("_", " ")
                .title()
            )
            metadata["label"] = auto_label
            label = auto_label
        else:
            metadata["label"] = "Uncategorized"
            label = "Uncategorized"
    elif label:
        metadata["label"] = label

    emb = embedding if embedding is not None else _dummy_embedding()

    # Upsert: update if already present, add otherwise.
    if existing and existing.get("ids"):
        _training_collection.update(
            ids=[photo_id], embeddings=[emb], metadatas=[metadata]
        )
        logger.info(
            "Updated training example photo_id=%s scene_tags=%s", photo_id, scene_tags
        )
    else:
        _training_collection.add(ids=[photo_id], embeddings=[emb], metadatas=[metadata])
        logger.info(
            "Added training example photo_id=%s scene_tags=%s", photo_id, scene_tags
        )

    # Update style catalog
    if not skip_discovery:
        try:
            from services import style_catalog as style_catalog_service

            style_catalog_service.update_style_for_example(
                photo_id=photo_id,
                camera_make=camera_make,
                camera_model=camera_model,
                camera_profile=camera_profile,
                scene_tags=scene_tags,
                exposure_metrics=(exp_metrics if image_bytes else {}),
                user_keywords=user_keywords,
            )
            # Re-discover styles to update aggregate stats (mean DNA, counts)
            style_catalog_service.discover_styles_from_examples()
        except Exception as exc:
            logger.warning("Style discovery trigger failed: %s", exc)


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
    logger.info("Deleted training example photo_id=%s", photo_id)

    # Re-discover styles to remove orphaned styles / update counts
    try:
        from services import style_catalog as style_catalog_service

        style_catalog_service.discover_styles_from_examples()
    except Exception as exc:
        logger.warning("Style discovery after deletion failed: %s", exc)
    return True


def get_training_count() -> int:
    """Return the total number of stored training examples."""
    _ensure_initialized()
    if _training_collection is None:
        return 0
    try:
        result = _training_collection.get(include=[], limit=1_000_000)
    except _ChromaInternalError:
        return 0
    return len(result.get("ids") or [])


def _enrich_and_sync_metadatas_from_main_index(
    ids: list[str], metadatas: list[Any]
) -> None:
    """Check if training example metadata dicts are missing keywords, tags, or filename.

    If so, look them up in the main searchable index (image_embeddings collection)
    and backfill them into both the in-memory dicts and the edit_training collection.
    """
    if not ids or not metadatas:
        return
    missing_ids = []
    for i, m in enumerate(metadatas):
        if not m:
            continue
        meta = dict(m) if not isinstance(m, dict) else m
        kws = (
            meta.get("user_keywords")
            or meta.get("keywords")
            or meta.get("flattened_keywords")
        )
        tags = meta.get("scene_tags") or meta.get("tags")
        if (not kws or kws in ("", "[]", "null", "None")) and (
            not tags or tags in ("", "[]", "null", "None")
        ):
            if i < len(ids):
                missing_ids.append(ids[i])

    if not missing_ids:
        return

    try:
        from services import chroma as chroma_service

        chroma_service._ensure_initialized()
        if chroma_service.collection is None:
            return
        main_res = chroma_service.collection.get(ids=missing_ids, include=["metadatas"])
        main_ids = main_res.get("ids") or []
        main_metas = main_res.get("metadatas") or []
        main_map = {
            main_ids[j]: main_metas[j]
            for j in range(len(main_ids))
            if j < len(main_metas) and main_metas[j]
        }

        updated_ids = []
        updated_metas = []
        for i, pid in enumerate(ids):
            if pid in main_map and i < len(metadatas) and metadatas[i]:
                mm = main_map[pid]
                meta = (
                    dict(metadatas[i])
                    if not isinstance(metadatas[i], dict)
                    else metadatas[i]
                )
                changed = False
                if not meta.get("filename") and mm.get("filename"):
                    meta["filename"] = mm["filename"]
                    changed = True
                kws_cur = (
                    meta.get("user_keywords")
                    or meta.get("keywords")
                    or meta.get("flattened_keywords")
                )
                if not kws_cur or kws_cur in ("", "[]", "null", "None"):
                    for k in ("user_keywords", "keywords", "flattened_keywords"):
                        val = mm.get(k)
                        if val and val not in ("", "[]", "null", "None"):
                            meta["user_keywords"] = val
                            changed = True
                            break
                tags_cur = meta.get("scene_tags") or meta.get("tags")
                if not tags_cur or tags_cur in ("", "[]", "null", "None"):
                    for k in ("scene_tags", "tags"):
                        val = mm.get(k)
                        if val and val not in ("", "[]", "null", "None"):
                            meta["scene_tags"] = val
                            changed = True
                            break
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
    try:
        result = _training_collection.get(include=["metadatas"], limit=1_000_000)
    except _ChromaInternalError:
        return []
    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    _enrich_and_sync_metadatas_from_main_index(ids, metadatas)
    examples = []
    for i, pid in enumerate(ids):
        meta = dict(metadatas[i]) if i < len(metadatas) else {}
        examples.append(
            {
                "photo_id": pid,
                "filename": meta.get("filename", "") or "",
                "label": meta.get("label", ""),
                "summary": meta.get("summary", ""),
                "captured_at": meta.get("captured_at", ""),
                "has_embedding": bool(meta.get("has_embedding", False)),
                "focal_length_bucket": meta.get("focal_length_bucket", "unknown"),
                "time_of_day_bucket": meta.get("time_of_day_bucket", "unknown"),
                "scene_tags": meta.get("scene_tags") or meta.get("tags") or "[]",
                "user_keywords": meta.get("user_keywords")
                or meta.get("keywords")
                or meta.get("flattened_keywords")
                or "[]",
                "camera_make": meta.get("camera_make", ""),
                "camera_model": meta.get("camera_model", ""),
                "camera_profile": meta.get("camera_profile", ""),
                "canonical_settings": meta.get("canonical_settings", "{}"),
                "develop_settings": meta.get("develop_settings", "{}"),
                "histogram_signature": meta.get("histogram_signature", "{}"),
                "dominant_colors": meta.get("dominant_colors", "[]"),
                "exp_luminance_mean": meta.get("exp_luminance_mean", "0.5"),
                "exp_contrast": meta.get("exp_contrast", "0.0"),
                "zone_deep_shadows": meta.get("zone_deep_shadows", "0.0"),
                "zone_shadows": meta.get("zone_shadows", "0.0"),
                "zone_midtones": meta.get("zone_midtones", "0.0"),
                "zone_highlights": meta.get("zone_highlights", "0.0"),
                "zone_bright_highlights": meta.get("zone_bright_highlights", "0.0"),
            }
        )
    examples.sort(key=lambda x: x["captured_at"], reverse=True)
    return examples


def get_training_stats() -> dict[str, Any]:
    """Return aggregate statistics over all training examples for the style profile UI.

    Returns:
        {
            "count": int,
            "has_enough_examples": bool,
            "readiness": "cold_start" | "limited" | "active",
            "scene_distribution": { "scene_portrait": 3, ... },
            "exposure": { "mean_luminance": 0.45, "mean_contrast": 0.6, ... },
            "focal_buckets": { "normal": 5, "tele": 2, ... },
            "time_of_day": { "afternoon": 7, ... },
        }
    """
    _ensure_initialized()
    if _training_collection is None:
        return {
            "count": 0,
            "has_enough_examples": False,
            "readiness": "cold_start",
            "scene_distribution": {},
            "focal_buckets": {},
            "time_of_day": {},
            "camera_distribution": {},
            "exposure": {},
        }
    try:
        result = _training_collection.get(include=["metadatas"], limit=1_000_000)
    except _ChromaInternalError:
        result = {}
    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    count = len(ids)

    scene_dist: dict[str, int] = {}
    focal_dist: dict[str, int] = {}
    tod_dist: dict[str, int] = {}
    camera_dist: dict[str, int] = {}
    exp_means: list[float] = []
    exp_contrasts: list[float] = []
    exp_colorfulness: list[float] = []

    for meta in metadatas:
        if not isinstance(meta, dict):
            continue
        tags = _safe_json_list(meta.get("scene_tags", "[]"))
        for tag in tags:
            scene_dist[tag] = scene_dist.get(tag, 0) + 1

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

    if count == 0:
        readiness = "cold_start"
    elif count < 10:
        readiness = "warming_up"
    elif count < 50:
        readiness = "limited"
    else:
        readiness = "active"

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
    try:
        from services import style_catalog

        styles = style_catalog.list_styles()
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

    return {
        "count": count,
        "has_enough_examples": count >= 10,
        "readiness": readiness,
        "scene_distribution": scene_dist,
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
        label, filename, summary, distance, scene_tags, exp_luminance_mean,
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
            include=["metadatas", "distances"],
        )
    except Exception as exc:
        logger.error("query_similar_training_examples failed: %s", exc, exc_info=True)
        return []

    ids = (result.get("ids") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

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
                "develop_settings": dev_settings,
                "canonical_settings": canonical_settings,
                "label": meta.get("label", ""),
                "filename": meta.get("filename", ""),
                "summary": meta.get("summary", ""),
                "distance": float(distances[i]) if i < len(distances) else 1.0,
                "scene_tags": _safe_json_list(meta.get("scene_tags", "[]")),
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
    try:
        result = _training_collection.get(include=[], limit=1_000_000)
    except _ChromaInternalError:
        return 0
    ids = result.get("ids") or []
    if not ids:
        return 0
    _training_collection.delete(ids=ids)
    logger.info("Cleared all %d training examples.", len(ids))
    try:
        from services import style_upgrades

        style_upgrades.invalidate_upgrade_recommendations_cache()
    except Exception:
        pass
    return len(ids)


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
