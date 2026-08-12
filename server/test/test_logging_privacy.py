from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


def _source(relative_path: str) -> str:
    return (SOURCE_ROOT / relative_path).read_text(encoding="utf-8")


def test_index_base64_validation_never_logs_payload_or_catalog_identity():
    source = _source("routes/index.py")

    assert 'logger.info(f"{image}, {photo_id}, {filename}")' not in source
    assert "image_present=%s" in source
    assert "photo_id_present=%s" in source
    assert "filename_present=%s" in source


def test_routine_info_logs_do_not_include_photo_identity():
    route_source = _source("routes/index.py")
    index_source = _source("services/index.py")
    metadata_source = _source("services/metadata.py")
    training_source = _source("services/training.py")

    assert (
        'logger.info(f"Processing metadata field {key}: {value}")' not in route_source
    )
    assert 'f"Retrieved data for photo {photo_id}' not in route_source
    assert 'logger.info(f"UUID {uuid}' not in index_source
    assert 'logger.info(f"Generating metadata for {uuid}' not in metadata_source
    assert 'logger.info(f"Context for {uuid}' not in metadata_source
    assert 'logger.info("Updated training example photo_id=%s"' not in training_source
    assert 'logger.info("Added training example photo_id=%s"' not in training_source
    assert 'logger.info("Deleted training example photo_id=%s"' not in training_source
