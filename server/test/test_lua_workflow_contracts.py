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
    api_source = _source("APISearchIndex.lua")
    task_source = _source("TaskTrainFromEdits.lua")

    assert "local chunkSize = 1000" in api_source
    assert "seenIds[photoId]" in api_source
    assert "representativeById[photoId]" in task_source
    assert "duplicateSourceCount" in task_source


@pytest.mark.parametrize("catalog_size", [0, 1, 5_000, 5_001, 10_000])
def test_training_preflight_contract_has_no_fixed_catalog_ceiling(catalog_size):
    api_source = _source("APISearchIndex.lua")
    chunk_match = re.search(r"local chunkSize = (\d+)", api_source)

    assert chunk_match is not None
    assert "for chunkStart = 1, #uniqueIds, chunkSize do" in api_source
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


def test_training_uses_raw_source_contract_without_rendered_preview_payloads():
    task_source = _source("TaskTrainFromEdits.lua")

    assert "getJpegThumbnailForPhoto(photo, 1024, 1024)" not in task_source
    assert "image_bytes = imageBytes" not in task_source
    assert 'filepath = getPhotoRawMeta(photo, "path")' in task_source
