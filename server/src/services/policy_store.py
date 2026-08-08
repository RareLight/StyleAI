"""Transactional persistence for editing-policy v2 generations."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from core.migrations import run_migrations


V2_TABLES = (
    "policy_v2_generations",
    "policy_v2_examples",
    "policy_v2_models",
    "policy_v2_memberships",
    "policy_v2_descriptors",
    "policy_v2_coverage",
    "policy_v2_validation_results",
    "policy_v2_custom_names",
    "policy_v2_recommendation_reviews",
    "policy_v2_recommendation_candidates",
    "policy_v2_edit_inferences",
    "policy_v2_edit_events",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect_policy_store(db_path: str) -> sqlite3.Connection:
    """Open the catalog-local policy store and ensure its schema exists."""
    if not db_path:
        raise ValueError("db_path is required")
    run_migrations(db_path)
    connection = sqlite3.connect(
        os.path.join(db_path, "styles.sqlite"), check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


@contextmanager
def _immediate_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def create_generation(
    connection: sqlite3.Connection,
    *,
    algorithm_version: str,
    feature_schema_version: str,
    target_schema_version: str,
    generation_id: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> str:
    """Create an inactive generation that can be built and validated safely."""
    if not algorithm_version or not feature_schema_version or not target_schema_version:
        raise ValueError("algorithm, feature, and target versions are required")
    new_id = generation_id or uuid4().hex
    with _immediate_transaction(connection):
        connection.execute(
            """
            INSERT INTO policy_v2_generations (
                generation_id, status, algorithm_version,
                feature_schema_version, target_schema_version,
                metrics_json, created_at
            ) VALUES (?, 'building', ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                algorithm_version,
                feature_schema_version,
                target_schema_version,
                json.dumps(metrics or {}, sort_keys=True),
                _utc_now(),
            ),
        )
    return new_id


