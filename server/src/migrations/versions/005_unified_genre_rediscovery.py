import sqlite3


def upgrade(conn: sqlite3.Connection):
    """Create grouping_rule_state table and flag automatic post-migration re-discovery."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS grouping_rule_state (
            rule_key TEXT PRIMARY KEY,
            rule_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        INSERT OR REPLACE INTO grouping_rule_state (rule_key, rule_value, updated_at)
        VALUES ('GROUPING_RULE_VERSION', '2', datetime('now'))
    """)
    conn.execute("""
        INSERT OR REPLACE INTO grouping_rule_state (rule_key, rule_value, updated_at)
        VALUES ('NEEDS_REDISCOVERY', '1', datetime('now'))
    """)
