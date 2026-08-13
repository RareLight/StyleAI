"""Non-persisting local-LLM metadata benchmark execution.

This module intentionally calls the normal metadata provider path directly. It
does not import Chroma or any catalog-writing service, so benchmark outputs can
only leave the process through the HTTP response and the caller's report files.
"""

from __future__ import annotations

from hashlib import sha256
import io
import time
from typing import Any

from PIL import Image

from services.metadata import AnalysisService, get_analysis_service


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
) -> list[dict[str, Any]]:
    """Generate normalized metadata for each item without persistence."""
    service = analysis_service or get_analysis_service()
    provider = str(options.get("provider") or "").strip()
    if provider not in service.providers:
        raise ValueError(f"benchmark provider is unavailable: {provider}")
    results: list[dict[str, Any]] = []

    for item in items:
        if cancel_signal is not None and cancel_signal.is_set():
            raise InterruptedError("metadata benchmark operation has been canceled")

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

        result: dict[str, Any] = {
            "photo_id": photo_id,
            "source_photo_id": str(item.get("source_photo_id") or photo_id),
            "filename": filename,
            "status": "succeeded" if response.success else "failed",
            "provider": str(item_options.get("provider") or ""),
            "model": str(item_options.get("model") or ""),
            "proxy": proxy,
            "keywords": response.keywords if response.success else None,
            "title": response.title if response.success else None,
            "caption": response.caption if response.success else None,
            "alt_text": response.alt_text if response.success else None,
            "input_tokens": int(response.input_tokens or 0),
            "output_tokens": int(response.output_tokens or 0),
            "retry_count": int(response.retry_count or 0),
            "timing": timing,
        }
        if response.error:
            result["error"] = str(response.error)
        if response.warning:
            result["warning"] = str(response.warning)
        results.append(result)

    return results
