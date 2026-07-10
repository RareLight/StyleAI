import sqlite3


def upgrade(conn: sqlite3.Connection):
    """
    Add semantic_genre_cache table to persist SentenceTransformer mappings.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS semantic_genre_cache (
            keyword TEXT PRIMARY KEY,
            genre TEXT NOT NULL,
            created_at TEXT
        );
    """)
