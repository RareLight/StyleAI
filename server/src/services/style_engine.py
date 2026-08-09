"""Source-conditioned, absolute-target Lightroom editing-policy engine."""

from __future__ import annotations

from typing import Any

from config import logger
from . import policy_runtime
from . import training as training_service


CONFIDENCE_LOW = 0.30

_TONE_CURVE_KEYS = {
    "tone_curve_highlights": "highlights",
    "tone_curve_lights": "lights",
    "tone_curve_darks": "darks",
    "tone_curve_shadows": "shadows",
}


class StyleEngineResult:
    def __init__(
        self,
        recipe: dict[str, Any],
        confidence: float,
        matched_count: int,
        engine: str = "policy_v2",
        warning: str | None = None,
        error: str | None = None,
        matched_filenames: list[str] | None = None,
        generation_id: str | None = None,
        policy_id: str | None = None,
        hard_partition_key: str = "default",
        entropy: float | None = None,
        rendering_intent: dict[str, Any] | None = None,
    ) -> None:
        self.recipe = recipe
        self.confidence = confidence
        self.matched_count = matched_count
        self.engine = engine
        self.warning = warning
        self.error = error
        self.matched_filenames = matched_filenames or []
        self.generation_id = generation_id
        self.policy_id = policy_id
        self.hard_partition_key = hard_partition_key
        self.entropy = entropy
        self.rendering_intent = rendering_intent


def _canonical_to_edit_recipe(
    canonical: dict[str, Any],
    summary: str = "",
) -> dict[str, Any]:
    """Convert an absolute canonical target to the plugin recipe contract."""
    global_settings = {
        key: value
        for key, value in canonical.items()
        if key not in ("tone_curve",) and key not in _TONE_CURVE_KEYS
    }
    tone_curve = dict(canonical.get("tone_curve") or {})
    for canonical_key, recipe_key in _TONE_CURVE_KEYS.items():
        if canonical_key in canonical:
            tone_curve[recipe_key] = canonical[canonical_key]
    if tone_curve:
        global_settings["tone_curve"] = tone_curve
    return {
        "summary": summary or "StyleAI conditional editing policy",
        "global": global_settings,
        "masks": [],
        "warnings": [],
    }


def _no_result(message: str) -> StyleEngineResult:
    return StyleEngineResult(
        recipe={},
        confidence=0.0,
        matched_count=0,
        engine="none",
        warning=message,
    )


def generate_style_edit(
    photo_id: str,
    image_bytes: bytes,
    *,
    focal_length: float | None = None,
    capture_time_unix: float | None = None,
    clip_embedding: list[float] | None = None,
    camera_make: str | None = None,
    camera_model: str | None = None,
    camera_profile: str | None = None,
    user_keywords: list[str] | None = None,
    min_confidence: float = CONFIDENCE_LOW,
    current_settings: dict[str, Any] | None = None,
    style_strength: float | None = None,
    profile_mode: str = "suggest",
    hdr_mode: str = "suggest",
    source_provenance: str = "unknown",
) -> StyleEngineResult:
    """Predict one absolute edit, abstaining on unsupported or ambiguous input."""
    del min_confidence
    if not policy_runtime.has_active_generation():
        return _no_result(
            "No trained editing-policy generation is active. "
            "Run Train AI Style or Reset & Discover after saving at least "
            f"{policy_runtime.MIN_PARTITION_EXAMPLES} compatible examples."
        )
    if clip_embedding is None:
        return _no_result(
            "The active editing policy requires a valid source-image embedding."
        )

    try:
        query_exposure = training_service.compute_exposure_metrics(image_bytes)
        query_metadata: dict[str, Any] = {
            **query_exposure,
            "camera_make": camera_make,
            "camera_model": camera_model,
            "camera_profile": camera_profile,
            "focal_length": focal_length,
            "capture_time": capture_time_unix,
            "user_keywords": user_keywords or [],
            "source_provenance": source_provenance,
            "develop_settings": current_settings or {},
        }
        prediction = policy_runtime.predict_absolute_edit(
            embedding=clip_embedding,
            metadata=query_metadata,
            current_settings=current_settings,
            strength=style_strength if style_strength is not None else 1.0,
            profile_mode=profile_mode,
            hdr_mode=hdr_mode,
            source_provenance=source_provenance,
        )
    except Exception as exc:
        logger.error(
            "Editing-policy inference failed for photo_id=%s: %s",
            photo_id,
            exc,
            exc_info=True,
        )
        return StyleEngineResult(
            recipe={},
            confidence=0.0,
            matched_count=0,
            engine="error",
            error=str(exc),
        )

    if prediction is None:
        return _no_result(
            "No high-confidence editing policy matched this photo and "
            "camera-profile/HDR partition."
        )
    summary = (
        f"Editing Policy: {prediction.policy_name} "
        f"(confidence {prediction.confidence:.0%})"
    )
    recipe = _canonical_to_edit_recipe(prediction.applied, summary)
    rendering_intent = getattr(prediction, "rendering_intent", None)
    if rendering_intent is not None:
        recipe["rendering_intent"] = rendering_intent
    return StyleEngineResult(
        recipe=recipe,
        confidence=round(prediction.confidence, 3),
        matched_count=prediction.example_count,
        engine="policy_v2",
        matched_filenames=[prediction.policy_name],
        generation_id=getattr(prediction, "generation_id", None),
        policy_id=prediction.policy_id,
        hard_partition_key=getattr(prediction, "hard_partition_key", "default"),
        entropy=getattr(prediction, "entropy", None),
        rendering_intent=rendering_intent,
    )
