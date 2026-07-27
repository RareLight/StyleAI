"""Absolute Lightroom targets and current-to-target recipe interpolation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any


TARGET_SCHEMA_VERSION = "policy-target-v1"

_TOP_LEVEL_NEUTRALS: dict[str, float] = {
    "sharpening": 40.0,
    "color_noise_reduction": 25.0,
}
_CATEGORICAL_THRESHOLDS: dict[str, float] = {
    "white_balance": 0.7,
}


@dataclass(frozen=True)
class AbsoluteTarget:
    schema_version: str
    process_version: str
    values: dict[str, Any]
    modeled_paths: tuple[str, ...]


def _neutral_for_path(path: tuple[str, ...]) -> Any:
    if not path:
        return None
    if path[0] == "crop":
        if path[-1] in ("right", "bottom"):
            return 1.0
        return 0.0
    if path[:2] == ("color_grading", "blending"):
        return 50.0
    if path[0] in ("hsl", "color_grading"):
        return 0.0
    return _TOP_LEVEL_NEUTRALS.get(path[-1], 0.0)


def _circular_interpolate(start: float, target: float, strength: float) -> float:
    delta = (target - start + 180.0) % 360.0 - 180.0
    return (start + strength * delta) % 360.0


def _interpolate_curve(
    current: list[Any] | None, target: list[Any], strength: float
) -> list[float]:
    if strength >= 1.0:
        return [float(value) for value in target]
    if not target or len(target) % 2 != 0:
        raise ValueError("point curves must contain x/y pairs")
    target_x = [float(value) for value in target[::2]]
    target_y = [float(value) for value in target[1::2]]
    if current and len(current) >= 4 and len(current) % 2 == 0:
        import numpy as np

        current_x = np.asarray(current[::2], dtype=np.float64)
        current_y = np.asarray(current[1::2], dtype=np.float64)
        start_y = np.interp(target_x, current_x, current_y).tolist()
    else:
        start_y = target_x
    output: list[float] = []
    for x_value, start_value, target_value in zip(
        target_x, start_y, target_y, strict=True
    ):
        output.extend([x_value, start_value + strength * (target_value - start_value)])
    return output


def _interpolate_value(
    current: Any,
    target: Any,
    strength: float,
    path: tuple[str, ...],
) -> Any:
    if isinstance(target, dict):
        current_dict = current if isinstance(current, dict) else {}
        return {
            key: _interpolate_value(
                current_dict.get(key, _neutral_for_path((*path, key))),
                value,
                strength,
                (*path, key),
            )
            for key, value in target.items()
        }
    if isinstance(target, list):
        if "point_curve" in path:
            return _interpolate_curve(
                current if isinstance(current, list) else None, target, strength
            )
        return deepcopy(target) if strength >= 1.0 else deepcopy(current or target)
    if isinstance(target, bool):
        return target if strength >= 0.5 else bool(current)
    if isinstance(target, (int, float)) and not isinstance(target, bool):
        start = _neutral_for_path(path) if current is None else float(current)
        target_float = float(target)
        if not math.isfinite(start) or not math.isfinite(target_float):
            raise ValueError(f"non-finite target at {'.'.join(path)}")
        if path and path[0] == "color_grading" and path[-1] == "hue":
            return _circular_interpolate(start, target_float, strength)
        return start + strength * (target_float - start)
    threshold = _CATEGORICAL_THRESHOLDS.get(path[-1] if path else "", 0.5)
    return deepcopy(target if strength >= threshold else current)


def interpolate_absolute_target(
    current_values: dict[str, Any] | None,
    target: AbsoluteTarget | dict[str, Any],
    *,
    strength: float,
) -> dict[str, Any]:
    """Interpolate modeled values from current state to an absolute target."""
    if not math.isfinite(strength):
        raise ValueError("strength must be finite")
    bounded_strength = max(0.0, min(1.0, float(strength)))
    target_values = target.values if isinstance(target, AbsoluteTarget) else target
    if not isinstance(target_values, dict):
        raise ValueError("absolute target must be a dictionary")
    current = current_values if isinstance(current_values, dict) else {}
    return _interpolate_value(current, target_values, bounded_strength, ())
