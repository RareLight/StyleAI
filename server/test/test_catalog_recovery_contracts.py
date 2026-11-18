"""Regression cases that cross the route, job ledger and storage boundaries."""

from pathlib import Path
from urllib.parse import quote

import pytest

import config
import server_lifecycle
from core.migrations import run_migrations
from services import chroma, db, operations, source_embeddings, training
from styleai_server import app


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    path = tmp_path / "caf\u00e9" / "styleai.db"
    path.mkdir(parents=True)
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(server_lifecycle, "_ready_catalog", None)
    for module, names in (
        (chroma, ("chroma_client", "collection")),
        (training, ("_chroma_client", "_training_collection")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, None)
    yield str(path)
    chroma.unload_collections()
    training.unload_collections()


def test_readiness_failure_retries_and_foreign_mutations_never_dispatch(
    catalog, mocker
):
    migrate = mocker.patch(
        "core.migrations.run_migrations", side_effect=OSError("injected")
    )
    prune = mocker.patch.object(db, "prune_database")
    marker = mocker.patch.object(server_lifecycle, "write_ok_file")
    client = app.test_client()
    assert client.post("/initialize", json={"db_path": catalog}).status_code == 500
    marker.assert_not_called()
    assert client.post("/initialize", json={"db_path": catalog}).status_code == 500
    assert migrate.call_count == 2
    mocker.stop(migrate)
    assert (
        client.post("/initialize", json={"db_path": catalog}).get_json()["results"][
            "status"
        ]
        == "success"
    )
    marker.assert_called_once()
    for kwargs in (
        {"headers": {"X-StyleAI-Database": quote(catalog + "-other")}},
        {"query_string": {"db_path": catalog + "-other"}},
    ):
        assert (
            client.post(
                "/db/prune", json={"valid_photo_ids": ["keep"]}, **kwargs
            ).status_code
            == 409
        )
    prune.assert_not_called()


def test_restore_retires_snapshot_jobs_and_cleans_staging_on_backup_failure(
    catalog, mocker
):
    run_migrations(catalog)
    server_lifecycle.recover_catalog_session()
    job, _ = operations.create_job(catalog, kind="training", item_ids=["p"])
    archive, _ = db.build_backup_zip(persist=False)
    try:
        operations.request_cancel(catalog, job["job_id"])
        fail = mocker.patch.object(
            db, "create_persistent_backup", side_effect=OSError("disk full")
        )
        with pytest.raises(OSError, match="disk full"):
            db.restore_backup_archive(archive)
        assert not list(Path(catalog).parent.glob(".styleai-restore-stage-*"))
        assert operations.get_job(catalog, job["job_id"])["state"] == "canceled"
        mocker.stop(fail)
        db.restore_backup_archive(archive)
        restored = operations.get_job(catalog, job["job_id"])
        assert restored["state"] == restored["items"][0]["state"] == "interrupted"
    finally:
        Path(archive).unlink()


def test_discard_preserves_vector_and_prune_handles_training_only_records(
    catalog, mocker
):
    chroma.add_image("keep", [1.0, 0.0], {"title": "discard", "rating": 5})
    assert chroma.clear_image_metadata("keep")
    stored = chroma.get_image("keep")
    assert "title" not in stored["metadatas"][0]
    assert stored["metadatas"][0]["rating"] == 5
    assert list(stored["embeddings"][0]) == [1, 0]
    training._ensure_initialized()
    training._training_collection.add(
        ids=["keep", "gone1", "gone2"], embeddings=[[0.0, 1.0]] * 3
    )
    rebuild = mocker.patch("services.policy_runtime.schedule_rebuild")
    assert db.prune_database(["keep"])["training_deleted"] == 2
    assert training._training_collection.get(include=[])["ids"] == ["keep"]
    rebuild.assert_called_once()


def test_rendered_vector_cannot_be_restamped_as_raw(mocker):
    from routes import training as route

    pixels = b"edited"
    rendered = source_embeddings.NeutralSource(
        pixels,
        source_embeddings.RENDERED_PREVIEW_PROVENANCE,
        source_embeddings.rendered_preview_fingerprint(pixels),
    )
    record = {
        "ids": ["p"],
        "embeddings": [[1.0, 0.0]],
        "metadatas": [source_embeddings.stamp_metadata({}, rendered)],
    }
    raw = source_embeddings.NeutralSource(
        b"raw", source_embeddings.RAW_PREVIEW_PROVENANCE, "fp"
    )
    mocker.patch.object(source_embeddings, "resolve_neutral_source", return_value=raw)
    mocker.patch.object(chroma, "get_image", return_value=record)
    mocker.patch.object(chroma, "get_images", return_value=record)
    mocker.patch.object(training, "get_training_source_records", return_value={})
    mocker.patch.object(source_embeddings, "decode_for_embedding", return_value=None)
    generate = mocker.patch.object(
        route, "_compute_clip_embedding", return_value=[0.0, 1.0]
    )
    for result in (
        route._resolve_training_source("p", pixels, "raw.dng"),
        route._resolve_training_sources_batch([("p", pixels, "raw.dng")])["p"],
    ):
        assert result[0] == [0.0, 1.0]
        assert result[3]["source_embedding_fingerprint"] == raw.fingerprint
    assert generate.call_count == 2


def test_large_registration_publishes_only_the_complete_set(catalog):
    run_migrations(catalog)
    client = app.test_client()
    count = 100_001
    job = client.post(
        "/operations", json={"kind": "edit", "registration_size": count}
    ).get_json()["results"]["job"]
    key = job["job_id"]
    url = f"/operations/{key}/register"
    assert client.post(url, json={"item_ids": ["0"], "final": True}).status_code == 409
    for i in range(0, count, 5000):
        stop = min(i + 5000, count)
        response = client.post(
            url,
            json={"item_ids": [str(n) for n in range(i, stop)], "final": stop == count},
        )
        assert response.status_code == 200
        if i == 0:
            assert operations.get_job_item(catalog, key, "0") is None
    assert operations.get_job_item(catalog, key, "100000")["state"] == "queued"
    ack = client.post(
        f"/operations/{key}/items",
        json={"items": [{"item_id": "0", "state": "succeeded"}]},
    )
    assert ack.status_code == 200 and len(ack.data) < 512


def test_pressure_never_escalates_explicit_gpu_limit_or_strands_ticket(mocker):
    mocker.patch.object(config, "STYLEAI_GPU_BATCH_SIZE", 1)
    for level in ("critical", "constrained"):
        mocker.patch.object(
            operations, "refresh_system_pressure", return_value={"level": level}
        )
        assert operations.recommended_gpu_batch_size() == 1
    admission = operations.ResourceAdmission({"image_bytes": 10})
    admission.update_capacities({"image_bytes": 4})
    with (
        pytest.raises(ValueError, match="budget"),
        admission.acquire({"image_bytes": 5}),
    ):
        pytest.fail("oversized batch was admitted")
    assert admission.snapshot()["waiting"] == 0
