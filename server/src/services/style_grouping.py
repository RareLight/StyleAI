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
import math
import statistics
from typing import Any

from chromadb.utils import embedding_functions

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
    # Portraits & People & Pets
    "portrait": "scene_portrait",
    "people": "scene_portrait",
    "person": "scene_portrait",
    "headshot": "scene_portrait",
    "fashion": "scene_portrait",
    "newborn": "scene_portrait",
    "maternity": "scene_portrait",
    "baby": "scene_portrait",
    "baby portrait": "scene_portrait",
    "infant": "scene_portrait",
    "toddler": "scene_portrait",
    "family": "scene_portrait",
    "family portrait": "scene_portrait",
    "couple": "scene_portrait",
    "bridal": "scene_portrait",
    "pet": "scene_portrait",
    "pets": "scene_portrait",
    "pet portrait": "scene_portrait",
    "pet photography": "scene_portrait",
    "dog": "scene_portrait",
    "dogs": "scene_portrait",
    "cat": "scene_portrait",
    "cats": "scene_portrait",
    "puppy": "scene_portrait",
    "kitten": "scene_portrait",
    "animal": "scene_portrait",
    "animals": "scene_portrait",
    "tabby": "scene_portrait",
    "shorthair": "scene_portrait",
    "spaniel": "scene_portrait",
    "feline": "scene_portrait",
    "canine": "scene_portrait",
    "golden retriever": "scene_portrait",
    "retriever": "scene_portrait",
    "labrador": "scene_portrait",
    "poodle": "scene_portrait",
    "terrier": "scene_portrait",
    "bulldog": "scene_portrait",
    "shepherd": "scene_portrait",
    "husky": "scene_portrait",
    "doodle": "scene_portrait",
    "goldendoodle": "scene_portrait",
    "labradoodle": "scene_portrait",
    "beagle": "scene_portrait",
    "boxer": "scene_portrait",
    "corgi": "scene_portrait",
    "dachshund": "scene_portrait",
    "pointer": "scene_portrait",
    "setter": "scene_portrait",
    "collie": "scene_portrait",
    "rottweiler": "scene_portrait",
    "doberman": "scene_portrait",
    "mastiff": "scene_portrait",
    "chihuahua": "scene_portrait",
    "pug": "scene_portrait",
    "shih tzu": "scene_portrait",
    "schnauzer": "scene_portrait",
    "maltese": "scene_portrait",
    "pomeranian": "scene_portrait",
    "akita": "scene_portrait",
    "samoyed": "scene_portrait",
    "shiba inu": "scene_portrait",
    "whippet": "scene_portrait",
    "greyhound": "scene_portrait",
    "basset hound": "scene_portrait",
    "dalmatian": "scene_portrait",
    "great dane": "scene_portrait",
    "saint bernard": "scene_portrait",
    "newfoundland": "scene_portrait",
    "bernese mountain dog": "scene_portrait",
    "ragdoll": "scene_portrait",
    "persian": "scene_portrait",
    "siamese": "scene_portrait",
    "maine coon": "scene_portrait",
    "bengal": "scene_portrait",
    "sphynx": "scene_portrait",
    "british shorthair": "scene_portrait",
    "american shorthair": "scene_portrait",
    "scottish fold": "scene_portrait",
    "russian blue": "scene_portrait",
    # Landscapes & Scenery
    "landscape": "scene_landscape",
    "nature": "scene_landscape",
    "scenery": "scene_landscape",
    "seascape": "scene_landscape",
    "drone": "scene_landscape",
    "aerial": "scene_landscape",
    "golden_hour": "scene_golden_hour",
    "sunset": "scene_golden_hour",
    "sunrise": "scene_golden_hour",
    "waterfall": "scene_landscape",
    "mountain": "scene_landscape",
    "mountains": "scene_landscape",
    "forest": "scene_landscape",
    "desert": "scene_landscape",
    "beach": "scene_landscape",
    "ocean": "scene_landscape",
    "river": "scene_landscape",
    "lake": "scene_landscape",
    "canyon": "scene_landscape",
    "valley": "scene_landscape",
    "glacier": "scene_landscape",
    "cliff": "scene_landscape",
    "dune": "scene_landscape",
    "dunes": "scene_landscape",
    "sand dunes": "scene_landscape",
    "winter landscape": "scene_landscape",
    "snowy": "scene_landscape",
    "vista": "scene_landscape",
    # Nature & Wildlife & Macro
    "wildlife": "scene_wildlife",
    "bird": "scene_wildlife",
    "birds": "scene_wildlife",
    "macro": "scene_macro",
    "close_up": "scene_macro",
    "detail": "scene_macro",
    "flower": "scene_macro",
    "flowers": "scene_macro",
    "botanical": "scene_macro",
    "flora": "scene_macro",
    "fauna": "scene_wildlife",
    "insect": "scene_macro",
    "insects": "scene_macro",
    "bug": "scene_macro",
    "bugs": "scene_macro",
    "beetle": "scene_macro",
    "butterfly": "scene_macro",
    "bee": "scene_macro",
    "spider": "scene_macro",
    # Architecture & Real Estate & Property
    "architecture": "scene_architecture",
    "building": "scene_architecture",
    "buildings": "scene_architecture",
    "real estate": "scene_architecture",
    "property": "scene_architecture",
    "realtor": "scene_architecture",
    "interior design": "scene_architecture",
    "monument": "scene_architecture",
    "bridge": "scene_architecture",
    "staircase": "scene_architecture",
    "facade": "scene_architecture",
    # Studio & Product & Toy & Commercial & Automotive
    "studio": "scene_studio",
    "product": "scene_studio",
    "product photography": "scene_studio",
    "product shot": "scene_studio",
    "food": "scene_studio",
    "food photography": "scene_studio",
    "culinary": "scene_studio",
    "beverage": "scene_studio",
    "drink": "scene_studio",
    "drinks": "scene_studio",
    "cocktail": "scene_studio",
    "meal": "scene_studio",
    "toy": "scene_studio",
    "toy photography": "scene_studio",
    "lego": "scene_studio",
    "advertisement": "scene_studio",
    "vintage advertisement": "scene_studio",
    "automotive": "scene_studio",
    "car": "scene_studio",
    "car photography": "scene_studio",
    "motorcycle": "scene_studio",
    "vehicle": "scene_studio",
    "supercar": "scene_studio",
    # Events & Weddings & Concerts
    "event": "scene_event",
    "wedding": "scene_event",
    "party": "scene_event",
    "concert": "scene_event",
    "festival": "scene_event",
    "reception": "scene_event",
    "ceremony": "scene_event",
    "gala": "scene_event",
    "conference": "scene_event",
    "banquet": "scene_event",
    "candid": "scene_event",
    "candid event": "scene_event",
    "gathering": "scene_event",
    "group": "scene_event",
    # Street & Urban & Documentary
    "street": "scene_street",
    "urban": "scene_street",
    "city": "scene_street",
    "street photography": "scene_street",
    "documentary": "scene_street",
    "photojournalism": "scene_street",
    "candid street": "scene_street",
    "graffiti": "scene_street",
    "alley": "scene_street",
    "ferris wheel": "scene_street",
    "amusement park": "scene_street",
    # Action & Sports & Athletics
    "sports": "scene_action",
    "action": "scene_action",
    "athletics": "scene_action",
    "runner": "scene_action",
    "running": "scene_action",
    "surfing": "scene_action",
    "motorsport": "scene_action",
    "race": "scene_action",
    # Night & Astrophotography
    "night": "scene_night",
    "astrophotography": "scene_astrophotography",
    "nightscape": "scene_astrophotography",
    "aurora": "scene_astrophotography",
    "aurora borealis": "scene_astrophotography",
    "northern lights": "scene_astrophotography",
    "stars": "scene_astrophotography",
    "star trails": "scene_astrophotography",
    "milky way": "scene_astrophotography",
    "deep sky": "scene_astrophotography",
    "nebula": "scene_astrophotography",
    "eclipse": "scene_astrophotography",
    "lunar": "scene_astrophotography",
}

