from core import migrations


def test_migrations_run_once_per_catalog_path(mocker, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    run = mocker.patch.object(migrations, "_run_migrations_uncached")
    migrations._migrated_paths.discard(str(tmp_path / "styleai.db"))

    migrations.run_migrations(db_path)
    migrations.run_migrations(db_path)

    run.assert_called_once_with(db_path)
