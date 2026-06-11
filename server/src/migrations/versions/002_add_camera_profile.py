import sqlite3


def upgrade(conn: sqlite3.Connection):
    """
    Migration: Add `camera_profile` to `styles` table.
    """
    # Check if the column exists first, in case the database was created
    # with the ad-hoc schemas from before migrations existed.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(styles)")]
    if "camera_profile" not in cols:
        conn.execute("ALTER TABLE styles ADD COLUMN camera_profile TEXT")
