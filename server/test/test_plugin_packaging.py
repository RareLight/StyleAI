from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "package_lrc_plugin.py"


def _load_packager():
    spec = spec_from_file_location("styleai_plugin_packager", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_package_modes_copy_the_same_runtime_gated_manifest(tmp_path):
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
    assert "LrHelpMenuItems" not in release_manifest
    assert "LrHelpMenuItems" not in developer_manifest
    for task in (
        "TaskAutomatedTests.lua",
        "TaskBenchmark.lua",
        "TaskMetadataBenchmark.lua",
        "TaskRenderingStateCapabilitySpike.lua",
        "TaskReconcileAIEditState.lua",
    ):
        assert task not in release_manifest
        assert task not in developer_manifest
        assert 'require("DeveloperOptions")' in (release / task).read_text(
            encoding="utf-8"
        )
        assert ".run()" in (release / task).read_text(encoding="utf-8")
    assert release_manifest == developer_manifest == source_manifest
    assert not (release / "BuildConfig.lua").exists()
    assert not (developer / "BuildConfig.lua").exists()
    assert (packager.SOURCE_PLUGIN / "Info.lua").read_text(
        encoding="utf-8"
    ) == source_manifest


def test_macos_development_backend_is_detached_from_lightroom():
    api_source = (
        REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin" / "APISearchIndex.lua"
    ).read_text(encoding="utf-8")

    assert "launchctl submit -l" in api_source
    assert "com.styleai.server.dev." in api_source
    assert 'LrPathUtils.child(devServerDir, ".venv")' in api_source
    assert "venvPython, devServerScript, dbPath" in api_source
