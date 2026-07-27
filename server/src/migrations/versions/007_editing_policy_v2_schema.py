import sqlite3


def upgrade(conn: sqlite3.Connection):
    """Create the clean editing-policy v2 schema alongside legacy styles."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS policy_v2_generations (
            generation_id          TEXT PRIMARY KEY,
            status                 TEXT NOT NULL
                                   CHECK (status IN ('building', 'active', 'retired', 'failed')),
            algorithm_version      TEXT NOT NULL,
            feature_schema_version TEXT NOT NULL,
            target_schema_version  TEXT NOT NULL,
            metrics_json           TEXT NOT NULL DEFAULT '{}',
            created_at             TEXT NOT NULL,
            activated_at           TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_v2_one_active_generation
            ON policy_v2_generations(status)
            WHERE status = 'active';

        CREATE TABLE IF NOT EXISTS policy_v2_examples (
            photo_id               TEXT PRIMARY KEY,
            source_provenance       TEXT NOT NULL,
            feature_schema_version  TEXT NOT NULL,
            source_features_json    TEXT NOT NULL,
            feature_mask_json       TEXT NOT NULL DEFAULT '{}',
            target_schema_version   TEXT NOT NULL,
            target_values_json      TEXT NOT NULL,
            burst_group_id          TEXT,
            sample_weight           REAL NOT NULL DEFAULT 1.0
                                    CHECK (sample_weight > 0),
            metadata_json           TEXT NOT NULL DEFAULT '{}',
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS policy_v2_models (
            generation_id          TEXT NOT NULL,
            policy_id              TEXT NOT NULL,
            hard_partition_key     TEXT NOT NULL DEFAULT 'default',
            expert_index           INTEGER NOT NULL CHECK (expert_index >= 0),
            estimator_type         TEXT NOT NULL,
            artifact_name          TEXT NOT NULL,
            preprocessing_json     TEXT NOT NULL DEFAULT '{}',
            validation_json        TEXT NOT NULL DEFAULT '{}',
            effective_sample_count REAL NOT NULL DEFAULT 0
                                   CHECK (effective_sample_count >= 0),
            created_at             TEXT NOT NULL,
            PRIMARY KEY (generation_id, policy_id),
            UNIQUE (generation_id, hard_partition_key, expert_index),
            FOREIGN KEY (generation_id)
                REFERENCES policy_v2_generations(generation_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS policy_v2_memberships (
            generation_id          TEXT NOT NULL,
            policy_id              TEXT NOT NULL,
            photo_id               TEXT NOT NULL,
            responsibility         REAL NOT NULL
                                   CHECK (responsibility >= 0 AND responsibility <= 1),
            outlier_score          REAL,
            assignment_entropy     REAL,
            PRIMARY KEY (generation_id, policy_id, photo_id),
            FOREIGN KEY (generation_id, policy_id)
                REFERENCES policy_v2_models(generation_id, policy_id) ON DELETE CASCADE,
            FOREIGN KEY (photo_id)
                REFERENCES policy_v2_examples(photo_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_policy_v2_memberships_photo
            ON policy_v2_memberships(generation_id, photo_id);

        CREATE TABLE IF NOT EXISTS policy_v2_descriptors (
            generation_id          TEXT NOT NULL,
            policy_id              TEXT NOT NULL,
            descriptor_kind        TEXT NOT NULL,
            descriptor             TEXT NOT NULL,
            score                  REAL NOT NULL,
            provenance             TEXT NOT NULL,
            PRIMARY KEY (
                generation_id, policy_id, descriptor_kind, descriptor, provenance
            ),
            FOREIGN KEY (generation_id, policy_id)
                REFERENCES policy_v2_models(generation_id, policy_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS policy_v2_coverage (
            generation_id          TEXT NOT NULL,
            policy_id              TEXT NOT NULL,
            dimension_key          TEXT NOT NULL,
            bucket_key             TEXT NOT NULL,
            effective_count        REAL NOT NULL DEFAULT 0,
            coverage_score         REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (
                generation_id, policy_id, dimension_key, bucket_key
            ),
            FOREIGN KEY (generation_id, policy_id)
                REFERENCES policy_v2_models(generation_id, policy_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS policy_v2_validation_results (
            generation_id          TEXT NOT NULL,
            validation_scope       TEXT NOT NULL,
            metric_key             TEXT NOT NULL,
            metric_value           REAL,
            details_json           TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (generation_id, validation_scope, metric_key),
            FOREIGN KEY (generation_id)
                REFERENCES policy_v2_generations(generation_id) ON DELETE CASCADE
        );
    """)
