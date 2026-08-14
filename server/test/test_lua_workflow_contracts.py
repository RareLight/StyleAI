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
    assert "return up to 12 distinct, highly relevant terms" in legacy_source
    assert "return up to 12 distinct, highly relevant terms" in active_source
    assert "typically 8-12 total across all categories" in active_source
    assert "species, breed, plant type, landmark, vehicle type" in active_source
    assert "specific term and one useful broader class" in active_source
    assert "important actions, interactions, behavior, or events" in active_source
    assert "clearly supported season, weather, time of day" in active_source
    assert "supplied context as factual context" in active_source
    assert "Never output placeholders such as None, N/A, Unknown" in active_source
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
    assert "current_photo_index" in source
    assert "progressModelTitle" in source
    assert "local MAX_PROGRESS_MODEL_CHARS = 48" in source
    assert "details.quantization" in source
    assert "details.format" in source
    assert 'publisher .. ":"' in source
    assert '"$$$/StyleAI/MetadataBenchmark/Model=^1 (^2/^3)"' in source
    assert "model_index = modelIndex" in source
    assert "photo_index = photoIndex" in source
    assert "analyzeAndIndexSelectedPhotos" not in source
    assert "applyMetadata" not in source
    assert "local RECOMMENDED_MAX_PHOTOS = 32" in source
    assert "selectedPhotos > RECOMMENDED_MAX_PHOTOS" in source
    assert "Measured requests: ^1 photos × ^2 models = ^3" in source
    assert 'props:addObserver("warmup", updateRequestEstimate)' in source
    assert "A representative set of 24–32 photos is recommended" in source
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
    assert "os.rename" not in report_source
    assert "Report.IMPLEMENTATION_VERSION = 3" in report_source
    assert "roundTimingValues" in report_source
    assert "photos_per_minute = total > 0 and roundTo" in report_source
    assert "elapsed_ms = roundMilliseconds" in source
    assert 'sdkFunction(LrFileUtils, "LrFileUtils", "copy")' in report_source
    assert 'sdkFunction(LrFileUtils, "LrFileUtils", "delete")' in report_source
    assert "function Report.validateRuntime()" in report_source
    assert 'result.photo_id = "missing-photo-id-"' in report_source
    assert "protectedCall" in report_source


def test_model_selectors_use_enriched_labels_and_stable_keys():
    api_source = _source("APISearchIndex.lua")
    prepare_source = _source("TaskAnalyzeAndIndex.lua")
    benchmark_source = _source("MetadataBenchmark.lua")

    assert "function SearchIndexAPI.getModelChoices()" in api_source
    assert "response.models" in api_source
    assert 'title = tostring(provider) .. ": " .. label' in api_source
    assert 'key = tostring(provider) .. "::" .. model' in api_source
    assert "SearchIndexAPI.getModelChoices()" in prepare_source
    assert "width = 540" in prepare_source
    assert "return SearchIndexAPI.getModelChoices()" in benchmark_source


def test_metadata_benchmark_contains_controlled_runtime_error_boundaries():
    source = _source("MetadataBenchmark.lua")
    task_source = _source("TaskMetadataBenchmark.lua")

    assert "local function validateBenchmarkRuntime()" in source
    assert 'type(response.items) ~= "table"' in source
    assert 'optionalRuntimeValue(LrApplication, "versionString", "unknown")' in source
    assert 'type(LrShell.revealInShell) == "function"' in source
    assert "local ok, err = LrTasks.pcall" in task_source
    assert "Metadata benchmark failed unexpectedly" in task_source
    assert "No metadata was written to Lightroom" in task_source
    assert "operation bookkeeping also failed" in source
    assert "error(tostring(updateError))" not in source


def test_help_menu_distinguishes_llm_and_indexing_benchmarks():
    info_source = _source("Info.lua")
    indexing_source = _source("TaskBenchmark.lua")

    llm_entry = info_source.index("DeveloperLlmBenchmark")
    indexing_entry = info_source.index("DeveloperIndexingBenchmark")
    assert llm_entry < indexing_entry
    assert "Benchmark Local LLM Tagging & Metadata" in info_source
    assert "Benchmark Indexing Throughput (256+ Photos)" in info_source
    assert "local minRequired = 256" in indexing_source
    assert "not the local LLM tagging benchmark" in indexing_source


