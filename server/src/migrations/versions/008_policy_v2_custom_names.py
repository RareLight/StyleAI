import sqlite3


def upgrade(conn: sqlite3.Connection):
    """Persist user-facing policy names independently of model generations."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS policy_v2_custom_names (
            policy_id   TEXT PRIMARY KEY,
            custom_name TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
