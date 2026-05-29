"""Style grouping and auto-discovery logic.

Groups training examples by (camera + profile + genre), splits subgenres
when develop-settings variance exceeds a threshold, and generates
human-readable style names and descriptions.

Variance is computed on *normalized* slider values so that a raw variance
of 300 for Contrast (+/-100 scale) and a raw variance of 0.3 for Exposure
(+/-5 scale) can be compared on equal footing.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical develop keys used for variance-based subgenre splitting.
# These are the most style-defining sliders.
VARIANCE_KEYS = (
    "exposure",
    "contrast",
    "temperature",
    "highlights",
    "shadows",
    "clarity",
    "dehaze",
)

# Approximate half-ranges for each slider so we can normalise values to
# a roughly comparable 0..1 scale before computing variance.
#  e.g. Contrast raw value ÷ 100  → normalised value
#       Exposure raw value ÷ 5    → normalised value
_SLIDER_RANGES: dict[str, float] = {
    "exposure": 5.0,
    "contrast": 100.0,
    "temperature": 10000.0,  # raw Kelvin, but deltas matter more
    "highlights": 100.0,
    "shadows": 100.0,
    "clarity": 100.0,
    "dehaze": 100.0,
}

DEFAULT_VARIANCE_THRESHOLD = 0.15


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_json_loads(value: str | None, default: Any = None) -> Any:
    """Safely parse a JSON string; return *default* on failure."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


# Mapping from common user keywords to canonical scene tags.
# When a user keyword matches, it overrides the AI-detected scene tag.
_KEYWORD_TO_GENRE: dict[str, str] = {
    "portrait": "scene_portrait",
    "people": "scene_portrait",
    "person": "scene_portrait",
    "headshot": "scene_portrait",
    "landscape": "scene_landscape",
    "nature": "scene_landscape",
    "scenery": "scene_landscape",
    "architecture": "scene_architecture",
    "building": "scene_architecture",
    "interior": "scene_interior",
    "indoor": "scene_interior",
    "exterior": "scene_exterior",
    "outdoor": "scene_exterior",
    "wildlife": "scene_wildlife",
    "animal": "scene_wildlife",
    "bird": "scene_wildlife",
    "macro": "scene_macro",
    "close_up": "scene_macro",
    "detail": "scene_macro",
    "street": "scene_street",
    "urban": "scene_street",
    "city": "scene_street",
    "event": "scene_event",
    "wedding": "scene_event",
    "party": "scene_event",
    "sports": "scene_action",
    "action": "scene_action",
    "studio": "scene_studio",
    "golden_hour": "scene_golden_hour",
    "sunset": "scene_golden_hour",
    "sunrise": "scene_golden_hour",
}


def _primary_genre(scene_tags: list[str]) -> str:
    """Return the primary genre (first tag) or 'scene_unknown'."""
    return scene_tags[0] if scene_tags else "scene_unknown"


def _primary_genre_with_keywords(
    scene_tags: list[str], user_keywords: list[str]
) -> str:
    """Return the primary genre, preferring user keywords over AI tags.

    Args:
        scene_tags: AI-detected scene tags (e.g. ["scene_landscape"]).
        user_keywords: Normalized user keywords (e.g. ["macro", "nature"]).

    Returns:
        Canonical genre tag like "scene_macro" or "scene_unknown".
    """
    # 1. Try to map user keywords to known genres
    for kw in user_keywords:
        mapped = _KEYWORD_TO_GENRE.get(kw)
        if mapped:
            return mapped

    # 2. Fall back to AI scene tags
    return scene_tags[0] if scene_tags else "scene_unknown"


def _camera_id(camera_make: str | None, camera_model: str | None) -> str:
    """Build a stable camera identifier string."""
    make = (camera_make or "unknown").strip()
    model = (camera_model or "unknown").strip()
    return f"{make} {model}".strip()


def _profile_name(camera_profile: str | None) -> str:
    """Normalise a camera-profile string for grouping."""
    return (camera_profile or "default").strip()


def _normalise_value(raw: float, key: str) -> float:
    """Scale a raw slider value to a comparable 0..1-ish space."""
    divisor = _SLIDER_RANGES.get(key, 100.0)
    if divisor == 0:
        return 0.0
    return float(raw) / divisor


