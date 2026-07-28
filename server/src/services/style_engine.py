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
    ) -> None:
        self.recipe = recipe
        self.confidence = confidence
        self.matched_count = matched_count
        self.engine = engine
        self.warning = warning
        self.error = error
        self.matched_filenames = matched_filenames or []


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
    style_override: str | None = None,
    do_not_clip: bool = True,
) -> StyleEngineResult:
    """Predict one absolute edit, abstaining on unsupported or ambiguous input."""
    del min_confidence, do_not_clip
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
            "source_provenance": "raw_preview",
        }
        training_count = training_service.get_training_count()
        if training_count >= 500:
            from services.knn_regression import predict_knn_local_regression
            prediction_result = predict_knn_local_regression(
                query_embedding=clip_embedding,
                metadata=query_metadata,
                current_settings=current_settings,
                strength=style_strength if style_strength is not None else 1.0,
            )
            # If KNN returned a result, we just return it immediately.
            # If it abstained, we let it fall through (it'll go to LLM or return None).
            if prediction_result is not None:
                return prediction_result
            
            # If KNN abstained, prediction is None to trigger fallbacks below
            prediction = None
        else:
            prediction = policy_runtime.predict_absolute_edit(
                embedding=clip_embedding,
                metadata=query_metadata,
                current_settings=current_settings,
                strength=style_strength if style_strength is not None else 1.0,
                policy_override=style_override,
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
    return StyleEngineResult(
        recipe=_canonical_to_edit_recipe(prediction.applied, summary),
        confidence=round(prediction.confidence, 3),
        matched_count=prediction.example_count,
        engine="policy_v2",
        matched_filenames=[prediction.policy_name],
    )
