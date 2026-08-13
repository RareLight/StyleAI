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


def test_release_and_developer_packages_keep_manifest_separation(tmp_path):
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
    assert "developerBuild = false" in (release / "BuildConfig.lua").read_text(
        encoding="utf-8"
    )
    assert "LrHelpMenuItems" in developer_manifest
    assert "TaskAutomatedTests.lua" in developer_manifest
    assert "TaskBenchmark.lua" in developer_manifest
    assert "TaskMetadataBenchmark.lua" in developer_manifest
    assert "TaskMetadataBenchmark.lua" not in release_manifest
    assert "TaskRenderingStateCapabilitySpike.lua" in developer_manifest
    assert "TaskReconcileAIEditState.lua" in developer_manifest
    assert "developerBuild = true" in (developer / "BuildConfig.lua").read_text(
        encoding="utf-8"
    )
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
