from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "package_lrc_plugin.py"
FINGERPRINT_SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "plugin_tree_fingerprint.py"


def _load_packager():
    spec = spec_from_file_location("styleai_plugin_packager", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_fingerprinter():
    spec = spec_from_file_location(
        "styleai_plugin_tree_fingerprint", FINGERPRINT_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_package_modes_copy_the_same_canonical_manifest(tmp_path):
    packager = _load_packager()
    source_manifest = (packager.SOURCE_PLUGIN / "Info.lua").read_text(encoding="utf-8")
    release = packager.build_package("release", tmp_path / "StyleAI.lrplugin")
    developer = packager.build_package(
        "developer", tmp_path / "StyleAI-dev.lrdevplugin"
    )

    release_manifest = (release / "Info.lua").read_text(encoding="utf-8")
    developer_manifest = (developer / "Info.lua").read_text(encoding="utf-8")
    assert "LrShutdownApp" not in release_manifest
    assert "LrShutdownApp" not in developer_manifest
    assert not (release / "ShutdownApp.lua").exists()
    assert not (developer / "ShutdownApp.lua").exists()
    assert "LrHelpMenuItems" in release_manifest
    assert "LrHelpMenuItems" in developer_manifest
    support_tasks = {
        "TaskOpenDocumentation.lua": "LrHttp.openUrlInBrowser",
        "TaskCheckUpdates.lua": "UpdateCheck.checkForNewVersion",
        "TaskGenerateSupportReport.lua": "TaskDiagnostics.generateReport",
        "TaskOpenLogsFolder.lua": "LrShell.revealInShell",
    }
    for task, expected_action in support_tasks.items():
        assert task in release_manifest
        assert task in developer_manifest
        assert expected_action in (release / task).read_text(encoding="utf-8")
    for task in (
        "TaskAutomatedTests.lua",
        "TaskBenchmark.lua",
        "TaskMetadataBenchmark.lua",
        "TaskRenderingStateCapabilitySpike.lua",
        "TaskReconcileAIEditState.lua",
    ):
        assert task in release_manifest
        assert task in developer_manifest
        task_source = (release / task).read_text(encoding="utf-8")
        assert "DeveloperOptions" not in task_source
        assert "LrTasks.startAsyncTask" in task_source
    assert release_manifest == developer_manifest == source_manifest
    source_fingerprint = packager.plugin_tree_fingerprint(packager.SOURCE_PLUGIN)
    assert packager.plugin_tree_fingerprint(release) == source_fingerprint
    assert packager.plugin_tree_fingerprint(developer) == source_fingerprint
    assert not (release / "BuildConfig.lua").exists()
    assert not (developer / "BuildConfig.lua").exists()
    assert not (release / "DeveloperOptions.lua").exists()
    assert (packager.SOURCE_PLUGIN / "Info.lua").read_text(
        encoding="utf-8"
    ) == source_manifest


def test_plugin_tree_fingerprint_covers_paths_contents_and_empty_directories(tmp_path):
    fingerprinter = _load_fingerprinter()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "empty").mkdir()
    (second / "empty").mkdir()
    (first / "Info.lua").write_bytes(b"return { VERSION = 1 }")
    (second / "Info.lua").write_bytes(b"return { VERSION = 1 }")

    original = fingerprinter.plugin_tree_fingerprint(first)
    assert fingerprinter.plugin_tree_fingerprint(second) == original

    (second / "Info.lua").write_bytes(b"return { VERSION = 2 }")
    assert fingerprinter.plugin_tree_fingerprint(second) != original
    (second / "Info.lua").write_bytes(b"return { VERSION = 1 }")
    (second / "empty").rmdir()
    assert fingerprinter.plugin_tree_fingerprint(second) != original
    (second / "empty").mkdir()
    (second / "Info.lua").rename(second / "Manifest.lua")
    assert fingerprinter.plugin_tree_fingerprint(second) != original


def test_ai_edit_workflow_keeps_operation_alive_and_isolates_virtual_copies():
    source = (
        REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin" / "AiEditAction.lua"
    ).read_text(encoding="utf-8")

    # A live Lightroom review/export pause must not look like an orphaned backend.
    assert "SearchIndexAPI.getOperation(operationId, false)" in source
    assert "for _ = 1, 30 do" in source
    # A failed batch is terminal at the batch boundary, not retried once per photo.
    assert "elseif ok and apiOk then" in source
    assert "Do not multiply it into one doomed single-photo retry per image" in source
    # Lightroom can automatically add a new copy to the viewed collection. The
    # workflow records and removes that inherited standard/published membership.
    assert "editPhoto:getContainedCollections()" in source
    assert "editPhoto:getContainedPublishedCollections()" in source
    assert "inheritedCollection:removePhotos({ editCopy })" in source


def test_macos_development_backend_is_detached_from_lightroom():
    api_source = (
        REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin" / "APISearchIndex.lua"
    ).read_text(encoding="utf-8")

    assert "launchctl submit -l" in api_source
    assert "com.styleai.server.dev." in api_source
    assert 'LrPathUtils.child(devServerDir, ".venv")' in api_source
    assert (
        'PATH=\\"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH\\"'
        in api_source
    )
    assert 'exec \\"%s\\" \\"%s\\" --db-path \\"%s\\"' in api_source
    assert "venvPython, devServerScript, dbPath" in api_source
