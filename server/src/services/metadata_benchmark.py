"""Non-persisting local-LLM metadata benchmark execution.

This module intentionally calls the normal metadata provider path directly. It
does not import Chroma or any catalog-writing service, so benchmark outputs can
only leave the process through the HTTP response and the caller's report files.
"""

from __future__ import annotations

from hashlib import sha256
import io
import time
from collections.abc import Callable
from typing import Any
import math

from PIL import Image

from services.metadata import AnalysisService, get_analysis_service


_FORBIDDEN_PLACEHOLDERS = frozenset(
    {
        "none",
        "n/a",
        "na",
        "null",
        "unknown",
        "unspecified",
        "not applicable",
        "not available",
        "not present",
        "not visible",
        "no keyword",
        "no keywords",
    }
)


def _flatten_keyword_values(value: Any) -> list[str]:
    flattened: list[str] = []
    if isinstance(value, str):
        flattened.append(value)
    elif isinstance(value, list):
        for item in value:
            flattened.extend(_flatten_keyword_values(item))
    elif isinstance(value, dict):
        if isinstance(value.get("name"), str):
            flattened.append(value["name"])
            flattened.extend(_flatten_keyword_values(value.get("aliases")))
        else:
            for item in value.values():
                flattened.extend(_flatten_keyword_values(item))
    return flattened


def _normalized_words(value: str | None) -> list[str]:
    if not isinstance(value, str):
        return []
    normalized = "".join(
        character.lower() if character.isalnum() else " " for character in value
    )
    return [token for token in normalized.split() if token]


def calculate_contract_metrics(
    response: Any, options: dict[str, Any]
) -> dict[str, Any]:
    """Return deterministic output-contract checks, not visual-quality scores."""
    keywords = _flatten_keyword_values(response.keywords)
    normalized_keywords = [" ".join(value.lower().split()) for value in keywords]
    distinct_keywords = set(normalized_keywords)
    requested_presence = []
    if options.get("generate_keywords"):
        requested_presence.append(bool(keywords))
    for option_key, field_name in (
        ("generate_title", "title"),
        ("generate_caption", "caption"),
        ("generate_alt_text", "alt_text"),
    ):
        if options.get(option_key):
            requested_presence.append(
                bool(str(getattr(response, field_name, "") or "").strip())
            )

    caption_words = _normalized_words(response.caption)
    alt_text_words = _normalized_words(response.alt_text)
    caption_word_set = set(caption_words)
    alt_text_word_set = set(alt_text_words)
    overlap_denominator = len(caption_word_set | alt_text_word_set)
    overlap = (
        len(caption_word_set & alt_text_word_set) / overlap_denominator
        if overlap_denominator
        else None
    )
    placeholder_count = sum(
        normalized in _FORBIDDEN_PLACEHOLDERS for normalized in normalized_keywords
    )
    return {
        "structured_output_valid": bool(response.success),
        "all_requested_fields_present": bool(response.success)
        and all(requested_presence),
        "keyword_count": len(keywords),
        "distinct_keyword_count": len(distinct_keywords),
        "keyword_limit": 12,
        "keyword_limit_compliant": len(keywords) <= 12,
        "duplicate_keyword_count": len(keywords) - len(distinct_keywords),
        "forbidden_placeholder_count": placeholder_count,
        "caption_word_count": len(caption_words),
        "alt_text_word_count": len(alt_text_words),
        "caption_alt_text_lexical_overlap": (
            round(overlap, 3) if overlap is not None else None
        ),
        "caption_alt_text_excessive_overlap": bool(
            overlap is not None
            and overlap >= 0.9
            and min(len(caption_words), len(alt_text_words)) >= 5
        ),
    }


