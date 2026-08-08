"""Transactional catalog-local persistence for edit inference history."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from .edit_events import (
    EDIT_EVENT_SCHEMA_VERSION,
    EDIT_INFERENCE_SCHEMA_VERSION,
    EVENT_KINDS,
    TERMINAL_USER_OUTCOMES,
    classify_reconciled_state,
    develop_settings_state,
    recipe_target_state,
    state_fingerprint,
)
from .policy_store import connect_policy_store
from .rendering_state import rendering_state_from_settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def _optional_finite(value: Any, field: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _event_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["explicit_user_action"] = bool(result["explicit_user_action"])
    result["observed_state"] = (
        json.loads(result.pop("observed_state_json"))
        if result["observed_state_json"] is not None
        else None
    )
    result["details"] = json.loads(result.pop("details_json"))
    return result


def _inference_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for stored_key, output_key in (
        ("modeled_keys_json", "modeled_keys"),
        ("pre_edit_state_json", "pre_edit_state"),
        ("target_state_json", "target_state"),
    ):
        result[output_key] = json.loads(result.pop(stored_key))
    for stored_key, output_key in (
        ("current_rendering_state_json", "current_rendering_state"),
        ("target_rendering_state_json", "target_rendering_state"),
        ("rendering_intent_json", "rendering_intent"),
    ):
        raw = result.pop(stored_key, None)
        result[output_key] = json.loads(raw) if raw else None
    return result


def create_inference(
    *,
    db_path: str,
    photo_id: str,
    engine: str,
    algorithm_version: str,
    feature_schema_version: str,
    target_schema_version: str,
    hard_partition_key: str,
    modeled_keys: list[str],
    pre_edit_state: dict[str, float],
    pre_edit_fingerprint: str,
    target_state: dict[str, float],
    target_fingerprint: str,
    generation_id: str | None = None,
    policy_id: str | None = None,
    confidence: float | None = None,
    entropy: float | None = None,
    strength: float = 1.0,
    summary: str = "",
    inference_id: str | None = None,
    rendering_intent: dict[str, Any] | None = None,
) -> str:
    """Atomically store an immutable inference and its generated event."""
    required = (
        photo_id,
        engine,
        algorithm_version,
        feature_schema_version,
        target_schema_version,
        pre_edit_fingerprint,
        target_fingerprint,
    )
    if not all(required) or not modeled_keys or not pre_edit_state or not target_state:
        raise ValueError("complete edit inference provenance is required")
    new_id = inference_id or uuid4().hex
    created_at = _utc_now()
    connection = connect_policy_store(db_path)
    try:
        with _transaction(connection):
            connection.execute(
                """
                INSERT INTO policy_v2_edit_inferences (
                    inference_id, photo_id, generation_id, policy_id,
                    hard_partition_key, engine, algorithm_version,
                    feature_schema_version, target_schema_version,
                    inference_schema_version, confidence, entropy, strength,
                    summary, modeled_keys_json, pre_edit_state_json,
                    pre_edit_fingerprint, target_state_json,
                    target_fingerprint, created_at,
                    current_rendering_state_json, target_rendering_state_json,
                    rendering_intent_json, rendering_selector_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    photo_id,
                    generation_id,
                    policy_id,
                    hard_partition_key or "default",
                    engine,
                    algorithm_version,
                    feature_schema_version,
                    target_schema_version,
                    EDIT_INFERENCE_SCHEMA_VERSION,
                    _optional_finite(confidence, "confidence"),
                    _optional_finite(entropy, "entropy"),
                    _optional_finite(strength, "strength"),
                    summary,
                    json.dumps(sorted(set(modeled_keys)), separators=(",", ":")),
                    json.dumps(pre_edit_state, sort_keys=True, separators=(",", ":")),
                    pre_edit_fingerprint,
                    json.dumps(target_state, sort_keys=True, separators=(",", ":")),
                    target_fingerprint,
                    created_at,
                    (
                        json.dumps(rendering_intent.get("current"), sort_keys=True)
                        if rendering_intent and rendering_intent.get("current")
                        else None
                    ),
                    (
                        json.dumps(rendering_intent.get("effective"), sort_keys=True)
                        if rendering_intent and rendering_intent.get("effective")
                        else None
                    ),
                    json.dumps(rendering_intent, sort_keys=True)
                    if rendering_intent
                    else None,
                    rendering_intent.get("selector_algorithm_version")
                    if rendering_intent
                    else None,
                ),
            )
            connection.execute(
                """
                INSERT INTO policy_v2_edit_events (
                    event_id, inference_id, idempotency_key, event_kind,
                    event_schema_version, explicit_user_action, details_json,
                    created_at
                ) VALUES (?, ?, ?, 'generated', ?, 0, '{}', ?)
                """,
                (
                    uuid4().hex,
                    new_id,
                    f"generated:{new_id}",
                    EDIT_EVENT_SCHEMA_VERSION,
                    created_at,
                ),
            )
    finally:
        connection.close()
    return new_id


