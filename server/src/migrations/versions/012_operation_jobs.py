"""Persist catalog-local operation and per-item terminal state."""


def upgrade(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS operation_jobs (
            job_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            state TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            request_fingerprint TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            details_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            created_at REAL NOT NULL,
            started_at REAL,
            completed_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_operation_jobs_state_priority
            ON operation_jobs(state, priority DESC, created_at ASC);

        CREATE INDEX IF NOT EXISTS idx_operation_jobs_fingerprint
            ON operation_jobs(kind, request_fingerprint, state);

        CREATE TABLE IF NOT EXISTS operation_job_items (
            job_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            state TEXT NOT NULL,
            request_fingerprint TEXT,
            result_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            updated_at REAL NOT NULL,
            PRIMARY KEY (job_id, item_id),
            FOREIGN KEY (job_id) REFERENCES operation_jobs(job_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_operation_job_items_state
            ON operation_job_items(job_id, state, updated_at);
        """
    )