_BROAD_GENRE_MAP: dict[str, str] = {
    "scene_portrait": "scene_portrait",
    "scene_group": "scene_event",
    "scene_event": "scene_event",
    "scene_action": "scene_action",
    "scene_street": "scene_street",
    "scene_landscape": "scene_landscape",
    "scene_exterior": "scene_landscape",
    "scene_golden_hour": "scene_landscape",
    "scene_nature": "scene_nature",
    "scene_macro": "scene_macro",
    "scene_flowers": "scene_macro",
    "scene_wildlife": "scene_nature",
    "scene_architecture": "scene_architecture",
    "scene_studio": "scene_studio",
    "scene_interior": "scene_architecture",
    "scene_night": "scene_night",
    "scene_astrophotography": "scene_astrophotography",
}


_DYNAMIC_BUCKETS = {
    "scene_portrait": "portrait, people, family, fashion, headshot, baby, newborn, maternity, pet, pets, dog, cat, animal, puppy, kitten",
    "scene_event": "wedding, event, concert, ceremony, reception, party, conference, gala, banquet, festival, candid, group, gathering",
    "scene_landscape": "landscape, outdoors, travel, sunset, sunrise, scenery, vista, mountains, ocean, seascape, drone, aerial",
    "scene_nature": "wildlife, plants, birds, fauna, flora, trees, forest, wilderness",
    "scene_macro": "macro, close-up, extreme detail, insect, bug, beetle, spider, butterfly, flower detail, water droplet, botanical closeup",
    "scene_architecture": "architecture, real estate, interior design, building, house, property, monument, bridge, structure",
    "scene_studio": "studio, product, food, culinary, commercial, controlled light, flash, still life, toy photography, lego, car, automotive",
    "scene_street": "street photography, urban life, documentary, photojournalism, city street, candid street, alley, graffiti, urban environment",
    "scene_action": "sports, action, athletics, runner, surfing, motorsport, fast motion, dynamic movement, extreme sports",
    "scene_night": "night time, evening, after dark, city lights at night, dark ambiance",
    "scene_astrophotography": "astrophotography, nightscape, night sky, milky way, aurora borealis, northern lights, stars, star trails, telescope, deep sky",
}

