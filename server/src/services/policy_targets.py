"""Absolute Lightroom targets and current-to-target recipe interpolation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any

import numpy as np


TARGET_SCHEMA_VERSION = "policy-target-v1"


def flatten_absolute_target(canonical: dict[str, Any]) -> dict[str, float]:
    """Flatten supported absolute Lightroom targets into stable scalar keys."""
    flat: dict[str, float] = {}
    for key, value in canonical.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            flat[key] = float(value)

    for color, properties in canonical.get("hsl", {}).items():
        if isinstance(properties, dict):
            for property_name, value in properties.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    flat[f"hsl_{color}_{property_name}"] = float(value)

    for region, properties in canonical.get("color_grading", {}).items():
        if isinstance(properties, dict):
            for property_name, value in properties.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    flat[f"cg_{region}_{property_name}"] = float(value)
        elif isinstance(properties, (int, float)) and not isinstance(properties, bool):
            flat[f"cg_{region}"] = float(properties)

    point_curves = canonical.get("tone_curve", {}).get("point_curve", {})
    for channel, curve in point_curves.items():
        if isinstance(curve, list) and len(curve) >= 4:
            x_values = curve[::2]
            y_values = curve[1::2]
            evaluation_points = np.linspace(0, 255, 16)
            sampled = np.interp(evaluation_points, x_values, y_values)
            for index, value in enumerate(sampled):
                flat[f"curve_{channel}_y_{index}"] = float(value)

    crop = canonical.get("crop", {})
    if isinstance(crop, dict):
        for property_name in ("left", "right", "top", "bottom", "angle"):
            value = crop.get(property_name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                flat[f"crop_{property_name}"] = float(value)

    white_balance = canonical.get("white_balance", "As Shot")
    flat["white_balance_is_custom"] = (
        0.0 if str(white_balance).casefold() == "as shot" else 1.0
    )
    return flat


def default_flat_target_value(key: str) -> float:
    if key.startswith("crop_") and key != "crop_angle":
        return 1.0
    if key == "cg_blending":
        return 50.0
    if key.startswith("curve_"):
        try:
            point_index = int(key.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            return 0.0
        return float(np.linspace(0, 255, 16)[point_index])
    return 0.0


def unflatten_absolute_target(flat: dict[str, float]) -> dict[str, Any]:
    """Rebuild a nested absolute target from scalar model predictions."""
    canonical: dict[str, Any] = {
        "hsl": {},
        "color_grading": {},
        "tone_curve": {"point_curve": {}},
    }
    curve_values: dict[str, dict[int, float]] = {}
    for key, raw_value in flat.items():
        value = float(raw_value)
        if key.startswith("hsl_"):
            _, color, property_name = key.split("_", 2)
            canonical["hsl"].setdefault(color, {})[property_name] = value
        elif key.startswith("cg_"):
            parts = key.split("_")
            if len(parts) == 3:
                _, region, property_name = parts
                canonical["color_grading"].setdefault(region, {})[property_name] = value
            elif len(parts) == 2:
                canonical["color_grading"][parts[1]] = value
        elif key.startswith("curve_"):
            _, channel, _, index = key.split("_")
            curve_values.setdefault(channel, {})[int(index)] = value
        elif key.startswith("crop_"):
            canonical.setdefault("crop", {})[key.removeprefix("crop_")] = value
        elif key == "white_balance_is_custom":
            canonical["white_balance"] = "Custom" if value >= 0.7 else "As Shot"
        else:
            canonical[key] = value

    evaluation_points = np.linspace(0, 255, 16)
    for channel, values in curve_values.items():
        if len(values) != 16:
            continue
        curve: list[float] = []
        for index, x_value in enumerate(evaluation_points):
            curve.extend((float(x_value), float(values[index])))
        canonical["tone_curve"]["point_curve"][channel] = curve

    if not canonical["hsl"]:
        canonical.pop("hsl")
    if not canonical["color_grading"]:
        canonical.pop("color_grading")
    if not canonical["tone_curve"]["point_curve"]:
        canonical.pop("tone_curve")
    if canonical.get("white_balance") == "As Shot":
        canonical.pop("temperature", None)
        canonical.pop("tint", None)
    return canonical


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

    def validate(self) -> None:
        if self.schema_version != TARGET_SCHEMA_VERSION:
            raise ValueError("unsupported absolute-target schema version")
        if not self.process_version:
            raise ValueError("process version is required")
        if not isinstance(self.values, dict) or not self.values:
            raise ValueError("absolute target values are required")
        if not self.modeled_paths:
            raise ValueError("modeled target paths are required")


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
    if bounded_strength >= 1.0:
        return deepcopy(target_values)
    current = current_values if isinstance(current_values, dict) else {}
    return _interpolate_value(current, target_values, bounded_strength, ())
