import sqlite3


def upgrade(conn: sqlite3.Connection):
    """
    Initial SQLite schema for StyleAI.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS styles (
            style_id            TEXT PRIMARY KEY,
            style_name          TEXT NOT NULL,
            camera_make         TEXT,
            camera_model        TEXT,
            genre               TEXT NOT NULL,
            subgenre            TEXT,
            description         TEXT,
            example_count       INTEGER DEFAULT 0,
            mean_exposure_dna   TEXT,           -- JSON
            scene_distribution  TEXT,           -- JSON
            develop_variance    TEXT,           -- JSON
            confidence_threshold REAL DEFAULT 0.45,
            created_at          TEXT,
            updated_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS style_examples (
            style_id            TEXT NOT NULL,
            photo_id            TEXT NOT NULL,
            PRIMARY KEY (style_id, photo_id),
            FOREIGN KEY (style_id) REFERENCES styles(style_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS style_migration_log (
            migration_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            migrated_at         TEXT,
            source_examples     INTEGER,
            styles_created      INTEGER,
            status              TEXT
        );
    """)
