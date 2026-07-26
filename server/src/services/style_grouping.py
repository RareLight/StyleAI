"""Style grouping and auto-discovery logic.

Groups training examples by (camera + profile + genre), splits subgenres
when develop-settings variance exceeds a threshold, and generates
human-readable style names and descriptions.

Variance is computed on *normalized* slider values so that a raw variance
of 300 for Contrast (+/-100 scale) and a raw variance of 0.3 for Exposure
(+/-5 scale) can be compared on equal footing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import statistics
from typing import Any

from chromadb.utils import embedding_functions
import numpy as np

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
# Shared Visual Similarity Thresholds
# ---------------------------------------------------------------------------
# Both the Style Discovery pipeline (group_examples_by_profile_genre) and the
# Upgrade Recommendations pipeline (_select_style_recommendations) use these
# thresholds to enforce visually coherent style groupings.
#
# VISUAL_MIN_SIMILARITY: Minimum cosine similarity for a photo to be
#     considered visually related to a style cluster.
# VISUAL_STRICT_SIMILARITY: Elevated threshold applied when text-based genre
#     classification is ambiguous (scene_unknown / scene_general).
# BURST_COSINE_DISTANCE: Maximum cosine distance (1 - sim) to consider two
#     embeddings near-duplicates (burst shots).
# VISUAL_REASSIGN_MARGIN: During discovery, a photo is re-assigned to a
#     different style group only if the competing centroid similarity exceeds
#     the assigned centroid similarity by at least this margin.
VISUAL_MIN_SIMILARITY = 0.45
VISUAL_STRICT_SIMILARITY = 0.60
BURST_COSINE_DISTANCE = 0.05
VISUAL_REASSIGN_MARGIN = 0.05


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


# LLM-generated compositional and descriptive phrases that should be stripped
# so they do not hijack primary subject genre classification.
_LLM_NOISE_VOCABULARY: set[str] = {
    "portrait orientation",
    "landscape orientation",
    "action shot",
    "street vibe",
    "urban vibe",
    "dramatic lighting",
    "color graded",
    "high contrast",
    "low contrast",
    "natural light",
    "golden hour lighting",
    "cinematic",
    "cinematic lighting",
    "moody",
    "vibrant",
    "desaturated",
    "black and white",
    "monochrome",
    "depth of field",
    "shallow depth of field",
    "bokeh",
    "daylight",
    "daytime",
    "grass",
    "trees",
    "water",
    "background",
    "foreground",
    "lighting",
    "weather",
}


def _filter_llm_noise_keywords(keywords: list[str]) -> list[str]:
    """Strip out known non-subject compositional/LLM noise vocabulary."""
    filtered: list[str] = []
    for kw in keywords:
        kw_clean = kw.strip()
        kw_lower = kw_clean.lower()
        if kw_lower and kw_lower not in _LLM_NOISE_VOCABULARY:
            filtered.append(kw_clean)
    return filtered


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
    "senior portrait": "scene_portrait",
    "boudoir": "scene_portrait",
    "editorial": "scene_portrait",
    "model": "scene_portrait",
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
    "animal": "scene_wildlife",
    "animals": "scene_wildlife",
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
    "horse": "scene_wildlife",
    "equine": "scene_wildlife",
    "equestrian": "scene_portrait",
    "pony": "scene_wildlife",
    "rabbit": "scene_portrait",
    "bunny": "scene_portrait",
    "hamster": "scene_portrait",
    "reptile": "scene_wildlife",
    "mammal": "scene_wildlife",
    "furry": "scene_portrait",
    "fur": "scene_portrait",
    "domestic animal": "scene_portrait",
    "domestic": "scene_portrait",
    # Landscapes & Scenery
    "landscape": "scene_landscape",
    "nature": "scene_nature",
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
    "meadow": "scene_landscape",
    "field": "scene_landscape",
    "grassland": "scene_landscape",
    "countryside": "scene_landscape",
    "rural": "scene_landscape",
    "horizon": "scene_landscape",
    "clouds": "scene_landscape",
    "sky": "scene_landscape",
    "scenic": "scene_landscape",
    "outdoors": "scene_landscape",
    # Nature & Wildlife & Macro
    "wildlife": "scene_wildlife",
    "bird": "scene_wildlife",
    "birds": "scene_wildlife",
    "avian": "scene_wildlife",
    "safari": "scene_wildlife",
    "zoo": "scene_wildlife",
    "raptor": "scene_wildlife",
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
    "droplet": "scene_macro",
    "water droplet": "scene_macro",
    "dew": "scene_macro",
    "leaf": "scene_macro",
    "leaves": "scene_macro",
    "mushroom": "scene_macro",
    "fungi": "scene_macro",
    "moss": "scene_macro",
    "snail": "scene_macro",
    "frog": "scene_macro",
    "toad": "scene_macro",
    "petal": "scene_macro",
    # Architecture & Real Estate & Property
    "architecture": "scene_architecture",
    "cityscape": "scene_architecture",
    "skyline": "scene_architecture",
    "building": "scene_architecture",
    "buildings": "scene_architecture",
    "real estate": "scene_architecture",
    "property": "scene_architecture",
    "realtor": "scene_architecture",
    "interior photography": "scene_architecture",
    "interior": "scene_architecture",
    "indoor": "scene_architecture",
    "living room": "scene_architecture",
    "home": "scene_architecture",
    "house": "scene_architecture",
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
    "carnival": "scene_street",
    "carnival ride": "scene_street",
    "roller coaster": "scene_street",
    "fairground": "scene_street",
    "fair": "scene_street",
    # Action & Sports & Athletics
    "sports": "scene_action",
    "action": "scene_action",
    "athletics": "scene_action",
    "runner": "scene_action",
    "running": "scene_action",
    "surfing": "scene_action",
    "motorsport": "scene_action",
    "race": "scene_action",
    "aviation": "scene_action",
    "airshow": "scene_action",
    "aircraft": "scene_action",
    # Underwater & Marine Specialty
    "underwater": "scene_nature",
    "scuba": "scene_nature",
    "reef": "scene_nature",
    "marine life": "scene_nature",
    # Night & Astrophotography
    "night": "scene_night",
    "astrophotography": "scene_astrophotography",
    "stargazing": "scene_astrophotography",
    "astro": "scene_astrophotography",
    "nightscape": "scene_astrophotography",
    "aurora": "scene_astrophotography",
    "aurora borealis": "scene_astrophotography",
    "northern lights": "scene_astrophotography",
    "stars": "scene_astrophotography",
    "star trails": "scene_astrophotography",
    "milky way": "scene_astrophotography",
    "deep sky": "scene_astrophotography",
    "nebula": "scene_astrophotography",
    "galaxy": "scene_astrophotography",
    "eclipse": "scene_astrophotography",
    "lunar": "scene_astrophotography",
}

_BROAD_GENRE_MAP: dict[str, str] = {
    "scene_portrait": "scene_portrait",
    "scene_people": "scene_portrait",
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
    "scene_wildlife": "scene_wildlife",
    "scene_architecture": "scene_architecture",
    "scene_studio": "scene_studio",
    "scene_interior": "scene_architecture",
    "scene_night": "scene_night",
    "scene_astrophotography": "scene_astrophotography",
}


_DYNAMIC_BUCKETS = {
    "scene_portrait": "portrait, people, family, fashion, headshot, baby, newborn, maternity, pet, pets, dog, cat, animal, puppy, kitten",
    "scene_event": "wedding, event, concert, ceremony, reception, party, conference, gala, banquet, festival, candid, group, gathering",
    "scene_landscape": "landscape, outdoors, travel, sunset, sunrise, scenery, vista, mountains, ocean, seascape, drone, aerial, forest, valley",
    "scene_nature": "plants, trees, wilderness, greenery, undergrowth, foliage, habitat",
    "scene_macro": "macro, close-up, extreme detail, insect, bug, beetle, spider, butterfly, flower detail, water droplet, botanical closeup, flora",
    "scene_wildlife": "wildlife, birds, fauna, animal tracking, safari, bird watching, raptor, avian",
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
    """Use SentenceTransformer to dynamically map an unknown keyword to a broad bucket, caching persistently in SQLite."""
    global _ef_instance, _bucket_embeddings

    keyword_lower = keyword.strip().lower()
    if keyword_lower in _DYNAMIC_GENRE_CACHE:
        return _DYNAMIC_GENRE_CACHE[keyword_lower]

    try:
        from services import style_catalog

        conn = style_catalog._ensure_initialized()
        row = conn.execute(
            "SELECT genre FROM semantic_genre_cache WHERE keyword = ?",
            (keyword_lower,),
        ).fetchone()
        if row:
            cached_genre = row["genre"] if row["genre"] else None
            _DYNAMIC_GENRE_CACHE[keyword_lower] = cached_genre
            return cached_genre
    except Exception:
        pass

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

    # Only map if it's semantically close enough (cosine distance < 0.55)
    # This prevents ambiguous keywords from overriding explicit tags
    nature_guard_words = {
        "park",
        "trail",
        "path",
        "rock",
        "stone",
        "water",
        "stream",
        "creek",
        "river",
        "lake",
        "pond",
        "bird",
        "birds",
        "wildlife",
        "animal",
        "nature",
        "tree",
        "trees",
        "forest",
        "hill",
        "hills",
        "valley",
        "grass",
        "leaf",
    }
    if closest_dist <= 0.45:
        closest_bucket = list(_DYNAMIC_BUCKETS.keys())[closest_idx]
        if closest_bucket in {
            "scene_architecture",
            "scene_street",
            "scene_action",
            "scene_wildlife",
        }:
            non_animal_nature = {
                nw
                for nw in nature_guard_words
                if nw not in {"bird", "birds", "wildlife", "animal"}
            }
            if any(nw in keyword_lower for nw in non_animal_nature):
                closest_bucket = (
                    "scene_landscape"
                    if closest_bucket != "scene_wildlife"
                    else "scene_nature"
                )
    else:
        closest_bucket = None

    _DYNAMIC_GENRE_CACHE[keyword_lower] = closest_bucket

    try:
        from services import style_catalog

        conn = style_catalog._ensure_initialized()
        conn.execute(
            "INSERT OR REPLACE INTO semantic_genre_cache (keyword, genre, created_at) VALUES (?, ?, ?)",
            (
                keyword_lower,
                closest_bucket or "",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
    except Exception:
        pass

    return closest_bucket


def clear_semantic_genre_cache() -> None:
    """Clear both memory and SQLite caches for dynamic semantic genre mappings."""
    global _DYNAMIC_GENRE_CACHE
    _DYNAMIC_GENRE_CACHE.clear()
    try:
        from services import style_catalog

        conn = style_catalog._ensure_initialized()
        conn.execute("DELETE FROM semantic_genre_cache")
        conn.commit()
    except Exception:
        pass


def _get_broad_genre(tag: str) -> str:
    """Map a specific scene tag to a broad bucket.

    Uses longest-match selection when multiple substring keys match to prevent
    short keys (e.g. 'car', 'dog', 'bee') from hijacking compound tags like
    'car race' or 'hot dog'.
    """
    tag_lower = tag.lower().strip()
    if tag_lower in _BROAD_GENRE_MAP:
        return _BROAD_GENRE_MAP[tag_lower]
    if tag_lower in _KEYWORD_TO_GENRE:
        mapped = _KEYWORD_TO_GENRE[tag_lower]
        return _BROAD_GENRE_MAP.get(
            mapped, mapped if mapped.startswith("scene_") else "scene_unknown"
        )

    # Collect all substring matches and prefer the longest key to avoid
    # short-key collisions (e.g. 'car' matching 'car race' -> scene_studio
    # when 'race' -> scene_action is the correct longer match).
    matches: list[tuple[int, str]] = []
    for k, v in _BROAD_GENRE_MAP.items():
        if len(k) >= 3 and (
            f" {k} " in f" {tag_lower} "
            or tag_lower.startswith(f"{k} ")
            or tag_lower.endswith(f" {k}")
        ):
            matches.append((len(k), v))
    for k, v in _KEYWORD_TO_GENRE.items():
        if len(k) >= 3 and (
            f" {k} " in f" {tag_lower} "
            or tag_lower.startswith(f"{k} ")
            or tag_lower.endswith(f" {k}")
        ):
            mapped = _BROAD_GENRE_MAP.get(
                v, v if v.startswith("scene_") else "scene_unknown"
            )
            matches.append((len(k), mapped))
    if matches:
        return max(matches, key=lambda x: x[0])[1]
    return tag if tag.startswith("scene_") else "scene_unknown"


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


def _parse_shutter_seconds(val: Any) -> float:
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = (
        str(val)
        .lower()
        .replace("sec", "")
        .replace("seconds", "")
        .replace("s", "")
        .strip()
    )
    if "/" in s:
        parts = s.split("/")
        try:
            num, den = float(parts[0].strip()), float(parts[1].strip())
            return num / den if den != 0 else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        match = re.search(r"[-+]?\d*\.?\d+", s)
        return float(match.group(0)) if match else 0.0
    except (ValueError, TypeError):
        return 0.0


def _parse_exif_float(val: Any) -> float:
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    match = re.search(r"[-+]?\d*\.?\d+", str(val))
    try:
        return float(match.group(0)) if match else 0.0
    except (ValueError, TypeError):
        return 0.0


def _get_35mm_equivalent_focal_length(make: str, model: str, focal: float) -> float:
    """Convert focal length to 35mm full-frame equivalent based on camera make and model."""
    if focal <= 0:
        return 0.0

    make_lower = make.lower()
    model_lower = model.lower()

    crop = 1.0
    if make_lower in ("olympus", "om digital solutions", "panasonic"):
        if "dc-s" in model_lower:
            crop = 1.0
        else:
            crop = 2.0
    elif "fujifilm" in make_lower:
        if "gfx" in model_lower:
            crop = 0.79
        else:
            crop = 1.5
    elif "sony" in make_lower:
        if any(
            x in model_lower
            for x in ("ilce-6", "ilce-5", "ilce-3", "nex", "zv-e10", "a6", "a5", "a3")
        ):
            crop = 1.5
    elif "nikon" in make_lower:
        dx_models = (
            "z 50",
            "z fc",
            "z 30",
            "d3000",
            "d3100",
            "d3200",
            "d3300",
            "d3400",
            "d3500",
            "d5000",
            "d5100",
            "d5200",
            "d5300",
            "d5500",
            "d5600",
            "d7000",
            "d7100",
            "d7200",
            "d7500",
            "d500",
            "d300",
            "d200",
            "d90",
            "d80",
            "d70",
            "d60",
            "d40",
        )
        if any(x in model_lower for x in dx_models):
            crop = 1.5
    elif "canon" in make_lower:
        if any(
            x in model_lower
            for x in ("eos m", "r7", "r10", "r50", "r100", "rebel", "kiss", "7d")
        ):
            crop = 1.6
        elif re.search(r"\b\d{2,4}d\b", model_lower):
            crop = 1.6
    elif "leica" in make_lower:
        if any(x in model_lower for x in (" t", " tl", " cl", "t (", "tl (", "cl (")):
            crop = 1.5
        elif re.search(r"\bs\b", model_lower):
            crop = 0.8

    return focal * crop


def _evaluate_exif_priors(exif_metadata: dict[str, Any] | None) -> dict[str, float]:
    """Evaluate EXIF metadata as Bayesian prior evidence weights (not hard overrides)."""
    priors: dict[str, float] = {}
    if not exif_metadata or not isinstance(exif_metadata, dict):
        return priors

    shutter = _parse_shutter_seconds(exif_metadata.get("shutter_speed"))
    iso = _parse_exif_float(exif_metadata.get("iso"))
    focal = _parse_exif_float(exif_metadata.get("focal_length"))
    lens = str(exif_metadata.get("lens") or "").lower()
    flash = bool(exif_metadata.get("flash"))
    make = str(exif_metadata.get("camera_make") or "")
    model = str(exif_metadata.get("camera_model") or "")

    focal_35mm = _get_35mm_equivalent_focal_length(make, model, focal)

    if shutter >= 10.0 and iso >= 3200:
        priors["scene_night"] = 0.4
    if re.search(r"\b(macro|micro|mc)\b", lens):
        priors["scene_macro"] = priors.get("scene_macro", 0.0) + 0.35
    elif 85.0 <= focal_35mm <= 135.0:
        priors["scene_portrait"] = priors.get("scene_portrait", 0.0) + 0.15
    if 0.0 < focal_35mm <= 24.0:
        priors["scene_landscape"] = priors.get("scene_landscape", 0.0) + 0.15
        priors["scene_architecture"] = priors.get("scene_architecture", 0.0) + 0.15
    if flash and 0 < iso <= 200:
        priors["scene_studio"] = priors.get("scene_studio", 0.0) + 0.20

    return priors


def is_stitched_panorama(meta: dict[str, Any]) -> bool:
    """Check if a photo is a stitched panorama (by filename convention, keywords/tags, or extreme aspect ratio >= 2.2:1)."""
    if not meta or not isinstance(meta, dict):
        return False

    filename = str(meta.get("filename") or meta.get("file_name") or "").lower()
    if any(
        suffix in filename
        for suffix in ("-pano", "_pano", "-panorama", "_panorama", "pano.", "panorama.")
    ):
        return True

    for kw_field in (
        "user_keywords",
        "keywords",
        "flattened_keywords",
        "scene_tags",
        "tags",
        "title",
        "caption",
    ):
        val = meta.get(kw_field)
        if val:
            val_str = str(val).lower()
            if any(
                term in val_str
                for term in ("panorama", "panoramic", "stitched pano", "stitched")
            ):
                return True

    try:
        width = float(
            meta.get("width") or meta.get("orig_width") or meta.get("ImageWidth") or 0
        )
        height = float(
            meta.get("height")
            or meta.get("orig_height")
            or meta.get("ImageHeight")
            or 0
        )
        if width > 0 and height > 0:
            ratio = max(width, height) / min(width, height)
            if ratio >= 2.2:
                return True
    except (TypeError, ValueError):
        pass

    return False


def classify_photo_genre(
    meta: dict[str, Any],
    genre_centroids: dict[str, np.ndarray] | None = None,
) -> str | None:
    """Unified organizational classification pipeline for Trained Styles and Upgrade Recommendations.

    Enforces:
    - Universal filtering out of stitched panoramas.
    - Multi-tiered hierarchical genre resolution (explicit User Keywords -> Vision Model Tags -> EXIF Priors).
    - Never overrides an established multi-tiered genre with SigLIP2 visual centroid dot product.
    """
    if not meta or not isinstance(meta, dict):
        return None
    if is_stitched_panorama(meta):
        return None

    scene_tags = meta.get("scene_tags") or meta.get("tags")
    user_keywords = (
        meta.get("user_keywords")
        or meta.get("keywords")
        or meta.get("flattened_keywords")
    )
    genre = _primary_genre_with_keywords(scene_tags, user_keywords, meta)
    if genre_centroids and genre in ("scene_unknown", "scene_general", ""):
        genre = verify_genre_with_visual_centroid(
            genre, meta.get("embedding"), genre_centroids
        )
    return genre


def _primary_genre(scene_tags: Any) -> str:
    """Return the primary broad genre, ignoring stylistic tags."""
    return _primary_genre_with_keywords(scene_tags, None)


def _primary_genre_with_keywords(
    scene_tags: Any,
    user_keywords: Any,
    exif_metadata: dict[str, Any] | None = None,
) -> str:
    """Return the primary editing regime with an explicit EXIF hardware exclusion for macro."""
    genre = _primary_genre_with_keywords_impl(scene_tags, user_keywords, exif_metadata)
    if genre == "scene_macro":
        if exif_metadata and isinstance(exif_metadata, dict):
            lens = str(exif_metadata.get("lens") or "").strip().lower()
            if lens and lens not in ("none", "unknown", "null"):
                if not re.search(r"\b(macro|micro|mc)\b", lens):
                    # Filter out macro keywords and tags and re-evaluate
                    filtered_tags = [
                        t
                        for t in _extract_keyword_strings(scene_tags)
                        if _get_broad_genre(t) != "scene_macro"
                    ]
                    filtered_kws = [
                        k
                        for k in _extract_keyword_strings(user_keywords)
                        if _get_broad_genre(k) != "scene_macro"
                    ]
                    new_genre = _primary_genre_with_keywords_impl(
                        filtered_tags, filtered_kws, exif_metadata
                    )
                    return (
                        "scene_nature"
                        if new_genre in ("scene_macro", "scene_unknown", "")
                        else new_genre
                    )
    return genre


def _primary_genre_with_keywords_impl(
    scene_tags: Any,
    user_keywords: Any,
    exif_metadata: dict[str, Any] | None = None,
) -> str:
    """Return the primary editing regime using explicit user keywords, Softmax vision, and EXIF priors."""
    kw_list = _filter_llm_noise_keywords(_extract_keyword_strings(user_keywords))
    tag_list = _filter_llm_noise_keywords(_extract_keyword_strings(scene_tags))
    content_tags = [t for t in tag_list if not t.startswith("style_")]
    priors = _evaluate_exif_priors(exif_metadata)

    # 1. EXPLICIT DICTIONARY KEYWORDS
    if kw_list:
        tier_order = [
            "scene_studio",
            "scene_portrait",
            "scene_macro",
            "scene_event",
            "scene_wildlife",
            "scene_astrophotography",
            "scene_night",
            "scene_architecture",
            "scene_street",
            "scene_landscape",
            "scene_nature",
            "scene_action",
        ]

        for target_genre in tier_order:
            for t in kw_list:
                t_lower = t.lower()
                matched_target = None
                if (
                    _BROAD_GENRE_MAP.get(t_lower) == target_genre
                    or _BROAD_GENRE_MAP.get(t) == target_genre
                ):
                    matched_target = target_genre
                elif mapped := _KEYWORD_TO_GENRE.get(t_lower):
                    if _get_broad_genre(mapped) == target_genre:
                        matched_target = target_genre
                else:
                    for k, genre_val in _KEYWORD_TO_GENRE.items():
                        if _get_broad_genre(genre_val) == target_genre and len(k) >= 3:
                            if (
                                f" {k} " in f" {t_lower} "
                                or t_lower.startswith(f"{k} ")
                                or t_lower.endswith(f" {k}")
                            ):
                                matched_target = target_genre
                                break

                if matched_target:
                    if matched_target == "scene_wildlife":
                        top_mapped = {_get_broad_genre(vt) for vt in content_tags[:12]}
                        if "scene_macro" in top_mapped:
                            return "scene_macro"
                        if "scene_portrait" in top_mapped:
                            return "scene_portrait"
                    return matched_target

        for t in kw_list:
            mapped = _get_broad_genre(t)
            if mapped != "scene_unknown":
                if mapped in ("scene_nature", "scene_landscape", "scene_exterior"):
                    specialized = {_get_broad_genre(k) for k in kw_list} - {
                        "scene_nature",
                        "scene_landscape",
                        "scene_exterior",
                        "scene_unknown",
                    }
                    if specialized:
                        continue
                return mapped

    # 1.5 SPECIALIZED SEMANTIC VECTOR MAPPING
    if kw_list:
        specialized_regimes = {
            "scene_astrophotography",
            "scene_macro",
            "scene_event",
            "scene_action",
            "scene_studio",
            "scene_portrait",
            "scene_wildlife",
        }
        for kw in kw_list:
            if len(kw.strip()) > 1:
                mapped_bucket = _dynamic_semantic_mapping(kw)
                if mapped_bucket in specialized_regimes:
                    return mapped_bucket

    # 2. VISION MODEL TAGS
    if content_tags:
        primary_mapped = _get_broad_genre(content_tags[0])
        background_settings = {
            "scene_exterior",
            "scene_interior",
            "scene_golden_hour",
            "scene_night",
            "scene_unknown",
        }
        first_tag = content_tags[0].lower() if content_tags else ""
        if primary_mapped in background_settings or primary_mapped in {
            "scene_landscape",
            "scene_nature",
            "scene_wildlife",
            "scene_macro",
            "scene_portrait",
        }:
            if (
                first_tag == "scene_landscape" or primary_mapped == "scene_landscape"
            ) and first_tag not in background_settings:
                tier_order_subjects = [
                    "scene_studio",
                    "scene_macro",
                    "scene_event",
                    "scene_portrait",
                    "scene_astrophotography",
                ]
            elif primary_mapped == "scene_nature":
                tier_order_subjects = [
                    "scene_studio",
                    "scene_macro",
                    "scene_wildlife",
                    "scene_event",
                    "scene_portrait",
                    "scene_action",
                    "scene_astrophotography",
                    "scene_landscape",
                ]
            elif primary_mapped == "scene_wildlife":
                tier_order_subjects = [
                    "scene_studio",
                    "scene_macro",
                    "scene_event",
                    "scene_action",
                ]
            elif primary_mapped == "scene_macro":
                tier_order_subjects = [
                    "scene_studio",
                ]
            elif primary_mapped == "scene_portrait":
                tier_order_subjects = [
                    "scene_studio",
                    "scene_macro",
                    "scene_action",
                    "scene_event",
                    "scene_street",
                ]
            elif primary_mapped == "scene_architecture":
                # Guard against distant buildings in landscapes
                top_mapped_arch = {_get_broad_genre(t) for t in content_tags[:3]}
                if (
                    "scene_landscape" in top_mapped_arch
                    or "scene_nature" in top_mapped_arch
                ):
                    tier_order_subjects = ["scene_landscape", "scene_nature"]
                else:
                    tier_order_subjects = []
            else:
                tier_order_subjects = [
                    "scene_studio",
                    "scene_macro",
                    "scene_wildlife",
                    "scene_action",
                    "scene_event",
                    "scene_astrophotography",
                    "scene_street",
                    "scene_portrait",
                    "scene_architecture",
                ]

            if primary_mapped in (
                "scene_nature",
                "scene_wildlife",
                "scene_exterior",
                "scene_landscape",
            ):
                top_vision_tags = content_tags[:12]
            else:
                top_vision_tags = content_tags[:6]

            for target_genre in tier_order_subjects:
                for t in top_vision_tags:
                    t_lower = t.lower()
                    if (
                        _get_broad_genre(t) == target_genre
                        or _get_broad_genre(t_lower) == target_genre
                        or _BROAD_GENRE_MAP.get(t_lower) == target_genre
                        or _BROAD_GENRE_MAP.get(t) == target_genre
                    ):
                        return target_genre
                    mapped = _KEYWORD_TO_GENRE.get(t_lower)
                    if mapped and _get_broad_genre(mapped) == target_genre:
                        return target_genre

            # No overriding subject found in the tag horizon — return the
            # primary environmental regime, using EXIF macro prior to
            # disambiguate pure scene_nature when a macro lens is present.
            if primary_mapped == "scene_nature":
                if priors and priors.get("scene_macro", 0.0) > 0:
                    return "scene_macro"
                return "scene_nature"
            if primary_mapped == "scene_wildlife":
                return "scene_wildlife"

        top_vision_tags = content_tags[:6]
        if primary_mapped == "scene_action":
            top_mapped = {_get_broad_genre(t) for t in top_vision_tags}
            if "scene_event" in top_mapped:
                return "scene_event"

        canonical_regimes = {
            "scene_portrait",
            "scene_landscape",
            "scene_architecture",
            "scene_studio",
            "scene_night",
            "scene_astrophotography",
            "scene_wildlife",
            "scene_action",
            "scene_event",
            "scene_street",
            "scene_macro",
            "scene_nature",
            "scene_food",
            "scene_exterior",
            "scene_interior",
        }
        if primary_mapped in canonical_regimes:
            return primary_mapped
        for t in top_vision_tags:
            if t in canonical_regimes:
                return t
            mapped_t = _get_broad_genre(t)
            if mapped_t in canonical_regimes:
                return mapped_t
            if mapped_t == "scene_nature":
                top_mapped = {_get_broad_genre(t2) for t2 in content_tags[:12]}
                for candidate in [
                    "scene_studio",
                    "scene_macro",
                    "scene_wildlife",
                    "scene_portrait",
                    "scene_event",
                    "scene_landscape",
                ]:
                    if candidate in top_mapped:
                        return candidate
                if priors and priors.get("scene_macro", 0.0) > 0:
                    return "scene_macro"
                return "scene_nature"
            if mapped_t == "scene_wildlife":
                top_mapped = {_get_broad_genre(t2) for t2 in content_tags[:12]}
                for candidate in [
                    "scene_portrait",
                    "scene_event",
                    "scene_macro",
                    "scene_action",
                ]:
                    if candidate in top_mapped:
                        return candidate
                return "scene_wildlife"

    # 3. EXIF PRIORS
    if priors:
        best_prior_regime, best_prior_score = max(priors.items(), key=lambda x: x[1])
        if best_prior_score >= 0.38:
            if best_prior_regime == "scene_night":
                top_mapped = {_get_broad_genre(t) for t in content_tags[:6]}
                if not top_mapped.intersection(
                    {
                        "scene_astrophotography",
                        "scene_portrait",
                        "scene_event",
                        "scene_wildlife",
                        "scene_studio",
                    }
                ):
                    return best_prior_regime
            else:
                return best_prior_regime
        if best_prior_regime == "scene_macro" and best_prior_score >= 0.35:
            top_mapped_tags = {_get_broad_genre(t) for t in content_tags[:6]}
            if not top_mapped_tags.intersection(
                {
                    "scene_portrait",
                    "scene_wildlife",
                    "scene_studio",
                    "scene_event",
                }
            ) and not (
                "scene_landscape" in top_mapped_tags
                and "scene_macro" not in content_tags
            ):
                return "scene_macro"
        if best_prior_score >= 0.30:
            return best_prior_regime

    # 4. VISION MODEL FALLBACK
    if content_tags and content_tags[0] != "scene_unknown":
        fallback = _get_broad_genre(content_tags[0])
        if fallback != "scene_unknown":
            return fallback

    # 5. DYNAMIC SEMANTIC VECTOR MAPPING (SentenceTransformer)
    if kw_list:
        for kw in kw_list:
            if len(kw.strip()) > 1:
                mapped_bucket = _dynamic_semantic_mapping(kw)
                if mapped_bucket:
                    return mapped_bucket

    # 6. SETTING FALLBACKS
    if kw_list:
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
            if any(
                f" {w} " in f" {t_lower} " or t_lower == w for w in setting_arch_words
            ):
                return "scene_architecture"
            if any(
                f" {w} " in f" {t_lower} " or t_lower == w for w in setting_land_words
            ):
                return "scene_landscape"

    return "scene_unknown"


def _camera_id(camera_make: str | None, camera_model: str | None) -> str:
    """Build a stable camera identifier string."""
    make = (camera_make or "unknown").strip()
    model = (camera_model or "unknown").strip()
    return f"{make} {model}".strip()


def _profile_name(camera_profile: str | None) -> str:
    """Normalise a camera-profile string for grouping."""
    if not camera_profile:
        return "default"

    profile = camera_profile.strip()
    is_hdr = bool(re.search(r"(?i)\s*\+?\s*HDR\b", profile))

    # Strip HDR and version tags for base normalization
    p_clean = re.sub(r"(?i)\s*\+?\s*HDR\b", "", profile).strip()
    p_clean = re.sub(r"(?i)\s*\(v\d+\)", "", p_clean).strip()
    p_clean = re.sub(r"\s+", " ", p_clean)

    # Title case it for consistency to prevent casing fractures
    if p_clean.islower() or p_clean.isupper():
        p_clean = p_clean.title()

    if is_hdr:
        p_clean += " + HDR"

    return p_clean or "default"


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


def _compute_catalog_genre_centroids(
    examples: list[dict[str, Any]],
    min_samples: int = 1,
) -> dict[str, np.ndarray]:
    """Compute normalized SigLIP2 visual centroids per genre across the catalog pool.

    If a genre has fewer than min_samples (cold-start / sparse catalog), it is omitted
    so that visual verification falls back to zero-shot Softmax vision tags.
    """
    genre_embs: dict[str, list[np.ndarray]] = {}
    for ex in examples:
        emb = ex.get("embedding")
        if emb is None:
            continue
        if not isinstance(emb, np.ndarray):
            emb = np.array(emb, dtype=np.float32)
        if emb.size == 0:
            continue
        # Use initial genre assignment
        g = classify_photo_genre(ex, None)
        if g and g not in ("scene_unknown", "scene_general"):
            genre_embs.setdefault(g, []).append(emb)

    centroids: dict[str, np.ndarray] = {}
    for g, embs in genre_embs.items():
        if len(embs) >= min_samples:
            stacked = np.stack(embs, axis=0)
            mean_vec = np.mean(stacked, axis=0)
            norm = float(np.linalg.norm(mean_vec))
            if norm > 1e-9:
                centroids[g] = mean_vec / norm
    return centroids


def verify_genre_with_visual_centroid(
    p_genre: str,
    embedding: Any,
    genre_centroids: dict[str, np.ndarray],
    similarity_threshold: float = 0.60,
) -> str:
    """Verify and arbitrate a photo's genre using SigLIP2 visual centroid dot-product.

    If the photo's embedding has strong visual similarity (>= similarity_threshold)
    to a genre centroid, verify or reassign the genre. If centroids are sparse or
    missing (cold-start), retain p_genre unchanged.
    """
    if embedding is None or not genre_centroids:
        return p_genre
    emb_arr = (
        embedding
        if isinstance(embedding, np.ndarray)
        else np.array(embedding, dtype=np.float32)
    )
    if emb_arr.size == 0:
        return p_genre
    norm = float(np.linalg.norm(emb_arr))
    if norm <= 1e-9:
        return p_genre
    emb_norm = emb_arr / norm

    best_genre = p_genre
    best_sim = -1.0
    for g, centroid in genre_centroids.items():
        sim = float(np.dot(centroid, emb_norm))
        if sim > best_sim:
            best_sim = sim
            best_genre = g

    # Only override when subject genre is unresolved via tags/keywords/EXIF priors
    if p_genre in ("scene_unknown", "scene_general", ""):
        if best_sim >= similarity_threshold and best_genre not in (
            "scene_unknown",
            "scene_general",
            "",
        ):
            return best_genre

    return p_genre


def _compute_group_centroids(
    groups: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[tuple[str, str], np.ndarray]:
    """Compute L2-normalized visual centroids for each (profile, genre) group."""
    centroids: dict[tuple[str, str], np.ndarray] = {}
    for key, group_ex in groups.items():
        embs = [ex["embedding"] for ex in group_ex if ex.get("embedding") is not None]
        if embs:
            emb_matrix = np.array(embs, dtype=np.float32)
            mean_vec = np.mean(emb_matrix, axis=0)
            norm = float(np.linalg.norm(mean_vec))
            if norm > 1e-9:
                centroids[key] = mean_vec / norm
    return centroids


def _normalize_embedding(emb: Any) -> np.ndarray | None:
    """Return an L2-normalized embedding vector, or None if unusable."""
    if emb is None:
        return None
    emb_arr = emb if isinstance(emb, np.ndarray) else np.array(emb, dtype=np.float32)
    if emb_arr.size == 0:
        return None
    norm = float(np.linalg.norm(emb_arr))
    if norm <= 1e-9:
        return None
    return emb_arr / norm


def group_examples_by_profile_genre(
    examples: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group training examples by (profile, genre) with cross-group visual verification.

    Uses the same 3-stage pipeline as the Upgrade Recommendations path:

    1. **Text classification** (shared: ``classify_photo_genre``): Initial genre
       assignment based on scene tags, user keywords, and EXIF priors.
    2. **Visual centroid computation**: Compute an L2-normalized centroid
       embedding for each ``(profile, genre)`` group.
    3. **Cross-group visual re-assignment**: For every photo, compare its
       embedding similarity to its *own* group's centroid vs. *all other*
       groups' centroids (same camera profile).  If another group's centroid is
       a significantly better visual match (by ``VISUAL_REASSIGN_MARGIN``),
       re-assign the photo.  This mirrors the ``C_mat @ E_mat.T >= 0.45``
       gating check that keeps the Upgrade Recommendations pipeline accurate.

    Args:
        examples: Training-example metadata dicts (must include ``embedding``).

    Returns:
        Dict keyed by ``(profile, genre)`` → list of examples.
    """
    # ------------------------------------------------------------------
    # Pass 1: Initial text-based grouping (shared with upgrades pipeline)
    # ------------------------------------------------------------------
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for ex in examples:
        if is_stitched_panorama(ex):
            continue
        genre = classify_photo_genre(ex, None) or "scene_unknown"
        profile = _profile_name(ex.get("camera_profile"))
        key = (profile, genre)
        groups.setdefault(key, []).append(ex)

    # Need at least 2 groups within a profile for cross-group comparison
    profiles_with_multiple_groups: set[str] = set()
    profile_group_count: dict[str, int] = {}
    for profile, _genre in groups:
        profile_group_count[profile] = profile_group_count.get(profile, 0) + 1
    for profile, count in profile_group_count.items():
        if count >= 2:
            profiles_with_multiple_groups.add(profile)

    if not profiles_with_multiple_groups:
        return groups

    # ------------------------------------------------------------------
    # Pass 2: Compute visual centroids per group
    # ------------------------------------------------------------------
    centroids = _compute_group_centroids(groups)

    if not centroids:
        return groups

    # ------------------------------------------------------------------
    # Pass 3: Cross-group visual re-assignment
    # ------------------------------------------------------------------
    # For each photo, check if another group (same profile) is a better
    # visual fit.  This catches the cases where classify_photo_genre is
    # confidently *wrong* (e.g. a macro shot classified as portrait).
    reassignments: list[tuple[tuple[str, str], tuple[str, str], dict[str, Any]]] = []

    for key, group_ex in groups.items():
        profile, genre = key
        if profile not in profiles_with_multiple_groups:
            continue
        own_centroid = centroids.get(key)
        if own_centroid is None:
            continue

        for ex in group_ex:
            emb_norm = _normalize_embedding(ex.get("embedding"))
            if emb_norm is None:
                continue

            own_sim = float(np.dot(own_centroid, emb_norm))

            best_other_key: tuple[str, str] | None = None
            best_other_sim = -1.0

            for other_key, other_centroid in centroids.items():
                other_profile, other_genre = other_key
                if other_profile != profile or other_key == key:
                    continue
                sim = float(np.dot(other_centroid, emb_norm))
                if sim > best_other_sim:
                    best_other_sim = sim
                    best_other_key = other_key

            if (
                best_other_key is not None
                and best_other_sim > own_sim + VISUAL_REASSIGN_MARGIN
                and best_other_sim >= VISUAL_MIN_SIMILARITY
            ):
                reassignments.append((key, best_other_key, ex))

    # Apply reassignments
    for old_key, new_key, ex in reassignments:
        if ex in groups.get(old_key, []):
            groups[old_key].remove(ex)
            groups.setdefault(new_key, []).append(ex)

    # Recompute centroids after reassignment and do one more pass for
    # ambiguous photos (scene_unknown/scene_general) that may now have a
    # clear best-fit group.
    if reassignments:
        centroids = _compute_group_centroids(groups)

    ambiguous_key_genre = ("scene_unknown", "scene_general", "")
    ambiguous_keys = [k for k in groups if k[1] in ambiguous_key_genre]
    for amb_key in ambiguous_keys:
        profile = amb_key[0]
        remaining: list[dict[str, Any]] = []
        for ex in groups.get(amb_key, []):
            emb_norm = _normalize_embedding(ex.get("embedding"))
            if emb_norm is None:
                remaining.append(ex)
                continue

            best_key: tuple[str, str] | None = None
            best_sim = -1.0
            for other_key, centroid in centroids.items():
                if other_key[0] != profile or other_key[1] in ambiguous_key_genre:
                    continue
                sim = float(np.dot(centroid, emb_norm))
                if sim > best_sim:
                    best_sim = sim
                    best_key = other_key

            if best_key is not None and best_sim >= VISUAL_MIN_SIMILARITY:
                groups.setdefault(best_key, []).append(ex)
            else:
                remaining.append(ex)

        groups[amb_key] = remaining

    # Final visual verification: move any visual outlier (similarity < VISUAL_MIN_SIMILARITY)
    # out of specific genre groups into scene_general so it does not pollute style profiles.
    for key in list(groups.keys()):
        profile, genre = key
        if genre in ambiguous_key_genre:
            continue
        centroid = centroids.get(key)
        if centroid is None:
            continue
        valid_ex: list[dict[str, Any]] = []
        for ex in groups[key]:
            emb_norm = _normalize_embedding(ex.get("embedding"))
            if emb_norm is None:
                valid_ex.append(ex)
                continue
            sim = float(np.dot(centroid, emb_norm))
            if sim >= VISUAL_MIN_SIMILARITY:
                valid_ex.append(ex)
            else:
                groups.setdefault((profile, "scene_general"), []).append(ex)
        groups[key] = valid_ex

    # Clean up empty groups
    groups = {k: v for k, v in groups.items() if v}

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