def add_policy_model(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    policy_id: str,
    hard_partition_key: str,
    expert_index: int,
    estimator_type: str,
    artifact_name: str,
    preprocessing: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    effective_sample_count: float = 0.0,
) -> None:
    """Register one validated artifact candidate in a building generation."""
    if not generation_id or not policy_id or not estimator_type or not artifact_name:
        raise ValueError("generation, policy, estimator, and artifact are required")
    if os.path.isabs(artifact_name) or ".." in artifact_name.split("/"):
        raise ValueError("artifact_name must be a safe relative path")
    generation = connection.execute(
        "SELECT status FROM policy_v2_generations WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()
    if not generation or generation["status"] != "building":
        raise ValueError("models may only be added to a building generation")
    with _immediate_transaction(connection):
        connection.execute(
            """
            INSERT INTO policy_v2_models (
                generation_id, policy_id, hard_partition_key, expert_index,
                estimator_type, artifact_name, preprocessing_json,
                validation_json, effective_sample_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generation_id,
                policy_id,
                hard_partition_key or "default",
                int(expert_index),
                estimator_type,
                artifact_name,
                json.dumps(preprocessing or {}, sort_keys=True),
                json.dumps(validation or {}, sort_keys=True),
                float(effective_sample_count),
                _utc_now(),
            ),
        )


def activate_generation(connection: sqlite3.Connection, generation_id: str) -> None:
    """Atomically retire the old generation and activate a complete candidate."""
    with _immediate_transaction(connection):
        generation = connection.execute(
            "SELECT status FROM policy_v2_generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        if not generation or generation["status"] != "building":
            raise ValueError("only a building generation may be activated")
        model_count = connection.execute(
            "SELECT COUNT(*) FROM policy_v2_models WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()[0]
        if model_count == 0:
            raise ValueError("cannot activate a generation without models")
        connection.execute(
            "UPDATE policy_v2_generations SET status = 'retired' "
            "WHERE status = 'active'"
        )
        connection.execute(
            """
            UPDATE policy_v2_generations
            SET status = 'active', activated_at = ?
            WHERE generation_id = ?
            """,
            (_utc_now(), generation_id),
        )


def get_active_generation(
    connection: sqlite3.Connection,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM policy_v2_generations WHERE status = 'active'"
    ).fetchone()
    return dict(row) if row else None


def recover_incomplete_generations(connection: sqlite3.Connection) -> int:
    """Mark interrupted, never-activated builds failed without touching active data."""
    with _immediate_transaction(connection):
        cursor = connection.execute(
            "UPDATE policy_v2_generations SET status = 'failed' "
            "WHERE status = 'building'"
        )
    return max(0, int(cursor.rowcount))


def fail_generation(connection: sqlite3.Connection, generation_id: str) -> None:
    """Mark a non-active generation failed after a controlled build error."""
    with _immediate_transaction(connection):
        connection.execute(
            "UPDATE policy_v2_generations SET status = 'failed' "
            "WHERE generation_id = ? AND status = 'building'",
            (generation_id,),
        )


def prune_inactive_generations(
    connection: sqlite3.Connection,
    *,
    retain_retired: int = 0,
) -> list[str]:
    """Bound derived history while preserving the complete active generation."""
    if retain_retired < 0:
        raise ValueError("retain_retired must be non-negative")
    rows = connection.execute(
        """
        SELECT generation_id, status
        FROM policy_v2_generations
        WHERE status != 'active'
        ORDER BY
            CASE WHEN status = 'retired' THEN 0 ELSE 1 END,
            COALESCE(activated_at, created_at) DESC,
            generation_id DESC
        """
    ).fetchall()
    retained = 0
    delete_ids: list[str] = []
    for row in rows:
        if row["status"] == "retired" and retained < retain_retired:
            retained += 1
        else:
            delete_ids.append(str(row["generation_id"]))
    if not delete_ids:
        return []
    placeholders = ",".join("?" for _ in delete_ids)
    with _immediate_transaction(connection):
        connection.execute(
            f"DELETE FROM policy_v2_generations "
            f"WHERE generation_id IN ({placeholders})",
            delete_ids,
        )
        connection.execute(
            """
            DELETE FROM policy_v2_examples
            WHERE NOT EXISTS (
                SELECT 1
                FROM policy_v2_memberships AS memberships
                WHERE memberships.photo_id = policy_v2_examples.photo_id
            )
            """
        )
    return delete_ids


def upsert_policy_examples(
    connection: sqlite3.Connection,
    examples: list[dict[str, Any]],
) -> None:
    """Atomically store versioned source/target rows used by policy fitting."""
    now = _utc_now()
    with _immediate_transaction(connection):
        connection.executemany(
            """
            INSERT INTO policy_v2_examples (
                photo_id, source_provenance, feature_schema_version,
                source_features_json, feature_mask_json,
                target_schema_version, target_values_json,
                burst_group_id, sample_weight, metadata_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(photo_id) DO UPDATE SET
                source_provenance = excluded.source_provenance,
                feature_schema_version = excluded.feature_schema_version,
                source_features_json = excluded.source_features_json,
                feature_mask_json = excluded.feature_mask_json,
                target_schema_version = excluded.target_schema_version,
                target_values_json = excluded.target_values_json,
                burst_group_id = excluded.burst_group_id,
                sample_weight = excluded.sample_weight,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            [
                (
                    str(item["photo_id"]),
                    str(item["source_provenance"]),
                    str(item["feature_schema_version"]),
                    json.dumps(item["source_features"], separators=(",", ":")),
                    json.dumps(item.get("feature_mask", []), separators=(",", ":")),
                    str(item["target_schema_version"]),
                    json.dumps(item["target_values"], separators=(",", ":")),
                    item.get("burst_group_id"),
                    float(item.get("sample_weight", 1.0)),
                    json.dumps(item.get("metadata", {}), sort_keys=True),
                    now,
                    now,
                )
                for item in examples
            ],
        )


def replace_policy_memberships(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    memberships: list[dict[str, Any]],
) -> None:
    """Atomically replace soft assignments for a generation."""
    with _immediate_transaction(connection):
        connection.execute(
            "DELETE FROM policy_v2_memberships WHERE generation_id = ?",
            (generation_id,),
        )
        connection.executemany(
            """
            INSERT INTO policy_v2_memberships (
                generation_id, policy_id, photo_id, responsibility,
                outlier_score, assignment_entropy
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    generation_id,
                    str(item["policy_id"]),
                    str(item["photo_id"]),
                    float(item["responsibility"]),
                    (
                        float(item["outlier_score"])
                        if item.get("outlier_score") is not None
                        else None
                    ),
                    (
                        float(item["assignment_entropy"])
                        if item.get("assignment_entropy") is not None
                        else None
                    ),
                )
                for item in memberships
            ],
        )


def replace_validation_results(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    results: list[dict[str, Any]],
) -> None:
    with _immediate_transaction(connection):
        connection.execute(
            "DELETE FROM policy_v2_validation_results WHERE generation_id = ?",
            (generation_id,),
        )
        connection.executemany(
            """
            INSERT INTO policy_v2_validation_results (
                generation_id, validation_scope, metric_key,
                metric_value, details_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    generation_id,
                    str(item["validation_scope"]),
                    str(item["metric_key"]),
                    (
                        float(item["metric_value"])
                        if item.get("metric_value") is not None
                        else None
                    ),
                    json.dumps(item.get("details", {}), sort_keys=True),
                )
                for item in results
            ],
        )


def list_active_policy_models(
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT m.*, g.feature_schema_version, g.target_schema_version
        FROM policy_v2_models AS m
        JOIN policy_v2_generations AS g
          ON g.generation_id = m.generation_id
        WHERE g.status = 'active'
        ORDER BY m.hard_partition_key, m.expert_index
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_policy_custom_names(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["policy_id"]): str(row["custom_name"])
        for row in connection.execute(
            "SELECT policy_id, custom_name FROM policy_v2_custom_names"
        ).fetchall()
    }


def rename_policy(
    connection: sqlite3.Connection,
    *,
    policy_id: str,
    custom_name: str,
) -> bool:
    name = str(custom_name).strip()
    if not policy_id or not name:
        raise ValueError("policy_id and custom_name are required")
    active = connection.execute(
        """
        SELECT 1
        FROM policy_v2_models AS m
        JOIN policy_v2_generations AS g
          ON g.generation_id = m.generation_id
        WHERE g.status = 'active' AND m.policy_id = ?
        """,
        (policy_id,),
    ).fetchone()
    if not active:
        return False
    with _immediate_transaction(connection):
        connection.execute(
            """
            INSERT INTO policy_v2_custom_names (policy_id, custom_name, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                custom_name = excluded.custom_name,
                updated_at = excluded.updated_at
            """,
            (policy_id, name, _utc_now()),
        )
    return True


def reset_policy_v2(connection: sqlite3.Connection) -> None:
    """Delete derived policy state while preserving durable recommendation reviews."""
    with _immediate_transaction(connection):
        connection.execute("DELETE FROM policy_v2_examples")
        connection.execute("DELETE FROM policy_v2_generations")
        connection.execute("DELETE FROM policy_v2_custom_names")


def upsert_recommendation_review(
    connection: sqlite3.Connection,
    *,
    review_id: str,
    generation_id: str,
    policy_id: str,
    policy_index: int,
    hard_partition_key: str,
    target_count: int,
    existing_photo_ids: list[str],
    algorithm_version: str,
    feature_schema_version: str,
    recommendation_version: str,
    candidates: list[dict[str, Any]],
) -> None:
    """Store a replayable recommendation snapshot without duplicating embeddings."""
    if (
        not review_id
        or not generation_id
        or not policy_id
        or policy_index < 0
        or target_count <= 0
        or not candidates
    ):
        raise ValueError("recommendation review identity and candidates are required")
    now = _utc_now()
    with _immediate_transaction(connection):
        connection.execute(
            """
            INSERT INTO policy_v2_recommendation_reviews (
                review_id, generation_id, policy_id, policy_index,
                hard_partition_key, target_count, existing_photo_ids_json,
                algorithm_version, feature_schema_version,
                recommendation_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(review_id) DO UPDATE SET
                existing_photo_ids_json = excluded.existing_photo_ids_json,
                updated_at = excluded.updated_at
            """,
            (
                review_id,
                generation_id,
                policy_id,
                int(policy_index),
                hard_partition_key or "default",
                int(target_count),
                json.dumps(existing_photo_ids, separators=(",", ":")),
                algorithm_version,
                feature_schema_version,
                recommendation_version,
                now,
                now,
            ),
        )
        connection.executemany(
            """
            INSERT INTO policy_v2_recommendation_candidates (
                review_id, photo_id, responsibilities_json,
                assignment_entropy, coverage_gain, hard_partition_key,
                source_ambiguous, metadata_json, recommended_rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(review_id, photo_id) DO UPDATE SET
                responsibilities_json = excluded.responsibilities_json,
                assignment_entropy = excluded.assignment_entropy,
                coverage_gain = excluded.coverage_gain,
                hard_partition_key = excluded.hard_partition_key,
                source_ambiguous = excluded.source_ambiguous,
                metadata_json = excluded.metadata_json,
                recommended_rank = excluded.recommended_rank
            """,
            [
                (
                    review_id,
                    str(item["photo_id"]),
                    json.dumps(item["responsibilities"], separators=(",", ":")),
                    float(item["assignment_entropy"]),
                    float(item.get("coverage_gain", 0.0)),
                    str(
                        item.get("hard_partition_key")
                        or hard_partition_key
                        or "default"
                    ),
                    int(bool(item.get("source_ambiguous", False))),
                    json.dumps(
                        item.get("metadata", {}), separators=(",", ":"), sort_keys=True
                    ),
                    (
                        int(item["recommended_rank"])
                        if item.get("recommended_rank") is not None
                        else None
                    ),
                )
                for item in candidates
            ],
        )


def record_recommendation_feedback(
    connection: sqlite3.Connection,
    *,
    review_id: str,
    policy_id: str,
    labels: list[dict[str, Any]],
) -> dict[str, int]:
    """Atomically label candidates from one immutable recommendation snapshot."""
    if not review_id or not policy_id or not labels:
        raise ValueError("review_id, policy_id, and labels are required")
    review = connection.execute(
        """
        SELECT 1 FROM policy_v2_recommendation_reviews
        WHERE review_id = ? AND policy_id = ?
        """,
        (review_id, policy_id),
    ).fetchone()
    if not review:
        raise LookupError("recommendation review was not found for this policy")
    normalized: list[tuple[int | None, int | None, str, str]] = []
    seen: set[str] = set()
    for item in labels:
        photo_id = str(item.get("photo_id") or "").strip()
        if not photo_id or photo_id in seen:
            raise ValueError("feedback photo IDs must be non-empty and unique")
        seen.add(photo_id)
        policy_match = item.get("policy_match")
        useful = item.get("useful")
        if policy_match is not None and not isinstance(policy_match, bool):
            raise ValueError("policy_match must be true, false, or null")
        if useful is not None and not isinstance(useful, bool):
            raise ValueError("useful must be true, false, or null")
        if useful is True and policy_match is not True:
            raise ValueError("a useful candidate must also match the policy")
        normalized.append(
            (
                None if policy_match is None else int(policy_match),
                None if useful is None else int(useful),
                review_id,
                photo_id,
            )
        )
    placeholders = ",".join("?" for _ in seen)
    found = connection.execute(
        f"""
        SELECT COUNT(*) FROM policy_v2_recommendation_candidates
        WHERE review_id = ? AND photo_id IN ({placeholders})
        """,
        [review_id, *sorted(seen)],
    ).fetchone()[0]
    if found != len(seen):
        raise LookupError("one or more photos are not candidates in this review")
    now = _utc_now()
    with _immediate_transaction(connection):
        connection.executemany(
            """
            UPDATE policy_v2_recommendation_candidates
            SET policy_match = ?, useful = ?, reviewed_at = ?
            WHERE review_id = ? AND photo_id = ?
            """,
            [
                (policy_match, useful, now, stored_review_id, photo_id)
                for policy_match, useful, stored_review_id, photo_id in normalized
            ],
        )
        connection.execute(
            "UPDATE policy_v2_recommendation_reviews SET updated_at = ? "
            "WHERE review_id = ?",
            (now, review_id),
        )
    return {"updated": len(normalized), "requested": len(labels)}


def list_recommendation_reviews(
    connection: sqlite3.Connection,
    *,
    labelled_only: bool = True,
) -> list[dict[str, Any]]:
    """Return review snapshots and candidates for local analysis/export."""
    where = (
        "WHERE EXISTS (SELECT 1 FROM policy_v2_recommendation_candidates AS c "
        "WHERE c.review_id = r.review_id "
        "AND (c.policy_match IS NOT NULL OR c.useful IS NOT NULL))"
        if labelled_only
        else ""
    )
    reviews = connection.execute(
        f"""
        SELECT r.* FROM policy_v2_recommendation_reviews AS r
        {where}
        ORDER BY r.created_at, r.review_id
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in reviews:
        review = dict(row)
        review["existing_photo_ids"] = json.loads(review.pop("existing_photo_ids_json"))
        candidates = connection.execute(
            """
            SELECT * FROM policy_v2_recommendation_candidates
            WHERE review_id = ?
            ORDER BY
                CASE WHEN recommended_rank IS NULL THEN 1 ELSE 0 END,
                recommended_rank, photo_id
            """,
            (review["review_id"],),
        ).fetchall()
        review["candidates"] = []
        for candidate_row in candidates:
            candidate = dict(candidate_row)
            candidate.pop("review_id")
            candidate["responsibilities"] = json.loads(
                candidate.pop("responsibilities_json")
            )
            candidate["metadata"] = json.loads(candidate.pop("metadata_json"))
            candidate["source_ambiguous"] = bool(candidate["source_ambiguous"])
            candidate["policy_match"] = (
                None
                if candidate["policy_match"] is None
                else bool(candidate["policy_match"])
            )
            candidate["useful"] = (
                None if candidate["useful"] is None else bool(candidate["useful"])
            )
            review["candidates"].append(candidate)
        result.append(review)
    return result


def policy_store_stats(connection: sqlite3.Connection) -> dict[str, int]:
    """Return compact counts suitable for reset and integrity assertions."""
    return {
        "examples": connection.execute(
            "SELECT COUNT(*) FROM policy_v2_examples"
        ).fetchone()[0],
        "generations": connection.execute(
            "SELECT COUNT(*) FROM policy_v2_generations"
        ).fetchone()[0],
        "models": connection.execute(
            "SELECT COUNT(*) FROM policy_v2_models"
        ).fetchone()[0],
        "memberships": connection.execute(
            "SELECT COUNT(*) FROM policy_v2_memberships"
        ).fetchone()[0],
    }


def replace_policy_descriptors(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    policy_id: str,
    descriptors: list[dict[str, Any]],
) -> None:
    """Atomically replace derived, reproducible descriptors for one policy."""
    with _immediate_transaction(connection):
        connection.execute(
            "DELETE FROM policy_v2_descriptors "
            "WHERE generation_id = ? AND policy_id = ?",
            (generation_id, policy_id),
        )
        connection.executemany(
            """
            INSERT INTO policy_v2_descriptors (
                generation_id, policy_id, descriptor_kind,
                descriptor, score, provenance
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    generation_id,
                    policy_id,
                    str(item["descriptor_kind"]),
                    str(item["descriptor"]),
                    float(item["score"]),
                    str(item["provenance"]),
                )
                for item in descriptors
            ],
        )


def replace_policy_coverage(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    policy_id: str,
    coverage: list[dict[str, Any]],
) -> None:
    """Atomically replace empirical coverage buckets for one policy."""
    with _immediate_transaction(connection):
        connection.execute(
            "DELETE FROM policy_v2_coverage WHERE generation_id = ? AND policy_id = ?",
            (generation_id, policy_id),
        )
        connection.executemany(
            """
            INSERT INTO policy_v2_coverage (
                generation_id, policy_id, dimension_key,
                bucket_key, effective_count, coverage_score
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    generation_id,
                    policy_id,
                    str(item["dimension_key"]),
                    str(item["bucket_key"]),
                    float(item["effective_count"]),
                    float(item["coverage_score"]),
                )
                for item in coverage
            ],
        )
