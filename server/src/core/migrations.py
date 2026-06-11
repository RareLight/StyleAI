import os
import sqlite3
import importlib.util

from config import logger


def run_migrations(db_path: str):
    """
    Run all pending migrations for the StyleAI database.
    This includes both SQLite schema migrations and ChromaDB metadata migrations.
    """
    if not db_path:
        raise ValueError("db_path must be provided to run migrations.")

    sqlite_path = os.path.join(db_path, "styles.sqlite")

    # 1. Initialize SQLite schema_versions table
    # We must do this even if the file doesn't exist yet, as we need to track versions.
    os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
    conn = sqlite3.connect(sqlite_path)

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_versions (
                version_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
        """)
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to initialize schema_versions table: {e}")
        conn.close()
        raise

    # 2. Find all migration scripts in server/src/migrations/versions
    migrations_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "migrations", "versions"
    )
    if not os.path.exists(migrations_dir):
        logger.warning(f"Migrations directory not found at {migrations_dir}")
        conn.close()
        return

    migration_files = sorted(
        [
            f
            for f in os.listdir(migrations_dir)
            if f.endswith(".py") and f != "__init__.py"
        ]
    )

    # 3. Get applied migrations
    applied_versions = {
        row[0]
        for row in conn.execute("SELECT version_id FROM schema_versions").fetchall()
    }

    # 4. Apply pending migrations
    for filename in migration_files:
        version_id = filename.replace(".py", "")
        if version_id in applied_versions:
            continue

        logger.info(f"Applying migration: {version_id}")
        filepath = os.path.join(migrations_dir, filename)

        # Load the module dynamically
        spec = importlib.util.spec_from_file_location(
            f"migration_{version_id}", filepath
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load migration module {filename}")

        migration_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration_module)

        # Run `upgrade(conn)`
        if hasattr(migration_module, "upgrade"):
            try:
                migration_module.upgrade(conn)
                conn.execute(
                    "INSERT INTO schema_versions (version_id, applied_at) VALUES (?, datetime('now'))",
                    (version_id,),
                )
                conn.commit()
                logger.info(f"Successfully applied migration: {version_id}")
            except Exception as e:
                logger.error(f"Migration {version_id} failed: {e}")
                conn.rollback()
                conn.close()
                raise
        else:
            logger.warning(
                f"Migration {version_id} has no 'upgrade' function. Skipping."
            )

    conn.close()
    logger.info("All migrations applied successfully.")
