import os

import numpy as np

from services import source_embeddings


def _record(embedding, metadata):
    return {
        "ids": ["photo-1"],
        "embeddings": [embedding],
        "metadatas": [metadata],
    }


def test_raw_fingerprint_changes_with_source_state(tmp_path):
    source = tmp_path / "photo.raw"
    source.write_bytes(b"first")
    first = source_embeddings.raw_file_fingerprint(str(source))

    source.write_bytes(b"second-version")
    os.utime(source, None)
    second = source_embeddings.raw_file_fingerprint(str(source))

    assert first
    assert second
    assert first != second


def test_compatible_embedding_requires_complete_matching_contract(tmp_path):
    raw = tmp_path / "photo.raw"
    raw.write_bytes(b"raw-source")
    source = source_embeddings.NeutralSource(
        image_bytes=b"preview",
        provenance=source_embeddings.RAW_PREVIEW_PROVENANCE,
        fingerprint=source_embeddings.raw_file_fingerprint(str(raw)),
    )
    metadata = source_embeddings.stamp_metadata({}, source)
    record = _record([1.0, 0.0], metadata)

    assert source_embeddings.compatible_embedding(
        record,
        raw_filepath=str(raw),
        rendered_image_bytes=b"rendered",
    ) == [1.0, 0.0]

    metadata["source_embedding_schema"] = "old-schema"
    assert (
        source_embeddings.compatible_embedding(
            record,
            raw_filepath=str(raw),
            rendered_image_bytes=b"rendered",
        )
        is None
    )


def test_compatible_embedding_rejects_changed_raw_and_invalid_vector(tmp_path):
    raw = tmp_path / "photo.raw"
    raw.write_bytes(b"raw-source")
    source = source_embeddings.NeutralSource(
        image_bytes=b"preview",
        provenance=source_embeddings.RAW_PREVIEW_PROVENANCE,
        fingerprint=source_embeddings.raw_file_fingerprint(str(raw)),
    )
    metadata = source_embeddings.stamp_metadata({}, source)

    raw.write_bytes(b"changed-source")
    assert (
        source_embeddings.compatible_embedding(
            _record([1.0, 0.0], metadata),
            raw_filepath=str(raw),
            rendered_image_bytes=b"rendered",
        )
        is None
    )
    refreshed = source_embeddings.NeutralSource(
        image_bytes=b"preview",
        provenance=source_embeddings.RAW_PREVIEW_PROVENANCE,
        fingerprint=source_embeddings.raw_file_fingerprint(str(raw)),
    )
    refreshed_metadata = source_embeddings.stamp_metadata({}, refreshed)
    assert (
        source_embeddings.compatible_embedding(
            _record(np.zeros(2, dtype=np.float32), refreshed_metadata),
            raw_filepath=str(raw),
            rendered_image_bytes=b"rendered",
        )
        is None
    )


def test_resolve_neutral_source_prefers_raw_and_labels_fallback(tmp_path, mocker):
    raw = tmp_path / "photo.raw"
    raw.write_bytes(b"raw-source")
    mocker.patch(
        "services.source_embeddings.extract_exiftool_preview",
        return_value=b"embedded-preview",
    )

    resolved = source_embeddings.resolve_neutral_source(b"rendered", str(raw))
    assert resolved.image_bytes == b"embedded-preview"
    assert resolved.provenance == source_embeddings.RAW_PREVIEW_PROVENANCE

    missing = source_embeddings.resolve_neutral_source(b"rendered", None)
    assert missing.image_bytes == b"rendered"
    assert missing.provenance == source_embeddings.RENDERED_PREVIEW_PROVENANCE


def test_cached_metrics_require_complete_contract():
    metrics = {
        key: index / 100
        for index, key in enumerate(source_embeddings.SOURCE_METRIC_KEYS)
    }
    source = source_embeddings.NeutralSource(
        image_bytes=b"rendered",
        provenance=source_embeddings.RENDERED_PREVIEW_PROVENANCE,
        fingerprint=source_embeddings.rendered_preview_fingerprint(b"rendered"),
    )
    metadata = source_embeddings.stamp_metadata({}, source, source_metrics=metrics)

    assert source_embeddings.cached_source_metrics(metadata) == metrics
    metadata.pop(source_embeddings.SOURCE_METRIC_KEYS[0])
    assert source_embeddings.cached_source_metrics(metadata) is None