# ---------------------------------------------------------------------------
# Variance computation (normalised)
# ---------------------------------------------------------------------------


def _compute_variance(examples: list[dict[str, Any]], key: str) -> float:
    """Compute population variance for a single canonical develop key.

    Values are normalised by their typical slider range so that
    Contrast (+/-100) and Exposure (+/-5) can be compared fairly.
    """
    values: list[float] = []
    for ex in examples:
        canonical = _safe_json_loads(ex.get("canonical_settings"), {})
        if isinstance(canonical, dict) and key in canonical:
            val = canonical[key]
            if isinstance(val, (int, float)):
                values.append(_normalise_value(float(val), key))
    if len(values) < 2:
        return 0.0
    return statistics.pvariance(values)


# ---------------------------------------------------------------------------
# Histogram-based style similarity (profile-independent)
# ---------------------------------------------------------------------------


def _max_variance(examples: list[dict[str, Any]]) -> float:
    """Return the largest *normalised* variance across all VARIANCE_KEYS."""
    return max(
        (_compute_variance(examples, key) for key in VARIANCE_KEYS),
        default=0.0,
    )


def _load_histogram_signature(example: dict[str, Any]) -> dict[str, Any]:
    """Load histogram signature from example metadata."""
    raw = example.get("histogram_signature", "{}")
    return _safe_json_loads(raw, {})


def _histogram_distance(sig1: dict[str, Any], sig2: dict[str, Any]) -> float:
    """Compute distance between two histogram signatures (0..1)."""
    try:
        import numpy as np
    except ImportError:
        return 1.0

    # Chi-square on histogram bins
    chi_sq = 0.0
    for key in ("hist_L", "hist_a", "hist_b"):
        h1 = np.array(sig1.get(key, []), dtype=np.float32)
        h2 = np.array(sig2.get(key, []), dtype=np.float32)
        if len(h1) == 0 or len(h2) == 0 or len(h1) != len(h2):
            continue
        denom = h1 + h2 + 1e-8
        diff = h1 - h2
        chi_sq += float(np.sum(diff**2 / denom))

    # Normalize (empirical max ~4.0 for these bin counts)
    chi_component = min(1.0, chi_sq / 4.0)

    # Euclidean on summary stats
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

    return 0.6 * chi_component + 0.4 * stat_dist


def _max_histogram_distance(examples: list[dict[str, Any]]) -> float:
    """Return maximum pairwise histogram distance among examples.

    If no histograms are available, falls back to develop variance.
    """
    sigs = [_load_histogram_signature(ex) for ex in examples]
    valid = [s for s in sigs if s.get("hist_L")]

    if len(valid) < 2:
        # Fallback: use develop variance when histograms unavailable
        return _max_variance(examples)

    # Compute max pairwise distance (O(n²) but n is small)
    max_dist = 0.0
    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            dist = _histogram_distance(valid[i], valid[j])
            if dist > max_dist:
                max_dist = dist
    return max_dist


def _cluster_by_histogram(
    examples: list[dict[str, Any]], distance_threshold: float = 0.35
) -> dict[str, list[dict]]:
    """Cluster examples by histogram similarity using greedy agglomeration.

    Groups examples whose histogram distance is below threshold.
    Returns dict of cluster_name → list of examples.
    """
    if len(examples) < 2:
        return {"cluster_0": examples}

    sigs = [_load_histogram_signature(ex) for ex in examples]
    valid_indices = [i for i, s in enumerate(sigs) if s.get("hist_L")]

    if len(valid_indices) < 2:
        # No histograms available — fall back to exposure bucket
        return _split_by_exposure_bucket(examples)

    # Greedy clustering: start with each example as its own cluster,
    # then merge closest pairs below threshold
    clusters: list[list[int]] = [[i] for i in valid_indices]

    while True:
        best_merge = None
        best_dist = float("inf")

        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                # Average linkage: mean distance between all pairs
                total_dist = 0.0
                count = 0
                for ii in clusters[i]:
                    for jj in clusters[j]:
                        d = _histogram_distance(sigs[ii], sigs[jj])
                        total_dist += d
                        count += 1
                avg_dist = total_dist / count if count > 0 else 1.0
                if avg_dist < best_dist and avg_dist < distance_threshold:
                    best_dist = avg_dist
                    best_merge = (i, j)

        if best_merge is None:
            break

        i, j = best_merge
        clusters[i] = clusters[i] + clusters[j]
        clusters.pop(j)

    # Map back to examples
    result: dict[str, list[dict]] = {}
    for idx, cluster in enumerate(clusters):
        key = f"cluster_{idx}"
        result[key] = [examples[i] for i in cluster]

    return result


