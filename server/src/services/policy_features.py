"""Versioned, genre-neutral source features for editing-policy models."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np


FEATURE_SCHEMA_VERSION = "policy-source-v2"

SOURCE_METRIC_KEYS = (
    "exp_luminance_mean",
    "exp_luminance_std",
    "exp_highlight_ratio",
    "exp_shadow_ratio",
    "exp_midtone_ratio",
    "exp_colorfulness",
    "exp_warmth_proxy",
    "exp_contrast",
    "zone_deep_shadows",
    "zone_shadows",
    "zone_midtones",
    "zone_highlights",
    "zone_bright_highlights",
    "shadow_headroom",
    "highlight_headroom",
)


@dataclass(frozen=True)
class SourceFeatureVector:
    schema_version: str
    names: tuple[str, ...]
    values: tuple[float, ...]
    availability: tuple[bool, ...]
    categories: dict[str, str]
    source_provenance: str

    def validate(self) -> None:
        if not self.source_provenance:
            raise ValueError("source provenance is required")
        if not (len(self.names) == len(self.values) == len(self.availability)):
            raise ValueError("feature names, values, and availability differ in length")
        if len(set(self.names)) != len(self.names):
            raise ValueError("feature names must be unique")
        if not all(math.isfinite(value) for value in self.values):
            raise ValueError("feature values must be finite")


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _normalized_vector(values: list[float] | np.ndarray | None) -> np.ndarray:
    if values is None:
        return np.empty(0, dtype=np.float64)
    vector = np.asarray(values, dtype=np.float64).reshape(-1)
    if not np.all(np.isfinite(vector)):
        raise ValueError("embedding contains non-finite values")
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def build_source_feature_vector(
    metadata: dict[str, Any],
    *,
    image_embedding: list[float] | np.ndarray | None,
    source_provenance: str,
    semantic_embedding: list[float] | np.ndarray | None = None,
) -> SourceFeatureVector:
    """Build a fixed-order vector without interpreting photographic genres."""
    names: list[str] = []
    values: list[float] = []
    availability: list[bool] = []

    image_vector = _normalized_vector(image_embedding)
    for index, value in enumerate(image_vector):
        names.append(f"image_embedding_{index:04d}")
        values.append(float(value))
        availability.append(True)

    semantic_vector = _normalized_vector(semantic_embedding)
    for index, value in enumerate(semantic_vector):
        names.append(f"semantic_embedding_{index:04d}")
        values.append(float(value))
        availability.append(True)

    for feature_name in SOURCE_METRIC_KEYS:
        parsed = _finite_float(metadata.get(feature_name))
        names.append(feature_name)
        values.append(parsed if parsed is not None else 0.0)
        availability.append(parsed is not None)

    transformed_exif = (
        ("log_iso", metadata.get("iso"), lambda value: math.log1p(max(0.0, value))),
        (
            "log_aperture",
            metadata.get("aperture"),
            lambda value: math.log2(value) if value > 0 else 0.0,
        ),
        (
            "log_focal_length",
            metadata.get("focal_length"),
            lambda value: math.log1p(max(0.0, value)),
        ),
    )
    for feature_name, raw_value, transform in transformed_exif:
        parsed = _finite_float(raw_value)
        names.append(feature_name)
        values.append(transform(parsed) if parsed is not None else 0.0)
        availability.append(parsed is not None)

    categories = {
        key: str(metadata.get(key) or "unknown")
        for key in ("camera_make", "camera_model", "camera_profile", "lens")
    }
    categories["hdr_state"] = "hdr" if bool(metadata.get("is_hdr")) else "sdr"

    result = SourceFeatureVector(
        schema_version=FEATURE_SCHEMA_VERSION,
        names=tuple(names),
        values=tuple(values),
        availability=tuple(availability),
        categories=categories,
        source_provenance=source_provenance,
    )
    result.validate()
    return result
