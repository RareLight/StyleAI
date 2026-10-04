"""Blinded refinement sessions and append-only preference evidence.

The first developer milestone deliberately collects evidence only.  It never
changes training examples, policy generations, model artifacts, or thresholds.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
from typing import Any
from uuid import uuid4

from .edit_events import develop_settings_state, state_fingerprint
from .policy_store import connect_policy_store
from .policy_targets import unflatten_absolute_target


SESSION_SCHEMA_VERSION = "style-refinement-session-v1"
VARIANT_SCHEMA_VERSION = "style-refinement-variant-v1"
EVIDENCE_SCHEMA_VERSION = "style-refinement-evidence-v1"
MAX_SESSION_PAIRS = 100
CHOICES = frozenset({"prefer_a", "prefer_b", "equivalent", "neither", "skip"})

# QR-1 starts with continuous global sliders whose SDK bounds are already
# enforced by DevelopEditManager.  Structural/categorical families remain out.
CONTINUOUS_GLOBAL_BOUNDS: dict[str, tuple[float, float]] = {
    "exposure": (-5.0, 5.0),
    "contrast": (-100.0, 100.0),
    "highlights": (-100.0, 100.0),
    "shadows": (-100.0, 100.0),
    "whites": (-100.0, 100.0),
    "blacks": (-100.0, 100.0),
    "texture": (-100.0, 100.0),
    "clarity": (-100.0, 100.0),
    "dehaze": (-100.0, 100.0),
    "vibrance": (-100.0, 100.0),
    "saturation": (-100.0, 100.0),
    "sharpening": (0.0, 150.0),
    "sharpen_radius": (0.5, 3.0),
    "sharpen_detail": (0.0, 100.0),
    "sharpen_masking": (0.0, 100.0),
    "noise_reduction": (0.0, 100.0),
    "noise_reduction_detail": (0.0, 100.0),
    "noise_reduction_contrast": (0.0, 100.0),
    "color_noise_reduction": (0.0, 100.0),
    "color_noise_reduction_detail": (0.0, 100.0),
    "color_noise_reduction_smoothness": (0.0, 100.0),
    "vignette": (-100.0, 100.0),
    "grain": (0.0, 100.0),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _decode(value: Any, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _active_generation_id(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT generation_id FROM policy_v2_generations WHERE status = 'active'"
    ).fetchone()
    if row is None:
        raise ValueError("an active editing-policy generation is required")
    return str(row["generation_id"])


def _bounded_state(state: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, bounds in CONTINUOUS_GLOBAL_BOUNDS.items():
        value = state.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if not math.isfinite(number):
            continue
        result[key] = max(bounds[0], min(bounds[1], number))
    return result


def _normalized_distance(left: dict[str, float], right: dict[str, float]) -> float:
    keys = sorted(set(left) & set(right))
    if not keys:
        return 0.0
    squared = 0.0
    for key in keys:
        low, high = CONTINUOUS_GLOBAL_BOUNDS[key]
        squared += ((left[key] - right[key]) / (high - low)) ** 2
    return math.sqrt(squared / len(keys))


def _recipe(flat: dict[str, float], *, summary: str) -> dict[str, Any]:
    return {
        "global": unflatten_absolute_target(flat),
        "summary": summary,
        "engine_type": "StyleAI refinement preview",
    }


def _quick_variants(inference: dict[str, Any]) -> tuple[dict, dict, str, str] | None:
    before = _bounded_state(inference["pre_edit_state"])
    target = _bounded_state(inference["target_state"])
    keys = sorted(set(before) & set(target))
    if not keys:
        return None
    # Both variants stay on the existing learned edit direction.  This is a
    # strength comparison, not independent slider jitter.
    softer = {key: before[key] + 0.70 * (target[key] - before[key]) for key in keys}
    full = {key: target[key] for key in keys}
    if _normalized_distance(softer, full) < 0.002:
        return None
    return (
        _recipe(softer, summary="Controlled 70% learned-target variant"),
        _recipe(full, summary="Controlled 100% learned-target variant"),
        "learned_strength_070",
        "learned_strength_100",
    )


def _correction_variants(
    inference: dict[str, Any], latest_outcome: sqlite3.Row | None
) -> tuple[dict, dict, str, str] | None:
    if latest_outcome is None or latest_outcome["event_kind"] != "modified_and_kept":
        return None
    corrected = _bounded_state(_decode(latest_outcome["observed_state_json"], {}) or {})
    predicted = _bounded_state(inference["target_state"])
    keys = sorted(set(corrected) & set(predicted))
    corrected = {key: corrected[key] for key in keys}
    predicted = {key: predicted[key] for key in keys}
    if not keys or _normalized_distance(corrected, predicted) < 0.002:
        return None
    return (
        _recipe(corrected, summary="Recorded Modified and Kept target"),
        _recipe(predicted, summary="Original active-policy target"),
        "recorded_correction",
        "active_policy_prediction",
    )


def _inference_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["modeled_keys"] = _decode(result.pop("modeled_keys_json"), [])
    result["pre_edit_state"] = _decode(result.pop("pre_edit_state_json"), {})
    result["target_state"] = _decode(result.pop("target_state_json"), {})
    return result


def _latest_outcome(
    connection: sqlite3.Connection, inference_id: str
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT event_kind, observed_state_json, observed_fingerprint
        FROM policy_v2_edit_events
        WHERE inference_id = ?
          AND event_kind IN ('accepted', 'rejected', 'modified_and_kept')
        ORDER BY event_sequence DESC LIMIT 1
        """,
        (inference_id,),
    ).fetchone()