# ---------------------------------------------------------------------------
# Subgenre splitting strategies
# ---------------------------------------------------------------------------


def _split_by_secondary_tag(examples: list[dict[str, Any]]) -> dict[str, list[dict]]:
    """Try to split examples by their secondary scene tag."""
    groups: dict[str, list[dict]] = {}
    for ex in examples:
        tags = _safe_json_loads(ex.get("scene_tags"), [])
        # Use second tag if present, otherwise 'unknown'
        sub = tags[1] if len(tags) > 1 else "unknown"
        groups.setdefault(sub, []).append(ex)
    return groups


def _split_by_exposure_bucket(examples: list[dict[str, Any]]) -> dict[str, list[dict]]:
    """Split examples into Bright vs Dramatic/Moody based on mean luminance."""
    groups: dict[str, list[dict]] = {"bright": [], "dramatic": []}
    for ex in examples:
        lum = float(ex.get("exp_luminance_mean", 0.5))
        bucket = "bright" if lum > 0.5 else "dramatic"
        groups[bucket].append(ex)
    return groups


def _auto_split(
    examples: list[dict[str, Any]],
    variance_threshold: float = DEFAULT_VARIANCE_THRESHOLD,
    max_depth: int = 2,
    current_depth: int = 0,
    inherited_subgenre: str | None = None,
) -> list[dict[str, Any]]:
    """Recursively split a group of examples until similarity is acceptable.

    Uses histogram distance when JPEG previews are available;
    falls back to develop variance when only XMP/slider data is present.

    Returns a list of subgroup dicts, each with:
        - subgenre (str | None)
        - examples (list[dict])
        - mean_develop_settings (dict[str, float])
        - variance (float)
        - example_photo_ids (list[str])
    """
    # Check if histograms are available for most examples
    has_histograms = (
        sum(1 for ex in examples if _load_histogram_signature(ex).get("hist_L"))
        >= len(examples) // 2
    )

    if has_histograms:
        # Use histogram distance as primary splitting criterion
        current_distance = _max_histogram_distance(examples)
        distance_threshold = 0.35  # generous threshold for histogram distance

        if (
            current_distance <= distance_threshold
            or current_depth >= max_depth
            or len(examples) < 3
        ):
            return [_build_subgroup(examples, subgenre=inherited_subgenre)]

        # Strategy 1: histogram-based clustering (most robust)
        hist_clusters = _cluster_by_histogram(examples, distance_threshold=0.30)
        if len(hist_clusters) > 1 and all(len(g) >= 2 for g in hist_clusters.values()):
            result: list[dict[str, Any]] = []
            for subgenre, group in hist_clusters.items():
                result.extend(
                    _auto_split(
                        group,
                        variance_threshold,
                        max_depth,
                        current_depth + 1,
                        inherited_subgenre=subgenre,
                    )
                )
            return result

    else:
        # Fallback: use develop variance when histograms unavailable
        current_variance = _max_variance(examples)
        if (
            current_variance <= variance_threshold
            or current_depth >= max_depth
            or len(examples) < 3
        ):
            return [_build_subgroup(examples, subgenre=inherited_subgenre)]

    # Strategy 2: split by secondary scene tag
    tag_groups = _split_by_secondary_tag(examples)
    if len(tag_groups) > 1 and all(len(g) >= 2 for g in tag_groups.values()):
        result = []
        for subgenre, group in tag_groups.items():
            result.extend(
                _auto_split(
                    group,
                    variance_threshold,
                    max_depth,
                    current_depth + 1,
                    inherited_subgenre=subgenre,
                )
            )
        return result

    # Strategy 3: split by exposure bucket
    exp_groups = _split_by_exposure_bucket(examples)
    if len(exp_groups) > 1 and all(len(g) >= 2 for g in exp_groups.values()):
        result = []
        for subgenre, group in exp_groups.items():
            result.extend(
                _auto_split(
                    group,
                    variance_threshold,
                    max_depth,
                    current_depth + 1,
                    inherited_subgenre=subgenre,
                )
            )
        return result

    # Fallback: return as-is
    return [_build_subgroup(examples, subgenre=inherited_subgenre)]

    # Strategy 1: split by secondary scene tag
    tag_groups = _split_by_secondary_tag(examples)
    if len(tag_groups) > 1 and all(len(g) >= 2 for g in tag_groups.values()):
        result: list[dict[str, Any]] = []
        for subgenre, group in tag_groups.items():
            result.extend(
                _auto_split(
                    group,
                    variance_threshold,
                    max_depth,
                    current_depth + 1,
                    inherited_subgenre=subgenre,
                )
            )
        return result

    # Strategy 2: split by exposure bucket
    exp_groups = _split_by_exposure_bucket(examples)
    if len(exp_groups) > 1 and all(len(g) >= 2 for g in exp_groups.values()):
        result = []
        for subgenre, group in exp_groups.items():
            result.extend(
                _auto_split(
                    group,
                    variance_threshold,
                    max_depth,
                    current_depth + 1,
                    inherited_subgenre=subgenre,
                )
            )
        return result

    # Cannot split effectively
    return [_build_subgroup(examples, subgenre=inherited_subgenre)]