def is_genre_compatible(style_genre: str, photo_genre: str) -> tuple[bool, bool]:
    """Unified broad-genre compatibility check shared by Trained Styles and Upgrade Recommendations.

    Returns:
        (is_compatible, is_ambiguous):
        - is_compatible: True if broad genres match or either genre is general/unknown.
        - is_ambiguous: True if either genre is scene_unknown/scene_general/empty, signaling
          that visual membership verification is required.
    """
    s_clean = (style_genre or "").strip()
    p_clean = (photo_genre or "").strip()

    is_ambiguous = s_clean in ("scene_unknown", "scene_general", "") or p_clean in (
        "scene_unknown",
        "scene_general",
        "",
    )

    if is_ambiguous:
        return True, True

    b_style = _get_broad_genre(s_clean)
    b_photo = _get_broad_genre(p_clean)

    is_compat = (
        p_clean == s_clean
        or b_photo == s_clean
        or p_clean == b_style
        or b_photo == b_style
    )
    return is_compat, False


def verify_photo_visual_membership(
    embedding: Any,
    style_embeddings: np.ndarray | None = None,
    style_centroid: np.ndarray | None = None,
    min_similarity: float = 0.45,
    require_strict_if_ambiguous: bool = False,
) -> bool:
    """Unified SigLIP2 visual verification helper shared by Trained Styles and Upgrade Recommendations.

    Verifies whether a photo's embedding vector belongs to a style cluster by checking
    cosine similarity against the style's training embeddings matrix or visual centroid.
    """
    if embedding is None:
        return True

    emb_arr = (
        embedding
        if isinstance(embedding, np.ndarray)
        else np.array(embedding, dtype=np.float32)
    )
    if emb_arr.size == 0:
        return True
    norm = float(np.linalg.norm(emb_arr))
    if norm <= 1e-9:
        return True
    emb_norm = emb_arr / norm

    threshold = 0.60 if require_strict_if_ambiguous else min_similarity

    if style_embeddings is not None and len(style_embeddings) > 0:
        E_mat = (
            style_embeddings
            if isinstance(style_embeddings, np.ndarray)
            else np.array(style_embeddings, dtype=np.float32)
        )
        if E_mat.ndim == 1:
            E_mat = E_mat.reshape(1, -1)
        sims = emb_norm @ E_mat.T
        max_sim = float(np.max(sims))
        return max_sim >= threshold

    if style_centroid is not None and len(style_centroid) > 0:
        c_arr = (
            style_centroid
            if isinstance(style_centroid, np.ndarray)
            else np.array(style_centroid, dtype=np.float32)
        )
        c_norm = float(np.linalg.norm(c_arr))
        if c_norm > 1e-9:
            sim = float(np.dot(emb_norm, c_arr / c_norm))
            return sim >= threshold

    return True
