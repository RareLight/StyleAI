import sqlite3
import logging

logger = logging.getLogger("styleai-migrations")


def upgrade(conn: sqlite3.Connection):
    """
    Adds user_style_name to styles table to support renaming without breaking logic.
    """
    logger.info("Running migration: 003_add_user_style_name")

    # Check if the column already exists just in case
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(styles)")
    columns = [row[1] for row in cursor.fetchall()]

    if "user_style_name" not in columns:
        logger.info("Adding user_style_name column to styles table")
        conn.execute("ALTER TABLE styles ADD COLUMN user_style_name TEXT;")
    else:
        logger.info("Column user_style_name already exists, skipping.")
