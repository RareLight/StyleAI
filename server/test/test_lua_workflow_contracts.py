from pathlib import Path
import re

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin"


def _source(name: str) -> str:
    return (PLUGIN_ROOT / name).read_text(encoding="utf-8")


def test_stable_metadata_v1_prefix_matches_algorithm_contract():
    source = _source("Util.lua")

    assert 'local STABLE_ID_ALGO = "stable_meta_v1"' in source
    assert 'return "meta1:" .. digest, nil' in source
    assert 'return "meta2:" .. digest, nil' not in source


def test_training_preflight_is_chunked_and_deduplicated_in_lightroom():
    preflight_source = _source("TrainingPreflight.lua")
    task_source = _source("TaskTrainFromEdits.lua")

    assert "local chunkSize = 1000" in preflight_source
    assert "seenIds[photoId]" in preflight_source
    assert "representativeById[photoId]" in task_source
    assert "duplicateSourceCount" in task_source


@pytest.mark.parametrize("catalog_size", [0, 1, 5_000, 5_001, 10_000])
def test_training_preflight_contract_has_no_fixed_catalog_ceiling(catalog_size):
    preflight_source = _source("TrainingPreflight.lua")
    chunk_match = re.search(r"local chunkSize = (\d+)", preflight_source)

    assert chunk_match is not None
    assert "for chunkStart = 1, #uniqueIds, chunkSize do" in preflight_source
    chunk_size = int(chunk_match.group(1))
    pages = [
        range(start, min(start + chunk_size, catalog_size))
        for start in range(0, catalog_size, chunk_size)
    ]

    assert all(len(page) <= chunk_size for page in pages)
    assert [photo for page in pages for photo in page] == list(range(catalog_size))


def test_indexing_has_explicit_already_complete_noop_contract():
    api_source = _source("APISearchIndex.lua")
    task_source = _source("TaskAnalyzeAndIndex.lua")

    assert 'return "success", stats.processed, 0, {}, nil, nil, nil, true' in api_source
    assert "and operationId ~= nil" in task_source
    assert "AnalyzeAndIndex/AlreadyComplete" in task_source


def test_prepare_photos_waits_for_backend_before_model_status_dialog():
    task_source = _source("TaskAnalyzeAndIndex.lua")

    snapshot = task_source.index("PhotoSelector.snapshotSelectedPhotos()")
    wait = task_source.index("Util.waitForServerDialog", snapshot)
    dialog = task_source.index("showAnalyzeAndIndexDialog(context)", wait)

    assert snapshot < wait < dialog
    assert task_source.count("Util.waitForServerDialog") == 1


def test_lightroom_default_metadata_prompt_is_specific_and_migrates_prior_default():
    defaults_source = _source("Defaults.lua")
    settings_source = _source("SettingsManager.lua")
    legacy_end = defaults_source.index("\n}\nDefaults.defaultSystemInstruction")
    legacy_source = defaults_source[:legacy_end]
    active_source = defaults_source[legacy_end:]

    assert "Limit output to 5-10 highly relevant tags" in legacy_source
    assert "return 10-12 distinct, highly relevant tags" in legacy_source
    assert "return up to 12 distinct, highly relevant terms" in active_source
    assert "typically 8-12 total across all categories" in active_source
    assert "species, breed, plant type, landmark, vehicle type" in active_source
    assert "specific term and one useful broader class" in active_source
    assert "important actions, interactions, behavior, or events" in active_source
    assert "clearly supported season, weather, time of day" in active_source
    assert "supplied context as factual context" in active_source
    assert "for a screen-reader user" in active_source
    assert (
        "for _, legacy in ipairs(Defaults.legacySystemInstructions or {})"
        in settings_source
    )


def test_metadata_benchmark_freezes_selection_and_never_uses_indexing_path():
    source = _source("MetadataBenchmark.lua")
    api_source = _source("APISearchIndex.lua")
    report_source = _source("MetadataBenchmarkReport.lua")

    assert "PhotoSelector.snapshotSelectedPhotos()" in source
    assert 'createCollectionSet("Benchmarks", rootSet, true)' in source
    assert "getJpegThumbnailForPhoto(photo, 1024, 1024" in source
    assert "LrStringUtils.encodeBase64(jpegData)" in source
    assert "Util.getPhotoExif(photo)" in source
    assert '"metadata_benchmark"' in source
    assert "runMetadataBenchmarkBatch" in source
    assert 'WorkCoordinator.acquire("render", progressScope)' in source
    assert 'WorkCoordinator.acquire("catalog_write")' in source
    assert "analyzeAndIndexSelectedPhotos" not in source
    assert "applyMetadata" not in source
    assert 'METADATA_BENCHMARK = "/metadata_benchmark/run_batch"' in api_source
    assert "source_photo_id = item.source_photo_id" in api_source
    assert 'WorkCoordinator.acquire("request")' in api_source
    for filename in (
        "manifest.json",
        "results.jsonl",
        "comparison.csv",
        "summary.csv",
        "report.html",
    ):
        assert filename in report_source
    assert "proxy_consistency" in report_source
    assert "proxy_mismatches" in report_source


def test_metadata_benchmark_backend_service_has_no_persistence_dependency():
    source = (
        REPOSITORY_ROOT / "server" / "src" / "services" / "metadata_benchmark.py"
    ).read_text(encoding="utf-8")

    assert "generate_metadata_single" in source
    assert "from services import chroma" not in source
    assert "services.chroma" not in source
    assert "catalog_write" not in source