def create_recipe_inference(
    *,
    db_path: str,
    photo_id: str,
    recipe: dict[str, Any],
    current_settings: dict[str, Any],
    engine: str,
    algorithm_version: str,
    feature_schema_version: str,
    target_schema_version: str,
    generation_id: str | None = None,
    policy_id: str | None = None,
    hard_partition_key: str = "default",
    confidence: float | None = None,
    entropy: float | None = None,
    strength: float = 1.0,
) -> str:
    """Build canonical state evidence and persist one generated recipe."""
    target_state = recipe_target_state(recipe)
    rendering_intent = (
        recipe.get("rendering_intent")
        if isinstance(recipe.get("rendering_intent"), dict)
        else None
    )
    modeled_keys = list(target_state)
    pre_edit_state = develop_settings_state(current_settings, modeled_keys)
    return create_inference(
        db_path=db_path,
        photo_id=photo_id,
        generation_id=generation_id,
        policy_id=policy_id,
        hard_partition_key=hard_partition_key,
        engine=engine,
        algorithm_version=algorithm_version,
        feature_schema_version=feature_schema_version,
        target_schema_version=target_schema_version,
        modeled_keys=modeled_keys,
        pre_edit_state=pre_edit_state,
        pre_edit_fingerprint=state_fingerprint(pre_edit_state),
        target_state=target_state,
        target_fingerprint=state_fingerprint(target_state),
        confidence=confidence,
        entropy=entropy,
        strength=strength,
        summary=str(recipe.get("summary") or ""),
        rendering_intent=rendering_intent,
    )


