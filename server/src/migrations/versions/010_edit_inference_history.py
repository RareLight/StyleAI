import sqlite3


def upgrade(conn: sqlite3.Connection):
    """Create immutable edit inferences and their append-only event stream."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS policy_v2_edit_inferences (
            inference_id            TEXT PRIMARY KEY,
            photo_id                TEXT NOT NULL,
            generation_id           TEXT,
            policy_id               TEXT,
            hard_partition_key      TEXT NOT NULL DEFAULT 'default',
            engine                  TEXT NOT NULL,
            algorithm_version       TEXT NOT NULL,
            feature_schema_version  TEXT NOT NULL,
            target_schema_version   TEXT NOT NULL,
            inference_schema_version TEXT NOT NULL,
            confidence              REAL,
            entropy                 REAL,
            strength                REAL NOT NULL,
            summary                 TEXT NOT NULL DEFAULT '',
            modeled_keys_json       TEXT NOT NULL,
            pre_edit_state_json     TEXT NOT NULL,
            pre_edit_fingerprint    TEXT NOT NULL,
            target_state_json       TEXT NOT NULL,
            target_fingerprint      TEXT NOT NULL,
            created_at              TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_policy_v2_edit_inferences_photo
            ON policy_v2_edit_inferences(photo_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_policy_v2_edit_inferences_policy
            ON policy_v2_edit_inferences(policy_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS policy_v2_edit_events (
            event_sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id                TEXT NOT NULL UNIQUE,
            inference_id            TEXT NOT NULL,
            idempotency_key         TEXT NOT NULL UNIQUE,
            event_kind              TEXT NOT NULL,
            event_schema_version    TEXT NOT NULL,
            explicit_user_action    INTEGER NOT NULL DEFAULT 0
                                    CHECK (explicit_user_action IN (0, 1)),
            observed_state_json     TEXT,
            observed_fingerprint    TEXT,
            details_json            TEXT NOT NULL DEFAULT '{}',
            created_at              TEXT NOT NULL,
            FOREIGN KEY (inference_id)
                REFERENCES policy_v2_edit_inferences(inference_id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_policy_v2_edit_events_inference
            ON policy_v2_edit_events(inference_id, event_sequence DESC);
        CREATE INDEX IF NOT EXISTS idx_policy_v2_edit_events_kind
            ON policy_v2_edit_events(event_kind, event_sequence DESC);
    """)