def round_benchmark_timings(timing: dict[str, Any]) -> dict[str, Any]:
    """Return report-ready timings with consistent, human-readable precision."""

    def round_half_up(value: int | float, decimal_places: int) -> int | float:
        factor = 10**decimal_places
        scaled = float(value) * factor
        rounded_value = (
            math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
        )
        return rounded_value if decimal_places == 0 else rounded_value / factor

    rounded: dict[str, Any] = {}
    for key, value in timing.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            rounded[key] = value
        elif key.endswith("_ms"):
            rounded[key] = round_half_up(value, 0)
        elif key.endswith(("_seconds", "_minutes", "_per_second", "_per_minute")):
            rounded[key] = round_half_up(value, 1)
        else:
            rounded[key] = value
    return rounded


def inspect_proxy(image_data: bytes) -> dict[str, Any]:
    """Return bounded, non-pixel proxy provenance for reproducibility."""
    with Image.open(io.BytesIO(image_data)) as image:
        width, height = image.size
        image_format = image.format or "unknown"
    return {
        "sha256": sha256(image_data).hexdigest(),
        "byte_count": len(image_data),
        "width": int(width),
        "height": int(height),
        "format": str(image_format),
    }


def run_benchmark_batch(
    items: list[dict[str, Any]],
    options: dict[str, Any],
    *,
    analysis_service: AnalysisService | None = None,
    cancel_signal: Any | None = None,
    on_item_started: Callable[[dict[str, Any], int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Generate normalized metadata for each item without persistence."""
    service = analysis_service or get_analysis_service()
    provider = str(options.get("provider") or "").strip()
    if provider not in service.providers:
        raise ValueError(f"benchmark provider is unavailable: {provider}")
    if provider != service.get_active_provider():
        raise ValueError(
            f"benchmark provider does not match active provider: {service.get_active_provider()}"
        )
    results: list[dict[str, Any]] = []

    for item_index, item in enumerate(items, start=1):
        if cancel_signal is not None and cancel_signal.is_set():
            raise InterruptedError("metadata benchmark operation has been canceled")
        if on_item_started is not None:
            on_item_started(item, item_index, len(items))

        photo_id = str(item["photo_id"])
        filename = str(item.get("filename") or "photo.jpg")
        image_data = item["image_data"]
        proxy_started = time.perf_counter()
        proxy = inspect_proxy(image_data)
        proxy_inspection_ms = round((time.perf_counter() - proxy_started) * 1000.0, 3)
        started = time.perf_counter()
        item_options = dict(options)
        item_options.update(item.get("options") or {})
        response = service.generate_metadata_single(photo_id, image_data, item_options)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        timing = dict(response.timing or {})
        timing["base64_decode_ms"] = float(item.get("decode_ms") or 0.0)
        timing["proxy_inspection_ms"] = proxy_inspection_ms
        timing["benchmark_item_total_ms"] = elapsed_ms
        timing = round_benchmark_timings(timing)

        result: dict[str, Any] = {
            "photo_id": photo_id,
            "source_photo_id": str(item.get("source_photo_id") or photo_id),
            "filename": filename,
            "status": "succeeded" if response.success else "failed",
            "provider": str(item_options.get("provider") or ""),
            "model": str(item_options.get("model") or ""),
            "benchmark_variant": str(
                item_options.get("benchmark_variant") or "baseline"
            ),
            "speculation_mode": str(item_options.get("speculation_mode") or "baseline"),
            "draft_model_requested": item_options.get("draft_model"),
            "proxy": proxy,
            "keywords": response.keywords if response.success else None,
            "title": response.title if response.success else None,
            "caption": response.caption if response.success else None,
            "alt_text": response.alt_text if response.success else None,
            "input_tokens": int(response.input_tokens or 0),
            "output_tokens": int(response.output_tokens or 0),
            "retry_count": int(response.retry_count or 0),
            "timing": timing,
            "inference": dict(response.inference or {}),
            "contract_metrics": calculate_contract_metrics(response, item_options),
        }
        if response.error:
            result["error"] = str(response.error)
        if response.warning:
            result["warning"] = str(response.warning)
        results.append(result)

    return results
