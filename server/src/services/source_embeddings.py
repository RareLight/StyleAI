"""Canonical, target-independent source-image embedding contract.

The vector is derived data.  Callers may reuse it only when the complete
contract stamp matches the current source file and SigLIP implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from config import IMAGE_MODEL_ID, logger
from .policy_features import SOURCE_METRIC_KEYS
from utils.image_processing import extract_exiftool_preview


SOURCE_EMBEDDING_SCHEMA_VERSION = "neutral-source-v1"
SOURCE_EMBEDDING_MODEL_ID = IMAGE_MODEL_ID
SOURCE_EMBEDDING_PREPROCESS_VERSION = "thumbnail-512-rgb-v1"

RAW_PREVIEW_PROVENANCE = "raw_preview"
RENDERED_PREVIEW_PROVENANCE = "lightroom_rendered_preview"

_METADATA_KEYS = {
    "source_embedding_schema": SOURCE_EMBEDDING_SCHEMA_VERSION,
    "source_embedding_model": SOURCE_EMBEDDING_MODEL_ID,
    "source_embedding_preprocess": SOURCE_EMBEDDING_PREPROCESS_VERSION,
}


@dataclass(frozen=True)
class NeutralSource:
    image_bytes: bytes
    provenance: str
    fingerprint: str


def _hash_identity(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
    return digest.hexdigest()


def raw_file_fingerprint(filepath: str | None) -> str | None:
    """Return a cheap, strict identity for the immutable source-file state."""
    if not filepath:
        return None
    try:
        path = Path(filepath).expanduser().resolve(strict=True)
        stat = path.stat()
    except (OSError, RuntimeError):
        return None
    return _hash_identity(
        RAW_PREVIEW_PROVENANCE,
        os.fspath(path),
        str(stat.st_size),
        str(stat.st_mtime_ns),
    )


def rendered_preview_fingerprint(image_bytes: bytes) -> str:
    digest = hashlib.sha256(image_bytes).hexdigest()
    return _hash_identity(RENDERED_PREVIEW_PROVENANCE, digest)


def resolve_neutral_source(
    rendered_image_bytes: bytes,
    raw_filepath: str | None,
) -> NeutralSource:
    """Prefer an embedded RAW preview and provenance-label a safe fallback."""
    raw_fingerprint = raw_file_fingerprint(raw_filepath)
    if raw_fingerprint is not None:
        try:
            raw_preview = extract_exiftool_preview(str(raw_filepath))
        except (PermissionError, TimeoutError) as exc:
            logger.warning(
                "Neutral RAW preview unavailable for %s: %s", raw_filepath, exc
            )
            raw_preview = None
        if raw_preview:
            return NeutralSource(
                image_bytes=raw_preview,
                provenance=RAW_PREVIEW_PROVENANCE,
                fingerprint=raw_fingerprint,
            )

    return NeutralSource(
        image_bytes=rendered_image_bytes,
        provenance=RENDERED_PREVIEW_PROVENANCE,
        fingerprint=rendered_preview_fingerprint(rendered_image_bytes),
    )


def decode_for_embedding(image_bytes: bytes) -> Image.Image | None:
    """Decode with the exact preprocessing boundary named by the contract."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as source_image:
            source_image.thumbnail((512, 512), Image.Resampling.LANCZOS)
            return source_image.convert("RGB")
    except Exception as exc:
        logger.warning("Could not decode canonical embedding source: %s", exc)
        return None


def stamp_metadata(
    metadata: dict[str, Any],
    source: NeutralSource,
    *,
    source_metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    stamped = dict(metadata)
    stamped.update(_METADATA_KEYS)
    stamped["source_embedding_provenance"] = source.provenance
    stamped["source_embedding_fingerprint"] = source.fingerprint
    if source_metrics is not None:
        for key in SOURCE_METRIC_KEYS:
            if key in source_metrics:
                stamped[key] = float(source_metrics[key])
    return stamped


def metadata_has_current_contract(metadata: dict[str, Any] | None) -> bool:
    metadata = metadata or {}
    return all(metadata.get(key) == value for key, value in _METADATA_KEYS.items())


def cached_source_metrics(metadata: dict[str, Any] | None) -> dict[str, float] | None:
    metadata = metadata or {}
    if not metadata_has_current_contract(metadata):
        return None
    try:
        return {key: float(metadata[key]) for key in SOURCE_METRIC_KEYS}
    except (KeyError, TypeError, ValueError):
        return None


def expected_fingerprint(
    metadata: dict[str, Any] | None,
    *,
    raw_filepath: str | None,
    rendered_image_bytes: bytes | None,
) -> str | None:
    metadata = metadata or {}
    provenance = metadata.get("source_embedding_provenance")
    if provenance == RAW_PREVIEW_PROVENANCE:
        return raw_file_fingerprint(raw_filepath)
    if provenance == RENDERED_PREVIEW_PROVENANCE and rendered_image_bytes is not None:
        return rendered_preview_fingerprint(rendered_image_bytes)
    return None


def compatible_embedding(
    record: dict[str, Any] | None,
    *,
    raw_filepath: str | None,
    rendered_image_bytes: bytes | None,
) -> list[float] | None:
    """Return a vector only after validating its complete source contract."""
    if not record or not record.get("ids"):
        return None
    metadatas = record.get("metadatas") or []
    metadata = metadatas[0] if metadatas else {}
    if not metadata_has_current_contract(metadata):
        return None
    expected = expected_fingerprint(
        metadata,
        raw_filepath=raw_filepath,
        rendered_image_bytes=rendered_image_bytes,
    )
    if not expected or expected != metadata.get("source_embedding_fingerprint"):
        return None
    embeddings = record.get("embeddings")
    if embeddings is None or len(embeddings) == 0:
        return None
    vector = np.asarray(embeddings[0], dtype=np.float32).reshape(-1)
    if vector.size == 0 or not np.all(np.isfinite(vector)) or np.allclose(vector, 0.0):
        return None
    return vector.tolist()
