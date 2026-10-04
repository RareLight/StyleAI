import json
import os
import plistlib
import re
import shutil
import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "package_lrc_plugin.py"
FINGERPRINT_SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "plugin_tree_fingerprint.py"
PLUGIN_VALIDATOR_SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "validate_lrc_plugin.py"
PLUGIN_ROOT = REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin"
LUA = shutil.which("lua")


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


def _load_plugin_validator():
    spec = spec_from_file_location(
        "styleai_plugin_validator", PLUGIN_VALIDATOR_SCRIPT_PATH
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
        "TaskEvaluateAppliedEditQuality.lua",
        "TaskStyleRefinement.lua",
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
    # workflow records source membership before creation, merges the settled
    # copy membership later, and removes inherited standard/published membership.
    assert "local function appendRemovableCollections" in source
    capture_position = source.index(
        "appendRemovableCollections(photo, inheritedCollections, seenCollections)"
    )
    create_position = source.index("catalog:createVirtualCopies(", capture_position)
    assert capture_position < create_position
    assert "settled post-creation membership read" in source
    assert "photo:getContainedCollections()" in source
    assert "photo:getContainedPublishedCollections()" in source
    assert "inheritedCollection:removePhotos({ editCopy })" in source
    # A deliberate backend abstention is acknowledged as a successful skip, so
    # unsupported rendering partitions do not fail the entire edit operation.
    assert 'apiResponse.status == "skipped"' in source
    assert 'finishOperationItem(photoIdsByIndex[index], "succeeded", nil)' in source
    assert "SkipDetails=Skipped details:" in source


def test_edit_metadata_and_review_workflow_expose_user_facing_statuses_only():
    plugin = REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin"
    provider = (plugin / "MetadataProvider.lua").read_text(encoding="utf-8")
    review = (plugin / "TaskReviewAIEditOutcome.lua").read_text(encoding="utf-8")

    for field_id, title in (
        ("aiEditApplied", "StyleAI Edit Applied"),
        ("aiEditState", "StyleAI Edit State"),
        ("aiEditReview", "StyleAI Edit Review"),
    ):
        field_start = provider.index(f'id = "{field_id}"')
        field_end = provider.index("\n\t\t},", field_start)
        field = provider[field_start:field_end]
        assert title in field
        assert "searchable = true" in field
        assert "browsable = true" in field

    for field_id in ("aiEditStatus", "aiEditInferenceId", "keywords", "globalPhotoId"):
        field_start = provider.index(f'id = "{field_id}"')
        field_end = provider.index("\n\t\t},", field_start)
        field = provider[field_start:field_end]
        assert "searchable = false" in field
        assert "browsable = false" in field

    assert "getStyleEditOutcomeStatuses" in review
    assert "includePreviouslyReviewed" in review
    assert "Previously reviewed photos are skipped by default" in review
    assert '"aiEditReview"' in review
    assert "reviewDisplayValue(stored.outcome)" in review


def test_metadata_contract_requires_per_field_versions(tmp_path):
    validator = _load_plugin_validator()
    source_plugin = REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin"
    provider = (source_plugin / "MetadataProvider.lua").read_text(encoding="utf-8")
    plugin = tmp_path / "StyleAI.lrdevplugin"
    plugin.mkdir()
    (plugin / "MetadataProvider.lua").write_text(provider, encoding="utf-8")

    assert validator.check_metadata_contract_versions(plugin) == []

    unversioned_change = provider.replace("searchable = true", "searchable = false", 1)
    (plugin / "MetadataProvider.lua").write_text(unversioned_change, encoding="utf-8")
    errors = validator.check_metadata_contract_versions(plugin)
    assert any(
        "aiLastRun" in error and "changed without a new version" in error
        for error in errors
    )


def test_developer_evaluation_and_refinement_workflows_remain_safe_and_separate():
    plugin = REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin"
    manifest = (plugin / "Info.lua").read_text(encoding="utf-8")
    evaluation = (plugin / "TaskEvaluateAppliedEditQuality.lua").read_text(
        encoding="utf-8"
    )
    refinement = (plugin / "TaskStyleRefinement.lua").read_text(encoding="utf-8")
    manager = (plugin / "DevelopEditManager.lua").read_text(encoding="utf-8")

    assert "Developer: Evaluate Applied Edit Quality..." in manifest
    assert "Developer: Test Style Refinement..." in manifest
    assert "TaskEvaluateAppliedEditQuality.lua" in manifest
    assert "TaskStyleRefinement.lua" in manifest
    assert "LrTasks.startAsyncTask" in evaluation
    assert "evaluateAppliedEdits" in evaluation
    assert "LrTasks.startAsyncTask" in refinement
    assert 'photo:getRawMetadata("isVirtualCopy") == true' in refinement
    assert "restoreCopies" in refinement
    assert "recordStyleRefinementChoice" in refinement
    assert "finalizeStyleRefinementSession" in refinement
    assert "applyPreviewRecipe" in manager
    preview_start = manager.index("function DevelopEditManager.applyPreviewRecipe")
    preview_source = manager[preview_start:]
    assert "persistEditRecipe" not in preview_source


def test_macos_development_backend_is_detached_from_lightroom():
    api_source = (
        REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin" / "APISearchIndex.lua"
    ).read_text(encoding="utf-8")

    assert "launchctl bootstrap" in api_source
    assert '\\"gui/$(id -u)\\"' in api_source
    assert "launchctl submit" not in api_source
    assert "kickstart" not in api_source
    assert "styleai-server-launch.plist" in api_source
    assert 'nextServerLaunchLabel("dev")' in api_source
    assert 'nextServerLaunchLabel("session")' in api_source
    assert "serverLaunchSequence" in api_source
    assert "LrDate.currentTime()" in api_source
    assert "<key>RunAtLoad</key>" in api_source
    assert "<key>KeepAlive</key>" in api_source
    assert "<true/>" in api_source
    assert "<false/>" in api_source
    assert 'LrPathUtils.child(devServerDir, ".venv")' in api_source
    assert "venvPython," in api_source
    assert "devServerScript," in api_source
    assert '"--db-path",' in api_source
    assert "dbPath," in api_source
    assert '"--lightroom-managed"' in api_source
    assert (
        'PATH=\\"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH\\"'
        in api_source
    )


def test_macos_launch_plist_parse_is_one_shot():
    api_source = (
        REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin" / "APISearchIndex.lua"
    ).read_text(encoding="utf-8")

    run_at_load_at = api_source.index("<key>RunAtLoad</key>")
    assert api_source.index("<true/>") > run_at_load_at
    keep_alive_at = api_source.index("<key>KeepAlive</key>")
    assert api_source.index("<false/>") > keep_alive_at


def test_packaged_launch_agent_plist_is_one_shot_and_managed():
    plist_path = REPOSITORY_ROOT / "installers" / "macos" / "com.styleai.server.plist"
    plist = plistlib.loads(plist_path.read_bytes())

    assert plist["RunAtLoad"] is False
    assert plist["KeepAlive"] is False
    assert plist["ProgramArguments"] == [
        "/Applications/StyleAI/Server/styleai-server",
        "--lightroom-managed",
    ]


def test_repair_restart_explicitly_stops_and_relaunches_backend():
    api_source = (
        REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin" / "APISearchIndex.lua"
    ).read_text(encoding="utf-8")

    restart_start = api_source.index("function SearchIndexAPI.restartBackend()")
    restart_end = api_source.index(
        "function SearchIndexAPI.repairBackend()", restart_start
    )
    restart_body = api_source[restart_start:restart_end]

    assert "SearchIndexAPI.shutdownServer()" in restart_body
    assert "SearchIndexAPI.startServer(" in restart_body
    assert "readyTimeoutSeconds = 120" in restart_body
    assert "ENDPOINTS.RESTART" not in restart_body
    assert "pingServer()" not in restart_body


@pytest.mark.skipif(LUA is None, reason="lua interpreter is not available")
def test_macos_launch_writes_one_shot_jobs_for_every_variant(tmp_path):
    repo = tmp_path / "cat's & <repo>"
    plugin_dir = repo / "plugin" / "StyleAI.lrdevplugin"
    plugin_dir.mkdir(parents=True)
    server_dir = repo / "server"
    server_dir.mkdir()
    catalog_dirs = {}
    for name in ("venv", "uv", "system", "local"):
        catalog_dir = tmp_path / f"cat's & <{name}>"
        catalog_dir.mkdir()
        catalog_dirs[name] = catalog_dir

    def lua_string(value):
        return json.dumps(str(value))

    script = (
        """
local pluginDir = __PLUGIN_DIR__
local devServerDir = __DEV_SERVER_DIR__
local currentCatalogPath
local stub = { command = nil, exists = {} }
MAC_ENV = true
WIN_ENV = false
_PLUGIN = { path = pluginDir }
log = setmetatable({}, { __index = function() return function(...) end end })
LOC = function(value) return value end
os.time = function() return 1700000000 end
LrDate = { currentTime = function() return 1700000000.5 end }
LrPathUtils = {
    child = function(a, b) return tostring(a) .. "/" .. tostring(b) end,
    parent = function(p) return (tostring(p):gsub("/[^/]+$", "")) end,
    getStandardFilePath = function() return "/nonexistent-home" end,
}
LrApplication = {
    activeCatalog = function()
        return { getPath = function() return currentCatalogPath end }
    end,
}
LrFileUtils = {
    exists = function(path) return stub.exists[path] == true end,
    delete = function(path) return os.remove(path) ~= nil end,
}
LrTasks = {
    execute = function(command) stub.command = command return 0 end,
    startAsyncTask = function(fn) fn() end,
    pcall = function(fn, ...) return pcall(fn, ...) end,
    sleep = function() end,
    yield = function() end,
}
LrHttp = {}
LrShell = {}
JSON = { encode = function() return "{}" end, decode = function() return {} end }
local realRequire = require
require = function(name) return rawget(_G, name) or {} end
local api = dofile(__API_PATH__)
require = realRequire
local pingQueue = {}
api.pingServer = function()
    if #pingQueue > 0 then return table.remove(pingQueue, 1) end
    return true
end
api.initializeCatalog = function() return true end
api.checkServerHealth = function() end
local function plistLabel(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local body = f:read("*a")
    f:close()
    return body:match("<key>Label</key>%s*<string>([^<]+)</string>")
end
local function runStart(name, catalogDir)
    currentCatalogPath = catalogDir .. "/catalog.lrcat"
    pingQueue = { false }
    stub.command = nil
    local started = api.startServer({ readyTimeoutSeconds = 5 })
    print("RESULT[" .. name .. "]=" .. tostring(started))
    print("CMD[" .. name .. "]=" .. tostring(stub.command))
    print("LABEL[" .. name .. "]=" .. tostring(
        plistLabel(catalogDir .. "/styleai-server-launch.plist")))
end
local devScript = devServerDir .. "/src/styleai_server.py"
local devVenv = devServerDir .. "/.venv/bin/python"
stub.exists = { [devScript] = true, [devVenv] = true }
runStart("venv", __CAT_VENV__)
runStart("venv2", __CAT_VENV__)
stub.exists = { [devScript] = true }
runStart("uv", __CAT_UV__)
stub.exists = { ["/Applications/StyleAI/Server/styleai-server"] = true }
runStart("system", __CAT_SYS__)
local localBinary = LrPathUtils.parent(pluginDir) .. "/styleai-server/styleai-server"
stub.exists = { [localBinary] = true }
runStart("local", __CAT_LOCAL__)
local shutdownResult = false
local startCalls = 0
local startOpts = nil
api.shutdownServer = function() return shutdownResult end
api.startServer = function(opts)
    startCalls = startCalls + 1
    startOpts = opts
    return true
end
local failedResult, failedError = api.restartBackend()
print("REPAIR_FAIL=" .. tostring(failedResult) .. "|"
    .. tostring(startCalls) .. "|" .. tostring(failedError))
shutdownResult = true
local okResult = api.restartBackend()
print("REPAIR_OK=" .. tostring(okResult) .. "|" .. tostring(startCalls)
    .. "|" .. tostring(startOpts and startOpts.readyTimeoutSeconds))
""".replace("__PLUGIN_DIR__", lua_string(plugin_dir))
        .replace("__DEV_SERVER_DIR__", lua_string(server_dir))
        .replace("__API_PATH__", lua_string(PLUGIN_ROOT / "APISearchIndex.lua"))
        .replace("__CAT_VENV__", lua_string(catalog_dirs["venv"]))
        .replace("__CAT_UV__", lua_string(catalog_dirs["uv"]))
        .replace("__CAT_SYS__", lua_string(catalog_dirs["system"]))
        .replace("__CAT_LOCAL__", lua_string(catalog_dirs["local"]))
    )
    completed = subprocess.run(
        [LUA, "-e", script], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    output = completed.stdout

    def field(name):
        match = re.search(rf"^{re.escape(name)}=(.*)$", output, re.MULTILINE)
        assert match is not None, output
        return match.group(1)

    for name in ("venv", "venv2", "uv", "system", "local"):
        assert field(f"RESULT[{name}]") == "true", output

    dev_label = re.compile(r"com\.styleai\.server\.dev\.1700000000500\.\d+")
    session_label = re.compile(r"com\.styleai\.server\.session\.1700000000500\.\d+")
    assert dev_label.fullmatch(field("LABEL[venv]"))
    assert dev_label.fullmatch(field("LABEL[venv2]"))
    assert field("LABEL[venv]") != field("LABEL[venv2]")
    assert session_label.fullmatch(field("LABEL[system]"))
    assert session_label.fullmatch(field("LABEL[local]"))

    db_paths = {name: str(catalog_dirs[name] / "styleai.db") for name in catalog_dirs}
    for name in ("venv", "uv", "system", "local"):
        plist_path = catalog_dirs[name] / "styleai-server-launch.plist"
        quoted = "'" + str(plist_path).replace("'", "'\\''") + "'"
        assert field(f"CMD[{name}]") == (f'launchctl bootstrap "gui/$(id -u)" {quoted}')
        plist = plistlib.loads(plist_path.read_bytes())
        assert plist["RunAtLoad"] is True
        assert plist["KeepAlive"] is False
        arguments = plist["ProgramArguments"]
        if name == "venv":
            assert arguments == [
                str(server_dir / ".venv" / "bin" / "python"),
                str(server_dir / "src" / "styleai_server.py"),
                "--db-path",
                db_paths["venv"],
                "--lightroom-managed",
            ]
            assert plist["EnvironmentVariables"]["KMP_DUPLICATE_LIB_OK"] == "TRUE"
            assert "PATH" in plist["EnvironmentVariables"]
        elif name == "uv":
            assert arguments[:2] == ["/bin/bash", "-c"]
            assert "exec uv run python src/styleai_server.py" in arguments[2]
        else:
            binary = (
                "/Applications/StyleAI/Server/styleai-server"
                if name == "system"
                else str(plugin_dir.parent / "styleai-server" / "styleai-server")
            )
            assert arguments == [
                binary,
                "--db-path",
                db_paths[name],
                "--lightroom-managed",
            ]
            assert plist["EnvironmentVariables"]["KMP_DUPLICATE_LIB_OK"] == "TRUE"

    fake_bin = tmp_path / "fakehome" / ".local" / "bin"
    fake_bin.mkdir(parents=True)
    uv_capture = tmp_path / "uv-argv.txt"
    uv_stub = fake_bin / "uv"
    uv_stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STYLEAI_UV_CAPTURE"\n')
    uv_stub.chmod(0o755)
    environment = dict(os.environ)
    environment["HOME"] = str(fake_bin.parent.parent)
    environment["STYLEAI_UV_CAPTURE"] = str(uv_capture)
    uv_script = plistlib.loads(
        (catalog_dirs["uv"] / "styleai-server-launch.plist").read_bytes()
    )["ProgramArguments"][2]
    launched = subprocess.run(
        ["/bin/bash", "-c", uv_script],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert launched.returncode == 0, launched.stderr
    assert uv_capture.read_text().splitlines() == [
        "run",
        "python",
        "src/styleai_server.py",
        "--db-path",
        db_paths["uv"],
        "--lightroom-managed",
    ]

    failed = field("REPAIR_FAIL").split("|")
    assert failed == ["false", "0", "Backend shutdown failed"]
    repaired = field("REPAIR_OK").split("|")
    assert repaired == ["true", "1", "120"]
