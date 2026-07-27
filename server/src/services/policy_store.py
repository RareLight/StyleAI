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


def reset_policy_v2(connection: sqlite3.Connection) -> None:
    """Delete only v2 policy state, preserving search and legacy tables."""
    with _immediate_transaction(connection):
        connection.execute("DELETE FROM policy_v2_examples")
        connection.execute("DELETE FROM policy_v2_generations")


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