def test_metadata_benchmark_backend_service_has_no_persistence_dependency():
    source = (
        REPOSITORY_ROOT / "server" / "src" / "services" / "metadata_benchmark.py"
    ).read_text(encoding="utf-8")

    assert "generate_metadata_single" in source
    assert "from services import chroma" not in source
    assert "services.chroma" not in source
    assert "catalog_write" not in source


def test_single_build_registers_support_and_developer_tools_in_help_menu():
    info_source = _source("Info.lua")
    settings_source = _source("SettingsManager.lua")
    dialog_source = _source("PluginInfoDialogSections.lua")

    assert "LrHelpMenuItems" in info_source
    for filename in (
        "TaskOpenDocumentation.lua",
        "TaskCheckUpdates.lua",
        "TaskGenerateSupportReport.lua",
        "TaskOpenLogsFolder.lua",
        "TaskMetadataBenchmark.lua",
    ):
        assert f'file = "{filename}"' in info_source
    assert "enableDeveloperOptions" not in settings_source
    assert "enableDeveloperOptions" not in dialog_source
    assert "debugMode" not in settings_source
    assert "debugMode" not in dialog_source
    assert "captureLlmInputs = false" in settings_source
    assert 'bind("captureLlmInputs")' in dialog_source
    assert "TaskDiagnostics" not in dialog_source
    assert "manualCheckUpdates" not in dialog_source
    assert 'github.com/RareLight/StyleAI/wiki")' not in dialog_source
    assert "ExportDbBackup=Export Backup..." in dialog_source
    assert "RestoreDbBackup=Restore Backup..." in dialog_source
    assert "ClearCaptures=Clear Diagnostic Captures..." in dialog_source
    assert not (PLUGIN_ROOT / "BuildConfig.lua").exists()
    assert not (PLUGIN_ROOT / "DeveloperOptions.lua").exists()

    entry_points = {
        "TaskAutomatedTests.lua": "local confirm = LrDialogs.confirm",
        "TaskBenchmark.lua": "local catalog = LrApplication.activeCatalog()",
        "TaskMetadataBenchmark.lua": "MetadataBenchmark.run(ctx)",
        "TaskRenderingStateCapabilitySpike.lua": "local ok, err = LrTasks.pcall(runSpike)",
        "TaskReconcileAIEditState.lua": "Util.waitForServerDialog",
    }
    for filename, first_work in entry_points.items():
        source = _source(filename)
        assert "DeveloperOptions" not in source
        launch = source.index("LrTasks.startAsyncTask")
        work = source.index(first_work, launch)
        assert launch < work


def test_plugin_manager_capture_status_uses_local_query_encoding_and_safe_task_boundary():
    api_source = _source("APISearchIndex.lua")
    dialog_source = _source("PluginInfoDialogSections.lua")

    assert "local function encodeQueryValue(value)" in api_source
    assert '"?path=" .. encodeQueryValue(path)' in api_source
    assert "LrHttp.encodeForUrl" not in api_source
    refresh = dialog_source[
        dialog_source.index(
            "local function refreshCaptureInfo()"
        ) : dialog_source.index("propertyTable.refreshCaptureInfo = refreshCaptureInfo")
    ]
    assert "LrTasks.pcall" in refresh
    assert "SearchIndexAPI.getDiagnosticCaptureInfo" in refresh


def test_plugin_manager_processing_load_menu_has_bounded_width():
    source = _source("PluginInfoDialogSections.lua")
    menu = source[
        source.index('value = bind("processingLoadMode")') : source.index(
            'LoadAutomatic=Automatic (recommended)'
        )
    ]

    assert "width = 220" in menu
    assert "fill_horizontal" not in menu


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
            "Diagnostics=Diagnostics"
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