def append_event(
    *,
    db_path: str,
    inference_id: str,
    event_kind: str,
    idempotency_key: str,
    explicit_user_action: bool = False,
    observed_state: dict[str, float] | None = None,
    observed_fingerprint: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an event once; an idempotent retry returns the original event."""
    if event_kind not in EVENT_KINDS or event_kind == "generated":
        raise ValueError("unsupported appendable edit event kind")
    if not inference_id or not idempotency_key:
        raise ValueError("inference_id and idempotency_key are required")
    if (observed_state is None) != (observed_fingerprint is None):
        raise ValueError("observed state and fingerprint must be supplied together")
    connection = connect_policy_store(db_path)
    try:
        existing = connection.execute(
            "SELECT * FROM policy_v2_edit_events WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            if (
                existing["inference_id"] != inference_id
                or existing["event_kind"] != event_kind
            ):
                raise ValueError("idempotency key belongs to a different event")
            return _event_row(existing)
        if not connection.execute(
            "SELECT 1 FROM policy_v2_edit_inferences WHERE inference_id = ?",
            (inference_id,),
        ).fetchone():
            raise LookupError("edit inference was not found")
        event_id = uuid4().hex
        with _transaction(connection):
            connection.execute(
                """
                INSERT INTO policy_v2_edit_events (
                    event_id, inference_id, idempotency_key, event_kind,
                    event_schema_version, explicit_user_action,
                    observed_state_json, observed_fingerprint,
                    details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    inference_id,
                    idempotency_key,
                    event_kind,
                    EDIT_EVENT_SCHEMA_VERSION,
                    int(bool(explicit_user_action)),
                    (
                        json.dumps(
                            observed_state, sort_keys=True, separators=(",", ":")
                        )
                        if observed_state is not None
                        else None
                    ),
                    observed_fingerprint,
                    json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
                    _utc_now(),
                ),
            )
        row = connection.execute(
            "SELECT * FROM policy_v2_edit_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return _event_row(row)
    finally:
        connection.close()


def record_application(
    *,
    db_path: str,
    inference_id: str,
    event_kind: str,
    idempotency_key: str,
    current_settings: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record Lightroom's application result with canonical readback evidence."""
    if event_kind not in {
        "apply_confirmed",
        "apply_failed",
        "apply_unconfirmed",
        "not_applied",
    }:
        raise ValueError("invalid application event kind")
    inference = get_inference(db_path=db_path, inference_id=inference_id)
    if inference is None:
        raise LookupError("edit inference was not found")
    observed_state = None
    observed_fingerprint = None
    if event_kind == "apply_confirmed":
        if not isinstance(current_settings, dict):
            raise ValueError("confirmed application requires Lightroom readback")
        observed_state = develop_settings_state(
            current_settings,
            inference["modeled_keys"],
        )
        observed_fingerprint = state_fingerprint(observed_state)
        details = dict(details or {})
        target_rendering = inference.get("target_rendering_state") or {}
        target_profile = target_rendering.get("profile") or {}
        details["observed_rendering_state"] = rendering_state_from_settings(
            current_settings,
            camera_make=target_profile.get("camera_make"),
            camera_model=target_profile.get("camera_model"),
        )
    return append_event(
        db_path=db_path,
        inference_id=inference_id,
        event_kind=event_kind,
        idempotency_key=idempotency_key,
        observed_state=observed_state,
        observed_fingerprint=observed_fingerprint,
        details=details,
    )


def get_inference(*, db_path: str, inference_id: str) -> dict[str, Any] | None:
    connection = connect_policy_store(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM policy_v2_edit_inferences WHERE inference_id = ?",
            (inference_id,),
        ).fetchone()
        if not row:
            return None
        result = _inference_row(row)
        result["events"] = [
            _event_row(event)
            for event in connection.execute(
                """
                SELECT * FROM policy_v2_edit_events
                WHERE inference_id = ? ORDER BY event_sequence
                """,
                (inference_id,),
            ).fetchall()
        ]
        return result
    finally:
        connection.close()


def _latest_reconcilable_inference(
    *, db_path: str, photo_id: str
) -> dict[str, Any] | None:
    """Return the newest inference whose edit was applied or plausibly applied."""
    connection = connect_policy_store(db_path)
    try:
        row = connection.execute(
            """
            SELECT inference_id
            FROM policy_v2_edit_inferences AS inference
            WHERE photo_id = ?
              AND EXISTS (
                  SELECT 1 FROM policy_v2_edit_events AS event
                  WHERE event.inference_id = inference.inference_id
                    AND event.event_kind IN ('apply_confirmed', 'apply_unconfirmed')
              )
            ORDER BY created_at DESC, inference_id DESC
            LIMIT 1
            """,
            (photo_id,),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return None
    return get_inference(db_path=db_path, inference_id=row["inference_id"])


def reconcile_photo_state(
    *,
    db_path: str,
    photo_id: str,
    current_settings: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile one photo without inferring what caused its current state."""
    if not photo_id or not isinstance(current_settings, dict):
        raise ValueError("photo_id and current_settings are required")
    inference = _latest_reconcilable_inference(db_path=db_path, photo_id=photo_id)
    if inference is None:
        return {
            "photo_id": photo_id,
            "inference_id": None,
            "state": "untracked",
            "recorded": False,
        }

    application = next(
        (
            event
            for event in reversed(inference["events"])
            if event["event_kind"] in {"apply_confirmed", "apply_unconfirmed"}
        ),
        None,
    )
    applied_fingerprint = (
        application["observed_fingerprint"]
        if application and application["observed_fingerprint"]
        else inference["target_fingerprint"]
    )
    current_state = develop_settings_state(
        current_settings,
        inference["modeled_keys"],
    )
    current_fingerprint = state_fingerprint(current_state)
    state = classify_reconciled_state(
        current_fingerprint=current_fingerprint,
        pre_edit_fingerprint=inference["pre_edit_fingerprint"],
        applied_fingerprint=applied_fingerprint,
    )
    latest = inference["events"][-1]
    if (
        latest["event_kind"] == state
        and latest["observed_fingerprint"] == current_fingerprint
    ):
        return {
            "photo_id": photo_id,
            "inference_id": inference["inference_id"],
            "state": state,
            "recorded": False,
        }

    event = append_event(
        db_path=db_path,
        inference_id=inference["inference_id"],
        event_kind=state,
        idempotency_key=(
            f"reconcile:{inference['inference_id']}:{latest['event_id']}:"
            f"{state}:{current_fingerprint}"
        ),
        observed_state=current_state,
        observed_fingerprint=current_fingerprint,
        details={"source": "lightroom_readback"},
    )
    return {
        "photo_id": photo_id,
        "inference_id": inference["inference_id"],
        "state": state,
        "recorded": True,
        "event_id": event["event_id"],
    }


def record_user_outcome(
    *,
    db_path: str,
    inference_id: str,
    outcome: str,
    current_settings: dict[str, Any],
) -> dict[str, Any]:
    """Append one explicit user judgment with its contemporaneous readback."""
    if outcome not in TERMINAL_USER_OUTCOMES:
        raise ValueError("invalid edit outcome")
    if not isinstance(current_settings, dict):
        raise ValueError("current_settings are required for an edit outcome")
    inference = get_inference(db_path=db_path, inference_id=inference_id)
    if inference is None:
        raise LookupError("edit inference was not found")
    application = next(
        (
            event
            for event in reversed(inference["events"])
            if event["event_kind"] in {"apply_confirmed", "apply_unconfirmed"}
        ),
        None,
    )
    if application is None:
        raise ValueError("an edit must be applied before it can be reviewed")

    observed_state = develop_settings_state(
        current_settings,
        inference["modeled_keys"],
    )
    observed_fingerprint = state_fingerprint(observed_state)
    applied_fingerprint = (
        application["observed_fingerprint"] or inference["target_fingerprint"]
    )
    reconciled_state = classify_reconciled_state(
        current_fingerprint=observed_fingerprint,
        pre_edit_fingerprint=inference["pre_edit_fingerprint"],
        applied_fingerprint=applied_fingerprint,
    )
    rendering_details: dict[str, Any] = {}
    intent = inference.get("rendering_intent") or {}
    target_rendering = inference.get("target_rendering_state") or {}
    current_rendering = inference.get("current_rendering_state") or {}
    target_profile = (
        target_rendering.get("profile") or current_rendering.get("profile") or {}
    )
    observed_rendering = rendering_state_from_settings(
        current_settings,
        camera_make=target_profile.get("camera_make"),
        camera_model=target_profile.get("camera_model"),
    )
    if intent:
        rendering_details = {
            "observed_rendering_state": observed_rendering,
            "profile_decision": (
                "accepted"
                if observed_rendering.get("profile", {}).get("profile_id")
                == target_rendering.get("profile", {}).get("profile_id")
                else "returned_to_original"
                if observed_rendering.get("profile", {}).get("profile_id")
                == current_rendering.get("profile", {}).get("profile_id")
                else "replaced"
            ),
            "hdr_decision": (
                "accepted"
                if observed_rendering.get("is_hdr") == target_rendering.get("is_hdr")
                else "returned_to_original"
            ),
        }
    if outcome == "accepted" and reconciled_state != "apply_confirmed":
        raise ValueError(
            "the modeled edit changed; use modified_and_kept instead of accepted"
        )

    latest = inference["events"][-1]
    if (
        latest["event_kind"] == outcome
        and latest["observed_fingerprint"] == observed_fingerprint
    ):
        return {
            "photo_id": inference["photo_id"],
            "inference_id": inference_id,
            "outcome": outcome,
            "state": reconciled_state,
            "recorded": False,
            "event_id": latest["event_id"],
        }
    event = append_event(
        db_path=db_path,
        inference_id=inference_id,
        event_kind=outcome,
        idempotency_key=(
            f"outcome:{inference_id}:{latest['event_id']}:"
            f"{outcome}:{observed_fingerprint}"
        ),
        explicit_user_action=True,
        observed_state=observed_state,
        observed_fingerprint=observed_fingerprint,
        details={"reconciled_state": reconciled_state, **rendering_details},
    )
    return {
        "photo_id": inference["photo_id"],
        "inference_id": inference_id,
        "outcome": outcome,
        "state": reconciled_state,
        "recorded": True,
        "event_id": event["event_id"],
    }


def iter_inference_history_batches(
    *,
    db_path: str,
    page_size: int = 500,
    created_after: str | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Read complete inference histories with bounded, keyset-paginated SQL."""
    if page_size < 1 or page_size > 900:
        raise ValueError("page_size must be between 1 and 900")
    connection = connect_policy_store(db_path)
    last_created = created_after or ""
    last_id = ""
    try:
        while True:
            rows = connection.execute(
                """
                SELECT * FROM policy_v2_edit_inferences
                WHERE (created_at > ?)
                   OR (created_at = ? AND inference_id > ?)
                ORDER BY created_at, inference_id
                LIMIT ?
                """,
                (last_created, last_created, last_id, page_size),
            ).fetchall()
            if not rows:
                break
            histories = [_inference_row(row) for row in rows]
            by_id = {history["inference_id"]: history for history in histories}
            for history in histories:
                history["events"] = []
            placeholders = ",".join("?" for _ in histories)
            events = connection.execute(
                f"""
                SELECT * FROM policy_v2_edit_events
                WHERE inference_id IN ({placeholders})
                ORDER BY event_sequence
                """,
                tuple(by_id),
            ).fetchall()
            for event in events:
                parsed = _event_row(event)
                by_id[parsed["inference_id"]]["events"].append(parsed)
            yield histories
            last_created = rows[-1]["created_at"]
            last_id = rows[-1]["inference_id"]
    finally:
        connection.close()