# ---------------------------------------------------------------------------
# Subgroup builder
# ---------------------------------------------------------------------------


def _build_subgroup(
    examples: list[dict[str, Any]], subgenre: str | None
) -> dict[str, Any]:
    """Build a subgroup descriptor from a list of examples."""
    photo_ids = [ex["photo_id"] for ex in examples]

    # Compute mean develop settings (raw values, not normalised)
    mean_settings: dict[str, float] = {}
    for key in VARIANCE_KEYS:
        values: list[float] = []
        for ex in examples:
            canonical = _safe_json_loads(ex.get("canonical_settings"), {})
            if isinstance(canonical, dict) and key in canonical:
                val = canonical[key]
                if isinstance(val, (int, float)):
                    values.append(float(val))
        if values:
            mean_settings[key] = round(sum(values) / len(values), 4)

    # Compute *normalised* variance per key for reporting
    variance: dict[str, float] = {}
    for key in VARIANCE_KEYS:
        variance[key] = round(_compute_variance(examples, key), 6)

    # Scene tag distribution within subgroup
    tag_counts: dict[str, int] = {}
    for ex in examples:
        tags = _safe_json_loads(ex.get("scene_tags"), [])
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    total_tags = sum(tag_counts.values()) or 1
    scene_distribution = {
        tag: round(count / total_tags, 3) for tag, count in tag_counts.items()
    }

    # Mean exposure DNA
    exp_metrics = {}
    for metric_key in ("exp_luminance_mean", "exp_contrast", "exp_warmth_proxy"):
        vals = [float(ex.get(metric_key, 0.5)) for ex in examples if metric_key in ex]
        if vals:
            exp_metrics[metric_key] = round(sum(vals) / len(vals), 4)

    # Camera profile(s) used in this subgroup
    profiles = {ex.get("camera_profile", "") or "" for ex in examples}
    profiles.discard("")
    camera_profile = next(iter(profiles)) if len(profiles) == 1 else None

    return {
        "subgenre": subgenre,
        "examples": examples,
        "mean_develop_settings": mean_settings,
        "variance": variance,
        "example_photo_ids": photo_ids,
        "scene_distribution": scene_distribution,
        "mean_exposure_dna": exp_metrics,
        "camera_profile": camera_profile,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def group_examples_by_camera_genre(
    examples: list[dict[str, Any]],
) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    """Group training examples by (camera_make, camera_model, profile, genre).

    Args:
        examples: List of training-example metadata dicts (from ChromaDB).

    Returns:
        Dict keyed by (camera_make, camera_model, profile, genre) → list of examples.
    """
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for ex in examples:
        camera_make = (ex.get("camera_make") or "").strip()
        camera_model = (ex.get("camera_model") or "").strip()
        profile = _profile_name(ex.get("camera_profile"))
        scene_tags = _safe_json_loads(ex.get("scene_tags"), [])
        user_keywords = _safe_json_loads(ex.get("user_keywords"), [])
        genre = _primary_genre_with_keywords(scene_tags, user_keywords)
        key = (camera_make, camera_model, profile, genre)
        groups.setdefault(key, []).append(ex)
    return groups


def split_subgenres(
    group_examples: list[dict[str, Any]],
    variance_threshold: float = DEFAULT_VARIANCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Auto-split a group of examples into subgenres when develop variance is high.

    Args:
        group_examples: List of examples sharing the same (camera, profile, genre).
        variance_threshold: Maximum acceptable *normalised* variance before splitting.

    Returns:
        List of subgroup descriptors (see _build_subgroup).
    """
    if len(group_examples) < 3:
        return [_build_subgroup(group_examples, subgenre=None)]
    return _auto_split(group_examples, variance_threshold)


def generate_style_name(
    camera_model: str,
    genre: str,
    subgenre: str | None,
    camera_profile: str | None = None,
) -> str:
    """Generate a human-readable style name.

    Examples:
        "Nikon Z 7 — Architecture & City"
        "Nikon Z 7 — Portrait (Natural Light)"
        "Nikon Z 7 — Landscape [AgX-Like Med++]"
    """
    # Clean up genre tag (remove "scene_" prefix, title-case)
    clean_genre = genre.replace("scene_", "").replace("_", " ").title()

    parts = [f"{camera_model} — {clean_genre}"]

    if subgenre and subgenre != "unknown":
        clean_sub = subgenre.replace("scene_", "").replace("_", " ").title()
        parts.append(f"({clean_sub})")

    if camera_profile and camera_profile != "default":
        # Abbreviate long profile names
        short_profile = camera_profile
        if "AgX-Like" in short_profile:
            short_profile = short_profile.replace("Nikon Z7 ", "")
        if len(short_profile) > 30:
            short_profile = short_profile[:27] + "..."
        parts.append(f"[{short_profile}]")

    return " ".join(parts)


def generate_style_description(
    mean_settings: dict[str, float],
    genre: str,
    scene_distribution: dict[str, float],
    camera_profile: str | None = None,
) -> str:
    """Generate a short human-readable description of a style.

    Uses mean develop settings and genre to characterize the style.
    """
    parts: list[str] = []

    # Characterize overall look
    contrast = mean_settings.get("contrast", 0.0)
    if contrast > 10:
        parts.append("high-contrast")
    elif contrast < -5:
        parts.append("low-contrast")
    else:
        parts.append("medium-contrast")

    temp = mean_settings.get("temperature", 0.0)
    if temp > 5500:
        parts.append("warm")
    elif temp < 5000:
        parts.append("cool")

    clarity = mean_settings.get("clarity", 0.0)
    if clarity > 15:
        parts.append("punchy clarity")
    elif clarity > 5:
        parts.append("medium clarity")

    dehaze = mean_settings.get("dehaze", 0.0)
    if dehaze > 10:
        parts.append("strong dehaze")
    elif dehaze > 5:
        parts.append("subtle dehaze")

    clean_genre = genre.replace("scene_", "").replace("_", " ")
    desc = f"Your typical {clean_genre} editing style: {', '.join(parts)}."

    # Mention camera profile if present
    if camera_profile and camera_profile != "default":
        desc += f" Uses the {camera_profile} camera profile."

    # Add most common sub-scenes if any
    if scene_distribution:
        top_scenes = sorted(
            scene_distribution.items(), key=lambda x: x[1], reverse=True
        )[:2]
        scene_names = [s.replace("scene_", "").replace("_", " ") for s, _ in top_scenes]
        if scene_names:
            desc += f" Commonly includes: {', '.join(scene_names)}."

    return desc
