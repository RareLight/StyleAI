"""Versioned edit-inference contracts and Lightroom state fingerprints."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .policy_targets import default_flat_target_value, flatten_absolute_target
from .training import normalize_develop_settings_for_style


EDIT_INFERENCE_SCHEMA_VERSION = "edit-inference-v2"
EDIT_EVENT_SCHEMA_VERSION = "edit-event-v1"
EDIT_STATE_SCHEMA_VERSION = "edit-state-v1"

TERMINAL_USER_OUTCOMES = frozenset({"accepted", "rejected", "modified_and_kept"})
APPLICATION_EVENT_KINDS = frozenset(
    {"not_applied", "apply_confirmed", "apply_failed", "apply_unconfirmed"}
)
RECONCILIATION_EVENT_KINDS = frozenset({"reverted", "diverged"})
EVENT_KINDS = frozenset(
    {"generated"}
    | APPLICATION_EVENT_KINDS
    | RECONCILIATION_EVENT_KINDS
    | TERMINAL_USER_OUTCOMES
)


def _finite_float(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("edit state values must be finite")
    # Lightroom values do not require binary floating-point precision in an
    # identity digest. Eight decimal places remains well below SDK precision.
    return round(number, 8)


def recipe_target_state(recipe: dict[str, Any]) -> dict[str, float]:
    """Return the exact scalar target state represented by an edit recipe."""
    if (
        not isinstance(recipe, dict)
        or not isinstance(recipe.get("global"), dict)
        or not recipe["global"]
    ):
        raise ValueError("edit recipe must contain global settings")
    flattened = flatten_absolute_target(recipe["global"])
    if "white_balance" not in recipe["global"]:
        flattened.pop("white_balance_is_custom", None)
    if not flattened:
        raise ValueError("edit recipe does not contain modeled global settings")
    return {key: _finite_float(value) for key, value in sorted(flattened.items())}


def develop_settings_state(
    develop_settings: dict[str, Any],
    modeled_keys: list[str] | tuple[str, ...],
) -> dict[str, float]:
    """Project raw Lightroom Develop settings onto one recipe's scalar state."""
    if not isinstance(develop_settings, dict):
        raise ValueError("develop settings must be an object")
    keys = tuple(sorted({str(key) for key in modeled_keys if str(key)}))
    if not keys:
        raise ValueError("modeled keys are required")
    canonical = normalize_develop_settings_for_style(develop_settings)
    flattened = flatten_absolute_target(canonical)
    return {
        key: _finite_float(flattened.get(key, default_flat_target_value(key)))
        for key in keys
    }


def state_fingerprint(state: dict[str, float]) -> str:
    """Hash a canonical modeled state using stable JSON ordering."""
    if not state:
        raise ValueError("edit state is required")
    normalized = {key: _finite_float(value) for key, value in sorted(state.items())}
    payload = json.dumps(
        {
            "schema_version": EDIT_STATE_SCHEMA_VERSION,
            "values": normalized,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def classify_reconciled_state(
    *,
    current_fingerprint: str,
    pre_edit_fingerprint: str,
    applied_fingerprint: str,
) -> str:
    """Classify a later Lightroom state without inferring user intent."""
    if not current_fingerprint:
        raise ValueError("current fingerprint is required")
    if current_fingerprint == applied_fingerprint:
        return "apply_confirmed"
    if current_fingerprint == pre_edit_fingerprint:
        return "reverted"
    return "diverged"
