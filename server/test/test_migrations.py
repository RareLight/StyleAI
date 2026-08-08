from core import migrations
import sqlite3


def test_migrations_run_once_per_catalog_path(mocker, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    run = mocker.patch.object(migrations, "_run_migrations_uncached")
    migrations._migrated_paths.discard(str(tmp_path / "styleai.db"))

    migrations.run_migrations(db_path)
    migrations.run_migrations(db_path)

    run.assert_called_once_with(db_path)


def test_pending_migrations_require_pre_migration_backup(mocker, monkeypatch, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    tmp_path.joinpath("styleai.db").mkdir()
    connection = sqlite3.connect(tmp_path / "styleai.db" / "styles.sqlite")
    connection.execute("CREATE TABLE legacy_state (value TEXT)")
    connection.commit()
    connection.close()
    monkeypatch.setattr("config.DB_PATH", db_path)
    migrations._migrated_paths.discard(db_path)
    backup = mocker.patch("services.db.create_persistent_backup")
    mocker.patch("services.db.ensure_catalog_ownership")

    migrations.run_migrations(db_path, force=True)

    backup.assert_called_once_with(reason="pre-migration")
