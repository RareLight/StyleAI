import sqlite3


def upgrade(conn: sqlite3.Connection):
    """Persist catalog-local recommendation review sessions and labels."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS policy_v2_recommendation_reviews (
            review_id               TEXT PRIMARY KEY,
            generation_id           TEXT NOT NULL,
            policy_id               TEXT NOT NULL,
            policy_index            INTEGER NOT NULL CHECK (policy_index >= 0),
            hard_partition_key      TEXT NOT NULL,
            target_count            INTEGER NOT NULL CHECK (target_count > 0),
            existing_photo_ids_json TEXT NOT NULL DEFAULT '[]',
            algorithm_version       TEXT NOT NULL,
            feature_schema_version  TEXT NOT NULL,
            recommendation_version  TEXT NOT NULL,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_policy_v2_reviews_policy
            ON policy_v2_recommendation_reviews(policy_id, created_at);

        CREATE TABLE IF NOT EXISTS policy_v2_recommendation_candidates (
            review_id               TEXT NOT NULL,
            photo_id                TEXT NOT NULL,
            responsibilities_json   TEXT NOT NULL,
            assignment_entropy      REAL NOT NULL,
            coverage_gain           REAL NOT NULL,
            hard_partition_key      TEXT NOT NULL,
            source_ambiguous        INTEGER NOT NULL DEFAULT 0
                                    CHECK (source_ambiguous IN (0, 1)),
            metadata_json           TEXT NOT NULL DEFAULT '{}',
            recommended_rank        INTEGER,
            policy_match            INTEGER
                                    CHECK (policy_match IN (0, 1) OR policy_match IS NULL),
            useful                  INTEGER
                                    CHECK (useful IN (0, 1) OR useful IS NULL),
            reviewed_at             TEXT,
            PRIMARY KEY (review_id, photo_id),
            FOREIGN KEY (review_id)
                REFERENCES policy_v2_recommendation_reviews(review_id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_policy_v2_candidates_labelled
            ON policy_v2_recommendation_candidates(review_id, policy_match, useful);
    """)