def _has_application(connection: sqlite3.Connection, inference_id: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1 FROM policy_v2_edit_events
            WHERE inference_id = ?
              AND event_kind IN ('apply_confirmed', 'apply_unconfirmed')
            LIMIT 1
            """,
            (inference_id,),
        ).fetchone()
        is not None
    )


def preflight(
    *,
    db_path: str,
    inference_ids: list[str],
    mode: str,
    current_fingerprints: dict[str, str] | None = None,
    current_settings: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return authoritative bounded eligibility without creating a session."""
    if mode not in {"quick", "correction_validation"}:
        raise ValueError("mode must be quick or correction_validation")
    normalized = list(dict.fromkeys(str(value).strip() for value in inference_ids))
    if not normalized or any(not value for value in normalized):
        raise ValueError("inference_ids must contain non-empty identifiers")
    if len(normalized) > MAX_SESSION_PAIRS:
        raise ValueError(f"at most {MAX_SESSION_PAIRS} inferences may be refined")
    fingerprints = {
        str(key): str(value)
        for key, value in (current_fingerprints or {}).items()
        if str(key) and str(value)
    }
    settings_by_inference = current_settings or {}
    if not isinstance(settings_by_inference, dict):
        raise ValueError("current_settings must be an object")
    eligible: list[dict[str, Any]] = []
    exclusions: dict[str, int] = {}
    connection = connect_policy_store(db_path)
    try:
        active_generation_id = _active_generation_id(connection)
        for inference_id in normalized:
            row = connection.execute(
                """
                SELECT inference_id, photo_id, generation_id, policy_id,
                       hard_partition_key, modeled_keys_json,
                       pre_edit_state_json, target_state_json
                FROM policy_v2_edit_inferences WHERE inference_id = ?
                """,
                (inference_id,),
            ).fetchone()
            reason = None
            variants = None
            if row is None:
                reason = "missing_inference"
            else:
                inference = _inference_row(row)
                if inference.get("generation_id") != active_generation_id:
                    reason = "inactive_generation"
                elif not _has_application(connection, inference_id):
                    reason = "not_applied"
                else:
                    outcome = _latest_outcome(connection, inference_id)
                    if mode == "correction_validation":
                        if (
                            outcome is None
                            or outcome["event_kind"] != "modified_and_kept"
                        ):
                            reason = "no_modified_and_kept_outcome"
                        else:
                            current_fingerprint = fingerprints.get(inference_id)
                            current = settings_by_inference.get(inference_id)
                            if isinstance(current, dict):
                                current_fingerprint = state_fingerprint(
                                    develop_settings_state(
                                        current, inference["modeled_keys"]
                                    )
                                )
                        if reason is None and current_fingerprint != str(
                            outcome["observed_fingerprint"] or ""
                        ):
                            reason = "stale_or_unverified_readback"
                        elif reason is None:
                            variants = _correction_variants(inference, outcome)
                    else:
                        variants = _quick_variants(inference)
                    if reason is None and variants is None:
                        reason = "no_meaningful_supported_variant"
                if reason is None:
                    eligible.append(
                        {
                            "inference": inference,
                            "variants": variants,
                            "selection_reason": (
                                "explicit_current_correction"
                                if mode == "correction_validation"
                                else "active_applied_inference"
                            ),
                        }
                    )
            if reason:
                exclusions[reason] = exclusions.get(reason, 0) + 1
        return {
            "mode": mode,
            "base_generation_id": active_generation_id,
            "requested_count": len(normalized),
            "eligible_count": len(eligible),
            "excluded_count": len(normalized) - len(eligible),
            "exclusions": exclusions,
            "eligible": eligible,
            "evaluation_only": True,
        }
    finally:
        connection.close()


def create_session(
    *,
    db_path: str,
    inference_ids: list[str],
    mode: str,
    current_fingerprints: dict[str, str] | None = None,
    current_settings: dict[str, dict[str, Any]] | None = None,
    requested_count: int | None = None,
) -> dict[str, Any]:
    assessment = preflight(
        db_path=db_path,
        inference_ids=inference_ids,
        mode=mode,
        current_fingerprints=current_fingerprints,
        current_settings=current_settings,
    )
    eligible = assessment.pop("eligible")
    limit = int(requested_count or len(eligible))
    if limit < 1 or limit > MAX_SESSION_PAIRS:
        raise ValueError(f"requested_count must be between 1 and {MAX_SESSION_PAIRS}")
    eligible = eligible[:limit]
    if not eligible:
        return {**assessment, "session_id": None, "state": None}
    desired_inference_ids = [item["inference"]["inference_id"] for item in eligible]
    session_id = uuid4().hex
    now = _utc_now()
    connection = connect_policy_store(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        active_sessions = connection.execute(
            """
            SELECT session_id
            FROM policy_v2_refinement_sessions
            WHERE mode = ? AND base_generation_id = ? AND state = 'active'
            ORDER BY updated_at DESC
            """,
            (mode, assessment["base_generation_id"]),
        ).fetchall()
        for existing in active_sessions:
            existing_ids = [
                str(row["inference_id"])
                for row in connection.execute(
                    """
                    SELECT inference_id FROM policy_v2_refinement_pairs
                    WHERE session_id = ? ORDER BY ordinal
                    """,
                    (existing["session_id"],),
                ).fetchall()
            ]
            if existing_ids == desired_inference_ids:
                connection.commit()
                existing_summary = get_session(
                    db_path=db_path, session_id=str(existing["session_id"])
                )
                return {
                    **assessment,
                    **(existing_summary or {}),
                    "attached": True,
                }
        connection.execute(
            """
            INSERT INTO policy_v2_refinement_sessions (
                session_id, mode, base_generation_id, state, schema_version,
                requested_count, details_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                mode,
                assessment["base_generation_id"],
                SESSION_SCHEMA_VERSION,
                limit,
                _json({"exclusions": assessment["exclusions"]}),
                now,
                now,
            ),
        )
        for ordinal, item in enumerate(eligible, start=1):
            inference = item["inference"]
            left, right, left_identity, right_identity = item["variants"]
            seed = _hash(
                {
                    "schema": VARIANT_SCHEMA_VERSION,
                    "session_id": session_id,
                    "inference_id": inference["inference_id"],
                }
            )
            if int(seed[-1], 16) % 2:
                recipe_a, recipe_b = right, left
                identity_a, identity_b = right_identity, left_identity
            else:
                recipe_a, recipe_b = left, right
                identity_a, identity_b = left_identity, right_identity
            fingerprint_payload = {
                "schema": VARIANT_SCHEMA_VERSION,
                "base_generation_id": assessment["base_generation_id"],
                "inference_id": inference["inference_id"],
                "identity_a": identity_a,
                "identity_b": identity_b,
                "recipe_a": recipe_a,
                "recipe_b": recipe_b,
            }
            connection.execute(
                """
                INSERT INTO policy_v2_refinement_pairs (
                    pair_id, session_id, ordinal, inference_id, photo_id,
                    policy_id, hard_partition_key, variant_schema_version,
                    generation_method, recipe_a_json, recipe_b_json,
                    identity_a, identity_b, side_assignment_json,
                    pair_fingerprint, selection_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    session_id,
                    ordinal,
                    inference["inference_id"],
                    inference["photo_id"],
                    inference.get("policy_id"),
                    inference.get("hard_partition_key") or "default",
                    VARIANT_SCHEMA_VERSION,
                    (
                        "correction_vs_active_prediction"
                        if mode == "correction_validation"
                        else "learned_target_strength"
                    ),
                    _json(recipe_a),
                    _json(recipe_b),
                    identity_a,
                    identity_b,
                    _json({"a": identity_a, "b": identity_b}),
                    _hash(fingerprint_payload),
                    item["selection_reason"],
                    now,
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        **assessment,
        "session_id": session_id,
        "state": "active",
        "pair_count": len(eligible),
        "attached": False,
    }


def _assert_session_generation(
    connection: sqlite3.Connection, session: sqlite3.Row
) -> None:
    if session["base_generation_id"] != _active_generation_id(connection):
        raise RuntimeError(
            "the active style generation changed; finalize this session and start again"
        )


def get_session(*, db_path: str, session_id: str) -> dict[str, Any] | None:
    connection = connect_policy_store(db_path)
    try:
        session = connection.execute(
            "SELECT * FROM policy_v2_refinement_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            return None
        total = connection.execute(
            "SELECT COUNT(*) FROM policy_v2_refinement_pairs WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        answered = connection.execute(
            """
            SELECT COUNT(*) FROM policy_v2_refinement_pairs AS pair
            WHERE pair.session_id = ? AND EXISTS (
                SELECT 1 FROM policy_v2_refinement_choices AS choice
                WHERE choice.pair_id = pair.pair_id
            )
            """,
            (session_id,),
        ).fetchone()[0]
        result = dict(session)
        result["details"] = _decode(result.pop("details_json"), {})
        result.update(
            {
                "pair_count": int(total),
                "answered_count": int(answered),
                "remaining_count": int(total) - int(answered),
                "evaluation_only": True,
            }
        )
        return result
    finally:
        connection.close()


def next_pair(*, db_path: str, session_id: str) -> dict[str, Any] | None:
    connection = connect_policy_store(db_path)
    try:
        session = connection.execute(
            "SELECT * FROM policy_v2_refinement_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise LookupError("refinement session was not found")
        if session["state"] != "active":
            return None
        _assert_session_generation(connection, session)
        row = connection.execute(
            """
            SELECT pair.*
            FROM policy_v2_refinement_pairs AS pair
            WHERE pair.session_id = ? AND NOT EXISTS (
                SELECT 1 FROM policy_v2_refinement_choices AS choice
                WHERE choice.pair_id = pair.pair_id
            )
            ORDER BY pair.ordinal LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "pair_id": row["pair_id"],
            "ordinal": int(row["ordinal"]),
            "inference_id": row["inference_id"],
            "recipe_a": _decode(row["recipe_a_json"], {}),
            "recipe_b": _decode(row["recipe_b_json"], {}),
            "variant_schema_version": row["variant_schema_version"],
            "pair_fingerprint": row["pair_fingerprint"],
        }
    finally:
        connection.close()


def record_choice(
    *,
    db_path: str,
    session_id: str,
    pair_id: str,
    choice: str,
    idempotency_key: str,
    winner_keepable: bool | None = None,
) -> dict[str, Any]:
    if choice not in CHOICES:
        raise ValueError("invalid refinement choice")
    if choice not in {"prefer_a", "prefer_b"} and winner_keepable is not None:
        raise ValueError("winner_keepable is valid only for a preferred side")
    if not idempotency_key:
        raise ValueError("idempotency_key is required")
    connection = connect_policy_store(db_path)
    try:
        existing = connection.execute(
            "SELECT * FROM policy_v2_refinement_choices WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            existing_keepable = (
                None
                if existing["winner_keepable"] is None
                else bool(existing["winner_keepable"])
            )
            if (
                existing["pair_id"] != pair_id
                or existing["choice"] != choice
                or existing_keepable != winner_keepable
            ):
                raise ValueError("idempotency key belongs to a different choice")
            return {
                "choice_id": existing["choice_id"],
                "recorded": False,
                "choice": existing["choice"],
            }
        session = connection.execute(
            "SELECT * FROM policy_v2_refinement_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise LookupError("refinement session was not found")
        if session["state"] != "active":
            raise ValueError("refinement session is not active")
        _assert_session_generation(connection, session)
        pair = connection.execute(
            "SELECT 1 FROM policy_v2_refinement_pairs WHERE pair_id = ? AND session_id = ?",
            (pair_id, session_id),
        ).fetchone()
        if pair is None:
            raise LookupError("refinement pair was not found")
        choice_id = uuid4().hex
        now = _utc_now()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO policy_v2_refinement_choices (
                choice_id, pair_id, idempotency_key, choice,
                winner_keepable, explicit_user_action,
                evidence_schema_version, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, '{}', ?)
            """,
            (
                choice_id,
                pair_id,
                idempotency_key,
                choice,
                None if winner_keepable is None else int(winner_keepable),
                EVIDENCE_SCHEMA_VERSION,
                now,
            ),
        )
        connection.execute(
            "UPDATE policy_v2_refinement_sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        connection.commit()
        return {"choice_id": choice_id, "recorded": True, "choice": choice}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def finalize_session(*, db_path: str, session_id: str) -> dict[str, Any]:
    connection = connect_policy_store(db_path)
    try:
        session = connection.execute(
            "SELECT * FROM policy_v2_refinement_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise LookupError("refinement session was not found")
        now = _utc_now()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE policy_v2_refinement_sessions
            SET state = 'finalized', updated_at = ?, finalized_at = COALESCE(finalized_at, ?)
            WHERE session_id = ?
            """,
            (now, now, session_id),
        )
        connection.commit()
        rows = connection.execute(
            """
            SELECT latest.choice, COUNT(*) AS count
            FROM policy_v2_refinement_pairs AS pair
            JOIN policy_v2_refinement_choices AS latest
              ON latest.choice_sequence = (
                  SELECT MAX(choice_sequence)
                  FROM policy_v2_refinement_choices
                  WHERE pair_id = pair.pair_id
              )
            WHERE pair.session_id = ?
            GROUP BY latest.choice
            """,
            (session_id,),
        ).fetchall()
        counts = {choice: 0 for choice in sorted(CHOICES)}
        counts.update({row["choice"]: int(row["count"]) for row in rows})
        summary = get_session(db_path=db_path, session_id=session_id)
        return {**(summary or {}), "choice_counts": counts}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