def test_single_build_developer_options_hide_and_gate_every_tool():
    info_source = _source("Info.lua")
    settings_source = _source("SettingsManager.lua")
    dialog_source = _source("PluginInfoDialogSections.lua")
    gate_source = _source("DeveloperOptions.lua")

    assert "LrHelpMenuItems" not in info_source
    assert "enableDeveloperOptions = false" in settings_source
    assert 'bind("enableDeveloperOptions")' in dialog_source
    assert 'visible = bind("enableDeveloperOptions")' in dialog_source
    assert 'visible = bind("debugMode")' in dialog_source
    assert 'propertyTable:addObserver("enableDeveloperOptions"' in dialog_source
    assert 'propertyTable.runDeveloperTool("TaskMetadataBenchmark")' in dialog_source
    assert (
        "prefs.enableDeveloperOptions = propertyTable.enableDeveloperOptions == true"
        in dialog_source
    )
    assert "developerPrefs.enableDeveloperOptions == true" in gate_source
    assert "DeveloperOptions/Disabled=" in gate_source
    assert not (PLUGIN_ROOT / "BuildConfig.lua").exists()

    entry_points = {
        "TaskAutomatedTests.lua": "local confirm = LrDialogs.confirm",
        "TaskBenchmark.lua": "local catalog = LrApplication.activeCatalog()",
        "TaskMetadataBenchmark.lua": "MetadataBenchmark.run(ctx)",
        "TaskRenderingStateCapabilitySpike.lua": "local ok, err = LrTasks.pcall(runSpike)",
        "TaskReconcileAIEditState.lua": "Util.waitForServerDialog",
    }
    for filename, first_work in entry_points.items():
        source = _source(filename)
        assert ".run()" in source
        assert "return Task" in source
        gate = source.index("DeveloperOptions.requireEnabled()")
        work = source.index(first_work, gate)
        assert gate < work


def test_training_uses_raw_source_contract_without_rendered_preview_payloads():
    task_source = _source("TaskTrainFromEdits.lua")

    assert "getJpegThumbnailForPhoto(photo, 1024, 1024)" not in task_source
    assert "image_bytes = imageBytes" not in task_source
    assert 'filepath = getPhotoRawMeta(photo, "path")' in task_source


def test_data_recovery_actions_share_single_flight_inline_status_contract():
    source = _source("PluginInfoDialogSections.lua")
    prune_source = _source("TaskPruneDatabase.lua")

    assert "propertyTable.dataRecoveryBusy = false" in source
    assert "DataRecoveryReady=Ready." in source
    assert source.count('key = "dataRecoveryBusy"') == 3
    assert source.count("propertyTable.runDataRecovery(") == 3
    claim = source.index("propertyTable.dataRecoveryBusy = true")
    launch = source.index("LrTasks.startAsyncTask(function()", claim)
    assert claim < launch
    assert 'title = bind("dataRecoveryStatus")' in source
    assert "DataRecoveryStatus=Status:" in source
    assert "CleanupComplete=Cleanup complete" in source
    assert "LrDialogs.showBezel(propertyTable.dataRecoveryStatus, 4)" in source
    data_recovery_section = source[
        source.index("DataRecovery=Data & Recovery") : source.index(
            "SupportDebug=Support & Debug"
        )
    ]
    assert "LrDialogs.message" not in data_recovery_section
    assert "function TaskPruneDatabase.confirm()" in prune_source
    assert "function TaskPruneDatabase.process()" in prune_source
    assert "LrTasks.startAsyncTask" not in prune_source


def test_training_operation_fingerprint_covers_complete_deduplicated_request():
    api_source = _source("APISearchIndex.lua")
    preflight_source = _source("TrainingPreflight.lua")
    task_source = _source("TaskTrainFromEdits.lua")

    helper = api_source[
        api_source.index(
            "function SearchIndexAPI.trainingOperationFingerprint"
        ) : api_source.index(
            "function SearchIndexAPI.getOperation",
            api_source.index("function SearchIndexAPI.trainingOperationFingerprint"),
        )
    ]
    assert "TrainingPreflight.fingerprintPayload" in helper
    assert 'schema = "training_operation_v1"' in preflight_source
    assert 'kind = "training"' in preflight_source
    assert "TrainingPreflight.sortedUniqueIds(photoIds)" in preflight_source
    assert "force_retrain = forceRetrain == true" in preflight_source
    assert 'scope = tostring(scope or "selected")' in preflight_source

    capture = task_source.index("local requestedOperationItemIds = operationItemIds")
    filtering = task_source.index("operationItemIds = filteredOperationIds")
    fingerprint = task_source.index("SearchIndexAPI.trainingOperationFingerprint")
    operation = task_source.index('SearchIndexAPI.startOperation(\n\t\t\t"training"')
    assert capture < filtering < fingerprint < operation
    assert "requestedOperationItemIds," in task_source[fingerprint:operation]
    assert "requestFingerprint," in task_source[operation : operation + 900]
    assert "false\n\t\t)" in task_source[operation : operation + 900]


def test_training_preflight_failure_is_atomic_before_operation_or_source_work():
    source = _source("TaskTrainFromEdits.lua")

    preflight = source.index("SearchIndexAPI.preflightTrainingExamples")
    failure = source.index("if not preflightOk then", preflight)
    operation = source.index('SearchIndexAPI.startOperation(\n\t\t\t"training"')
    source_collection = source.index("local producerDone = false")
    assert preflight < failure < operation < source_collection
    failure_block = source[failure:operation]
    assert "progressScope:done()" in failure_block
    assert "return" in failure_block
