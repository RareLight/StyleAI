"""Request parsing utilities shared across route blueprints.

Extracted from routes.index so that edit and style-edit routes do not
depend on the indexing blueprint (which will be removed in a later phase).
"""

from __future__ import annotations

import json
from typing import Any

from config import logger


def _parse_json_field(value, default=None):
    """Parse JSON-encoded form fields when clients submit multipart data."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _extract_photo_ids(form_or_json):
    """Accept new photo_id(s) and legacy uuid(s)."""
    if hasattr(form_or_json, "getlist"):
        photo_ids = form_or_json.getlist("photo_id")
        if photo_ids:
            return photo_ids
        return form_or_json.getlist("uuid")
    photo_id = form_or_json.get("photo_id")
    if photo_id:
        return [photo_id]
    uuid = form_or_json.get("uuid")
    return [uuid] if uuid else []


def _bool_from_data(data, key: str, default: bool = False) -> bool:
    """Safely read a boolean-ish form field."""
    return str(data.get(key, default)).lower() == "true"


def _extract_options(data) -> dict[str, Any]:
    """Extracts options from request data (form or json)."""
    options: dict[str, Any] = {}

    try:
        logger.info(
            "Raw indexing option keys received: %s",
            list(getattr(data, "keys", lambda: [])()),
        )
    except Exception:
        pass

    # Provider / model basics
    options["provider"] = data.get("provider")
    options["model"] = data.get("model")
    options["api_key"] = data.get("api_key")
    options["language"] = data.get("language", "German")

    # Temperature
    try:
        options["temperature"] = float(data.get("temperature", 0.2))
    except (TypeError, ValueError):
        options["temperature"] = 0.2

    # Max tokens
    try:
        _mt = data.get("max_tokens")
        options["max_tokens"] = max(1, int(_mt)) if _mt is not None else None
    except (ValueError, TypeError):
        options["max_tokens"] = None

    # Metadata generation flags (indexing-specific, kept for backwards compatibility)
    options["generate_keywords"] = _bool_from_data(data, "generate_keywords", True)
    options["generate_caption"] = _bool_from_data(data, "generate_caption", True)
    options["generate_title"] = _bool_from_data(data, "generate_title", True)
    options["generate_alt_text"] = _bool_from_data(data, "generate_alt_text", True)
    options["submit_keywords"] = _bool_from_data(data, "submit_keywords", False)
    options["submit_folder_names"] = _bool_from_data(data, "submit_folder_names", False)

    # Existing keywords
    raw_existing = _parse_json_field(data.get("existing_keywords"))
    if raw_existing is None:
        options["existing_keywords"] = None
    elif isinstance(raw_existing, str):
        options["existing_keywords"] = [
            k.strip() for k in raw_existing.split(",") if k.strip()
        ]
    elif isinstance(raw_existing, list):
        options["existing_keywords"] = [
            str(k).strip() for k in raw_existing if str(k).strip()
        ]
    else:
        options["existing_keywords"] = None

    options["folder_names"] = data.get("folder_names")
    options["user_context"] = data.get("user_context")

    # Keyword categories
    keyword_categories_raw = data.get("keyword_categories", "[]")
    if isinstance(keyword_categories_raw, str):
        try:
            options["keyword_categories"] = json.loads(keyword_categories_raw)
        except json.JSONDecodeError:
            options["keyword_categories"] = []
    else:
        options["keyword_categories"] = keyword_categories_raw

    options["bilingual_keywords"] = _bool_from_data(data, "bilingual_keywords", False)
    options["keyword_secondary_language"] = (
        data.get("keyword_secondary_language") or None
    )
    options["generate_aliases"] = _bool_from_data(data, "generate_aliases", False)

    raw_catalog_kw = _parse_json_field(data.get("catalog_keywords"))
    if isinstance(raw_catalog_kw, list):
        options["catalog_keywords"] = [
            str(k).strip() for k in raw_catalog_kw if str(k).strip()
        ] or None
    else:
        options["catalog_keywords"] = None

    # Edit-specific options
    options["replace_ss"] = _bool_from_data(data, "replace_ss", False)
    options["ollama_base_url"] = data.get("ollama_base_url") or None
    options["lmstudio_base_url"] = data.get("lmstudio_base_url") or None

    # Regenerate metadata
    reg_val = data.get("regenerate_metadata")
    if reg_val is None:
        reg_val = data.get("regenerateMetadata", "true")
    options["regenerate_metadata"] = str(reg_val).lower() == "true"

    options["prompt"] = data.get("prompt")
    options["edit_intent"] = data.get("edit_intent")

    # Semantic Clustering Threshold (0.80 to 1.00)
    try:
        options["semantic_clustering_threshold"] = float(
            data.get("semantic_clustering_threshold", 0.94)
        )
    except (TypeError, ValueError):
        options["semantic_clustering_threshold"] = 0.94

    # Style strength (clamped 0..1)
    try:
        options["style_strength"] = float(data.get("style_strength", 0.5))
    except (TypeError, ValueError):
        options["style_strength"] = 0.5
    options["style_strength"] = max(0.0, min(1.0, options["style_strength"]))

    # Boolean edit-control flags
    options["include_masks"] = _bool_from_data(data, "include_masks", True)
    options["adjust_white_balance"] = _bool_from_data(
        data, "adjust_white_balance", True
    )
    options["adjust_basic_tone"] = _bool_from_data(data, "adjust_basic_tone", True)
    options["adjust_presence"] = _bool_from_data(data, "adjust_presence", True)
    options["adjust_color_mix"] = _bool_from_data(data, "adjust_color_mix", True)
    options["do_color_grading"] = _bool_from_data(data, "do_color_grading", True)
    options["use_tone_curve"] = _bool_from_data(data, "use_tone_curve", True)
    options["use_point_curve"] = _bool_from_data(data, "use_point_curve", True)
    options["adjust_detail"] = _bool_from_data(data, "adjust_detail", True)
    options["adjust_effects"] = _bool_from_data(data, "adjust_effects", True)
    options["adjust_lens_corrections"] = _bool_from_data(
        data, "adjust_lens_corrections", True
    )
    options["allow_auto_crop"] = _bool_from_data(data, "allow_auto_crop", True)

    # Composition mode
    composition_mode = str(data.get("composition_mode", "subtle")).lower().strip()
    if composition_mode not in ("none", "subtle", "aggressive"):
        composition_mode = "subtle"
    options["composition_mode"] = composition_mode

    # Capture time
    options["date_time"] = data.get("date_time")
    options["date_time_unix"] = data.get("date_time_unix")

    # Tasks list (indexing-specific, kept for backwards compatibility)
    tasks_raw = data.get("tasks")
    if tasks_raw:
        if isinstance(tasks_raw, str):
            try:
                tasks = (
                    json.loads(tasks_raw)
                    if tasks_raw.startswith("[")
                    else [t.strip() for t in tasks_raw.split(",")]
                )
            except (json.JSONDecodeError, AttributeError):
                tasks = [t.strip() for t in tasks_raw.split(",")]
        else:
            tasks = tasks_raw
    else:
        tasks = ["embeddings"]

    options["compute_embeddings"] = "embeddings" in tasks
    options["compute_metadata"] = "metadata" in tasks
    options["compute_faces"] = "faces" in tasks
    options["compute_vertexai"] = "vertexai" in tasks

    # Vertex AI config
    options["vertex_project_id"] = data.get("vertex_project_id") or data.get(
        "vertexProjectId"
    )
    options["vertex_location"] = data.get("vertex_location") or data.get(
        "vertexLocation"
    )

    # Cross-catalog soft-state
    options["catalog_id"] = data.get("catalog_id") or None
    if options["catalog_id"] and isinstance(options["catalog_id"], str):
        options["catalog_id"] = options["catalog_id"].strip() or None

    return options
