"""Add immutable operation-scoped burst-reuse provenance."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(policy_v2_edit_inferences)")
    }
    additions = (
        ("operation_job_id", "TEXT"),
        ("absolute_target_json", "TEXT"),
        ("grouping_schema_version", "TEXT"),
        ("reuse_policy_version", "TEXT"),
        ("threshold_version", "TEXT"),
        ("burst_group_id", "TEXT"),
        ("representative_photo_id", "TEXT"),
        ("reuse_tier", "TEXT NOT NULL DEFAULT 'independent'"),
        ("capture_delta_seconds", "REAL"),
        ("cosine_distance", "REAL"),
        ("source_metric_deltas_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("policy_agreement_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("burst_fallback_reason", "TEXT"),
    )
    for name, data_type in additions:
        if name not in columns:
            conn.execute(
                f"ALTER TABLE policy_v2_edit_inferences ADD COLUMN {name} {data_type}"
            )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_policy_v2_edit_inferences_burst
        ON policy_v2_edit_inferences(burst_group_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_policy_v2_edit_inferences_operation
        ON policy_v2_edit_inferences(operation_job_id, photo_id)
        """
    )