_DYNAMIC_GENRE_CACHE: dict[str, str] = {}
_ef_instance = None
_bucket_embeddings = None


def _dynamic_semantic_mapping(keyword: str) -> str:
    """Use SentenceTransformer to dynamically map an unknown keyword to a broad bucket."""
    global _ef_instance, _bucket_embeddings

    keyword_lower = keyword.strip().lower()
    if keyword_lower in _DYNAMIC_GENRE_CACHE:
        return _DYNAMIC_GENRE_CACHE[keyword_lower]

    if _ef_instance is None:
        _ef_instance = embedding_functions.DefaultEmbeddingFunction()
        _bucket_embeddings = _ef_instance(list(_DYNAMIC_BUCKETS.values()))

    # Embed keyword
    kw_emb = _ef_instance([keyword_lower])[0]

    # Compute cosine distances
    distances = []
    for b_emb in _bucket_embeddings:
        a = kw_emb
        b = b_emb
        # 1 - cosine similarity
        dist = 1 - sum(x * y for x, y in zip(a, b)) / (
            math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        )
        distances.append(dist)

    closest_idx = min(range(len(distances)), key=distances.__getitem__)
    closest_dist = distances[closest_idx]

    # Only map if it's semantically close enough (e.g. cosine distance < 0.85)
    # This prevents keywords like "Dave" or "Red" from overriding the AI tag
    if closest_dist < 0.75:
        closest_bucket = list(_DYNAMIC_BUCKETS.keys())[closest_idx]
    else:
        closest_bucket = None

    _DYNAMIC_GENRE_CACHE[keyword_lower] = closest_bucket
    return closest_bucket


def _get_broad_genre(tag: str) -> str:
    """Map a specific scene tag to a broad bucket."""
    return _BROAD_GENRE_MAP.get(tag, "scene_unknown")


def _extract_keyword_strings(val: Any) -> list[str]:
    """Extract individual keyword strings from string, list, dict, or JSON-string representations."""
    if val is None:
        return []
    if isinstance(val, str):
        parsed = _safe_json_loads(val, None)
        if parsed is not None and not isinstance(parsed, str):
            val = parsed
        else:
            return [w.strip() for w in val.split(",") if w.strip()]

    words: list[str] = []
    if isinstance(val, dict):
        priority_keys = (
            "people",
            "animals",
            "subject",
            "genre",
            "category",
            "scene",
            "sceneries",
            "scenery",
            "location",
            "setting",
            "environment",
        )
        for pk in priority_keys:
            for k, v in val.items():
                if k.lower() == pk:
                    if isinstance(v, list):
                        for item in v:
                            if isinstance(item, str) and item.strip():
                                words.append(item.strip())
                    elif isinstance(v, str) and v.strip():
                        words.append(v.strip())
        for k, v in val.items():
            if k.lower() not in priority_keys:
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item.strip():
                            words.append(item.strip())
                elif isinstance(v, str) and v.strip():
                    words.append(v.strip())
    elif isinstance(val, (list, tuple, set)):
        for item in val:
            if isinstance(item, str) and item.strip():
                words.append(item.strip())
    return words


def _primary_genre(scene_tags: Any) -> str:
    """Return the primary broad genre, ignoring stylistic tags."""
    return _primary_genre_with_keywords(scene_tags, None)


