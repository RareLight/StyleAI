"""Cheap, model-independent photo eligibility checks."""

from __future__ import annotations

from typing import Any


def is_stitched_panorama(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    filename = str(
        metadata.get("filename") or metadata.get("file_name") or ""
    ).casefold()
    if any(
        marker in filename
        for marker in (
            "-pano",
            "_pano",
            "-panorama",
            "_panorama",
            "pano.",
            "panorama.",
        )
    ):
        return True
    for field in (
        "user_keywords",
        "keywords",
        "flattened_keywords",
        "scene_tags",
        "tags",
        "title",
        "caption",
    ):
        text = str(metadata.get(field) or "").casefold()
        if any(
            marker in text
            for marker in ("panorama", "panoramic", "stitched pano", "stitched")
        ):
            return True
    try:
        width = float(
            metadata.get("width")
            or metadata.get("orig_width")
            or metadata.get("ImageWidth")
            or 0
        )
        height = float(
            metadata.get("height")
            or metadata.get("orig_height")
            or metadata.get("ImageHeight")
            or 0
        )
    except (TypeError, ValueError):
        return False
    return width > 0 and height > 0 and max(width, height) / min(width, height) >= 2.2
