"""Add append-only blinded style-refinement evidence and candidate lineage."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS policy_v2_refinement_sessions (
            session_id              TEXT PRIMARY KEY,
            mode                    TEXT NOT NULL
                                    CHECK (mode IN ('quick', 'correction_validation')),
            base_generation_id      TEXT NOT NULL,
            state                   TEXT NOT NULL
                                    CHECK (state IN ('active', 'finalized', 'canceled')),
            schema_version          TEXT NOT NULL,
            requested_count         INTEGER NOT NULL CHECK (requested_count > 0),
            details_json            TEXT NOT NULL DEFAULT '{}',
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL,
            finalized_at            TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_policy_v2_refinement_sessions_state
            ON policy_v2_refinement_sessions(state, updated_at DESC);

        CREATE TABLE IF NOT EXISTS policy_v2_refinement_pairs (
            pair_id                 TEXT PRIMARY KEY,
            session_id              TEXT NOT NULL,
            ordinal                 INTEGER NOT NULL CHECK (ordinal > 0),
            inference_id            TEXT NOT NULL,
            photo_id                TEXT NOT NULL,
            policy_id               TEXT,
            hard_partition_key      TEXT NOT NULL DEFAULT 'default',
            variant_schema_version  TEXT NOT NULL,
            generation_method       TEXT NOT NULL,
            recipe_a_json           TEXT NOT NULL,
            recipe_b_json           TEXT NOT NULL,
            identity_a              TEXT NOT NULL,
            identity_b              TEXT NOT NULL,
            side_assignment_json    TEXT NOT NULL,
            pair_fingerprint        TEXT NOT NULL UNIQUE,
            selection_reason        TEXT NOT NULL,
            created_at              TEXT NOT NULL,
            UNIQUE (session_id, ordinal),
            UNIQUE (session_id, inference_id),
            FOREIGN KEY (session_id)
                REFERENCES policy_v2_refinement_sessions(session_id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_policy_v2_refinement_pairs_session
            ON policy_v2_refinement_pairs(session_id, ordinal);

        CREATE TABLE IF NOT EXISTS policy_v2_refinement_choices (
            choice_sequence         INTEGER PRIMARY KEY AUTOINCREMENT,
            choice_id               TEXT NOT NULL UNIQUE,
            pair_id                 TEXT NOT NULL,
            idempotency_key         TEXT NOT NULL UNIQUE,
            choice                  TEXT NOT NULL
                                    CHECK (choice IN (
                                        'prefer_a', 'prefer_b', 'equivalent',
                                        'neither', 'skip'
                                    )),
            winner_keepable         INTEGER
                                    CHECK (winner_keepable IN (0, 1)),
            explicit_user_action    INTEGER NOT NULL DEFAULT 1
                                    CHECK (explicit_user_action IN (0, 1)),
            evidence_schema_version TEXT NOT NULL,
            details_json            TEXT NOT NULL DEFAULT '{}',
            created_at              TEXT NOT NULL,
            FOREIGN KEY (pair_id)
                REFERENCES policy_v2_refinement_pairs(pair_id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_policy_v2_refinement_choices_pair
            ON policy_v2_refinement_choices(pair_id, choice_sequence DESC);

        CREATE TABLE IF NOT EXISTS policy_v2_refinement_candidates (
            candidate_id            TEXT PRIMARY KEY,
            base_generation_id      TEXT NOT NULL,
            candidate_generation_id TEXT,
            state                   TEXT NOT NULL
                                    CHECK (state IN (
                                        'building', 'evaluating', 'eligible',
                                        'ineligible', 'failed', 'activated', 'retired'
                                    )),
            manifest_json           TEXT NOT NULL DEFAULT '{}',
            evaluation_report_path  TEXT,
            evaluation_fingerprint  TEXT,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS policy_v2_refinement_activation_events (
            event_sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id                TEXT NOT NULL UNIQUE,
            candidate_id            TEXT,
            action                  TEXT NOT NULL
                                    CHECK (action IN ('activate', 'rollback')),
            from_generation_id      TEXT NOT NULL,
            to_generation_id        TEXT NOT NULL,
            idempotency_key         TEXT NOT NULL UNIQUE,
            details_json            TEXT NOT NULL DEFAULT '{}',
            created_at              TEXT NOT NULL,
            FOREIGN KEY (candidate_id)
                REFERENCES policy_v2_refinement_candidates(candidate_id)
                ON DELETE SET NULL
        );
        """
    )