def _primary_genre_with_keywords(scene_tags: Any, user_keywords: Any) -> str:
    """Return the primary broad genre using hierarchical domain evaluation."""
    kw_list = _extract_keyword_strings(user_keywords)
    tag_list = _extract_keyword_strings(scene_tags)
    content_tags = [t for t in tag_list if not t.startswith("style_")]

    if kw_list:
        tier_order = [
            "scene_studio",
            "scene_macro",
            "scene_event",
            "scene_portrait",
            "scene_nature",
            "scene_street",
            "scene_architecture",
            "scene_astrophotography",
            "scene_night",
            "scene_action",
            "scene_landscape",
        ]

        for target_genre in tier_order:
            for t in kw_list:
                t_lower = t.lower()
                if (
                    _BROAD_GENRE_MAP.get(t_lower) == target_genre
                    or _BROAD_GENRE_MAP.get(t) == target_genre
                ):
                    return target_genre
                mapped = _KEYWORD_TO_GENRE.get(t_lower)
                if mapped and _get_broad_genre(mapped) == target_genre:
                    return target_genre
                for k, genre_val in _KEYWORD_TO_GENRE.items():
                    if _get_broad_genre(genre_val) == target_genre and len(k) >= 3:
                        if (
                            f" {k} " in f" {t_lower} "
                            or t_lower.startswith(f"{k} ")
                            or t_lower.endswith(f" {k}")
                        ):
                            return target_genre

        # Setting fallback: if no subject/domain matched in tiers, check setting keywords
        setting_arch_words = {
            "indoor",
            "interior",
            "room",
            "living room",
            "bedroom",
            "dining room",
            "home",
            "hallway",
            "house",
            "structure",
            "building",
            "real estate",
        }
        setting_land_words = {
            "outdoor",
            "exterior",
            "outdoors",
            "outside",
            "scenery",
            "vista",
        }
        for t in kw_list:
            t_lower = t.lower()
            if any(w in t_lower for w in setting_arch_words):
                return "scene_architecture"
            if any(w in t_lower for w in setting_land_words):
                return "scene_landscape"

        # For unknown user keywords, dynamically semantic map them
        for kw in kw_list:
            if len(kw.strip()) > 1:
                mapped_bucket = _dynamic_semantic_mapping(kw)
                if mapped_bucket:
                    return mapped_bucket

        # Fall back to first available tag mapped in kw_list
        for t in kw_list:
            mapped = _get_broad_genre(t)
            if mapped != "scene_unknown":
                return mapped

    if content_tags:
        primary_mapped = _get_broad_genre(content_tags[0])
        subject_tiers = {
            "scene_studio",
            "scene_macro",
            "scene_event",
            "scene_portrait",
            "scene_nature",
            "scene_action",
        }
        if primary_mapped in subject_tiers:
            return primary_mapped

        # If primary AI tag is a background setting, check if any AI tag indicates an animate subject / studio / macro domain
        tier_order_subjects = [
            "scene_studio",
            "scene_macro",
            "scene_event",
            "scene_portrait",
            "scene_action",
        ]
        for target_genre in tier_order_subjects:
            for t in content_tags:
                t_lower = t.lower()
                if (
                    _BROAD_GENRE_MAP.get(t_lower) == target_genre
                    or _BROAD_GENRE_MAP.get(t) == target_genre
                ):
                    return target_genre
                mapped = _KEYWORD_TO_GENRE.get(t_lower)
                if mapped and _get_broad_genre(mapped) == target_genre:
                    return target_genre

        return primary_mapped

    return "scene_unknown"


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


def group_examples_by_profile_genre(
    examples: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group training examples by (profile, genre).

    Args:
        examples: List of training-example metadata dicts (from ChromaDB).

    Returns:
        Dict keyed by (profile, genre) → list of examples.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ex in examples:
        profile = _profile_name(ex.get("camera_profile"))
        scene_tags = ex.get("scene_tags") or ex.get("tags")
        user_keywords = (
            ex.get("user_keywords")
            or ex.get("keywords")
            or ex.get("flattened_keywords")
        )
        genre = _primary_genre_with_keywords(scene_tags, user_keywords)
        key = (profile, genre)
        groups.setdefault(key, []).append(ex)
    return groups


def split_subgenres(
    group_examples: list[dict[str, Any]],
    variance_threshold: float = DEFAULT_VARIANCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Auto-split a group of examples into subgenres when develop variance is high.

    Args:
        group_examples: List of examples sharing the same (profile, genre).
        variance_threshold: Maximum acceptable *normalised* variance before splitting.

    Returns:
        List of subgroup descriptors (see _build_subgroup).
    """
    if len(group_examples) < 3:
        return [_build_subgroup(group_examples, subgenre=None)]
    return _auto_split(group_examples, variance_threshold)


def generate_style_name(
    genre: str,
    subgenre: str | None,
) -> str:
    """Generate a human-readable style name based on genre.

    Examples:
        "Architecture & City"
        "Portrait (Natural Light)"
        "Landscape (Med++)"
    """
    # Clean up genre tag (remove "scene_" prefix, title-case)
    clean_genre = genre.replace("scene_", "").replace("_", " ").title()

    parts = [clean_genre]

    if subgenre and subgenre != "unknown":
        clean_sub = subgenre.replace("scene_", "").replace("_", " ").title()
        parts.append(f"({clean_sub})")

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
