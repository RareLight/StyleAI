"""Tests for routes/training.py — covers POST /training/add, GET /training/list, GET /training/stats."""

import json
import pytest
from core.migrations import run_migrations
from services import operations, source_embeddings
from styleai_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def neutral_training_source(mocker):
    source_stamp = {
        "source_embedding_provenance": "raw_preview",
        "source_embedding_fingerprint": "fingerprint",
        "source_embedding_schema": source_embeddings.SOURCE_EMBEDDING_SCHEMA_VERSION,
        "source_embedding_model": source_embeddings.SOURCE_EMBEDDING_MODEL_ID,
        "source_embedding_preprocess": source_embeddings.SOURCE_EMBEDDING_PREPROCESS_VERSION,
    }
    single = mocker.patch(
        "routes.training._resolve_training_source",
        return_value=([0.1, 0.2, 0.3], b"neutral-preview", "raw_preview", source_stamp),
    )
    mocker.patch(
        "routes.training._resolve_training_sources_batch",
        side_effect=lambda items, **_kwargs: {
            photo_id: ([0.1, 0.2, 0.3], b"neutral-preview", "raw_preview", source_stamp)
            for photo_id, _rendered, _raw_path in items
        },
    )
    return single


@pytest.fixture
def training_operation_db(tmp_path, mocker):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.training.config.DB_PATH", db_path)
    return db_path


def test_add_training_example_missing_photo_id_returns_400(client):
    response = client.post("/training/add", data={"develop_settings": "{}"})
    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data.get("error") is not None
    assert "photo_id" in json_data["error"]


def test_add_training_example_invalid_develop_settings_returns_400(client):
    response = client.post(
        "/training/add",
        data={"photo_id": "photo-1", "develop_settings": "not-valid-json"},
    )
    assert response.status_code == 400
    json_data = response.get_json()
    assert json_data.get("error") is not None
    assert "valid JSON" in json_data["error"]


def test_add_training_example_success_and_hdr_partitioning(client, mocker):
    mock_add = mocker.patch("routes.training.training_service.add_training_example")
    mocker.patch("routes.training.training_service.get_training_count", return_value=1)

    payload = {
        "photo_id": "photo-hdr-1",
        "develop_settings": json.dumps({"Exposure": 0.5}),
        "label": "Cinematic",
        "camera_profile": "Adobe Standard HDR",
    }

    response = client.post("/training/add", data=payload)
    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data.get("error") is None
    res = json_data["results"]
    assert res["status"] == "ok"
    assert res["photo_id"] == "photo-hdr-1"

    mock_add.assert_called_once()
    kwargs = mock_add.call_args.kwargs
    assert kwargs["label"] == "Cinematic"


def test_training_list_endpoint(client, mocker):
    mocker.patch(
        "routes.training.training_service.list_training_examples",
        return_value=[{"photo_id": "p1", "label": "Warm"}],
    )

    response = client.get("/training/list")
    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data.get("error") is None
    res = json_data["results"]
    assert res["count"] == 1
    assert res["examples"][0]["photo_id"] == "p1"


def test_training_stats_endpoint(client, mocker):
    mocker.patch(
        "routes.training.training_service.get_training_stats",
        return_value={"total_examples": 5, "styles": ["Warm", "Cool"]},
    )

    response = client.get("/training/stats")
    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data.get("error") is None
    res = json_data["results"]
    assert res["total_examples"] == 5


def test_training_preflight_returns_only_needed_ids(client, mocker):
    mocker.patch(
        "routes.training.training_service.get_existing_training_ids",
        return_value={"p2"},
    )
    response = client.post(
        "/training/preflight",
        json={"photo_ids": ["p1", "p2", "p3"], "force_retrain": False},
    )

    assert response.status_code == 200
    payload = response.get_json()["results"]
    assert payload["existing_photo_ids"] == ["p2"]
    assert payload["needed_photo_ids"] == ["p1", "p3"]


def test_training_preflight_accepts_full_transport_page(client, mocker):
    get_existing = mocker.patch(
        "routes.training.training_service.get_existing_training_ids",
        return_value=set(),
    )
    photo_ids = [f"p{index}" for index in range(5000)]

    response = client.post(
        "/training/preflight",
        json={"photo_ids": photo_ids, "force_retrain": False},
    )

    assert response.status_code == 200
    assert response.get_json()["results"]["needed_photo_ids"] == photo_ids
    get_existing.assert_called_once_with(photo_ids)


def test_training_preflight_rejects_duplicates_with_clear_error(client, mocker):
    get_existing = mocker.patch(
        "routes.training.training_service.get_existing_training_ids"
    )

    response = client.post(
        "/training/preflight",
        json={"photo_ids": ["p1", "p1"], "force_retrain": False},
    )

    assert response.status_code == 400
    assert "duplicates" in response.get_json()["error"]
    get_existing.assert_not_called()


def test_training_rejects_missing_neutral_source(client, neutral_training_source):
    neutral_training_source.side_effect = ValueError("NEUTRAL_SOURCE_REQUIRED")
    response = client.post(
        "/training/add",
        data={"photo_id": "p1", "develop_settings": "{}"},
    )

    assert response.status_code == 422
    assert response.get_json()["error"] == "NEUTRAL_SOURCE_REQUIRED"


def test_batch_embedding_failure_is_isolated_to_one_training_source(mocker):
    mocker.stopall()
    from routes.training import _resolve_training_sources_batch
    from services import source_embeddings

    sources = [
        source_embeddings.NeutralSource(b"raw-one", "raw_preview", "one"),
        source_embeddings.NeutralSource(b"raw-two", "raw_preview", "two"),
    ]
    mocker.patch(
        "routes.training.source_embeddings.resolve_neutral_source",
        side_effect=sources,
    )
    mocker.patch(
        "routes.training.source_embeddings.compatible_embedding",
        return_value=None,
    )
    mocker.patch("services.chroma.get_images", return_value={})
    images = [mocker.MagicMock(), mocker.MagicMock()]
    mocker.patch(
        "routes.training.source_embeddings.decode_for_embedding",
        side_effect=images,
    )
    mocker.patch(
        "routes.training.operations.recommended_gpu_batch_size", return_value=8
    )
    mocker.patch("server_lifecycle.get_model", return_value=mocker.MagicMock())
    mocker.patch("server_lifecycle.get_processor", return_value=mocker.MagicMock())
    analysis = mocker.MagicMock()
    analysis._generate_image_embeddings.return_value = [[1.0, 0.0], None]
    mocker.patch("services.metadata.get_analysis_service", return_value=analysis)
    fallback = mocker.patch(
        "routes.training._compute_clip_embedding",
        return_value=None,
    )

    resolved = _resolve_training_sources_batch(
        [
            ("photo-1", b"rendered-one", "/photos/one.raw"),
            ("photo-2", b"rendered-two", "/photos/two.raw"),
        ]
    )

    assert resolved["photo-1"][0] == [1.0, 0.0]
    assert isinstance(resolved["photo-2"], ValueError)
    assert "NEUTRAL_EMBEDDING_UNAVAILABLE" in str(resolved["photo-2"])
    analysis._generate_image_embeddings.assert_called_once()
    fallback.assert_called_once_with(b"raw-two")
    for image in images:
        image.close.assert_called_once()


def test_batch_embedding_recomputes_after_canonical_cache_error(mocker):
    mocker.stopall()
    from routes.training import _resolve_training_sources_batch
    from services import source_embeddings

    source = source_embeddings.NeutralSource(b"raw", "raw_preview", "fingerprint")
    mocker.patch(
        "routes.training.source_embeddings.resolve_neutral_source",
        return_value=source,
    )
    mocker.patch("services.chroma.get_images", side_effect=RuntimeError("cache busy"))
    image = mocker.MagicMock()
    mocker.patch(
        "routes.training.source_embeddings.decode_for_embedding",
        return_value=image,
    )
    mocker.patch(
        "routes.training.operations.recommended_gpu_batch_size", return_value=8
    )
    mocker.patch("server_lifecycle.get_model", return_value=mocker.MagicMock())
    mocker.patch("server_lifecycle.get_processor", return_value=mocker.MagicMock())
    analysis = mocker.MagicMock()
    analysis._generate_image_embeddings.return_value = [[1.0, 0.0]]
    mocker.patch("services.metadata.get_analysis_service", return_value=analysis)

    resolved = _resolve_training_sources_batch(
        [("photo-1", b"rendered", "/photos/one.raw")]
    )

    assert resolved["photo-1"][0] == [1.0, 0.0]
    image.close.assert_called_once()


def test_batch_reuses_complete_cached_training_evidence_without_raw_extraction(mocker):
    mocker.stopall()
    from routes.training import _resolve_training_sources_batch
    from services.policy_features import SOURCE_METRIC_KEYS

    metrics = {key: float(index) for index, key in enumerate(SOURCE_METRIC_KEYS)}
    metadata = {
        "source_embedding_provenance": "raw_preview",
        "source_embedding_fingerprint": "current-fingerprint",
        "source_embedding_schema": source_embeddings.SOURCE_EMBEDDING_SCHEMA_VERSION,
        "source_embedding_model": source_embeddings.SOURCE_EMBEDDING_MODEL_ID,
        "source_embedding_preprocess": source_embeddings.SOURCE_EMBEDDING_PREPROCESS_VERSION,
        **metrics,
    }
    mocker.patch(
        "services.chroma.get_images",
        return_value={
            "ids": ["photo-1"],
            "metadatas": [metadata],
            "embeddings": [[1.0, 0.0]],
        },
    )
    mocker.patch(
        "routes.training.source_embeddings.compatible_embedding",
        return_value=[1.0, 0.0],
    )
    extract = mocker.patch("routes.training.source_embeddings.resolve_neutral_source")

    resolved = _resolve_training_sources_batch([("photo-1", b"", "/photos/one.raw")])

    embedding, image_bytes, provenance, source_stamp = resolved["photo-1"]
    assert embedding == [1.0, 0.0]
    assert image_bytes == b""
    assert provenance == "raw_preview"
    assert source_embeddings.cached_source_metrics(source_stamp) == metrics
    extract.assert_not_called()


def test_batch_can_defer_policy_rebuild_until_all_chunks_are_saved(client, mocker):
    mocker.patch("routes.training.training_service.add_training_example")
    mocker.patch("routes.training.training_service.get_training_count", return_value=1)
    rebuild = mocker.patch(
        "services.policy_runtime.request_rebuild",
        return_value={"generation_id": "unused"},
    )

    response = client.post(
        "/training/add-batch",
        json={
            "examples": [{"photo_id": "p1", "develop_settings": {}}],
            "rebuild_policies": False,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["results"]["policy_generation"] is None
    assert response.get_json()["results"]["policy_warning"] is None
    rebuild.assert_not_called()


def test_batch_surfaces_policy_rebuild_failure(client, mocker):
    mocker.patch("routes.training.training_service.add_training_example")
    mocker.patch("routes.training.training_service.get_training_count", return_value=1)
    mocker.patch(
        "services.policy_runtime.request_rebuild",
        side_effect=ValueError("at least 12 examples are required"),
    )

    response = client.post(
        "/training/add-batch",
        json={"examples": [{"photo_id": "p1", "develop_settings": {}}]},
    )

    assert response.status_code == 200
    payload = response.get_json()["results"]
    assert payload["policy_generation"] is None
    assert payload["policy_warning"] == "at least 12 examples are required"


def test_batch_updates_predeclared_training_job_item(
    client, mocker, training_operation_db
):
    mocker.patch("routes.training.training_service.add_training_example")
    mocker.patch("routes.training.training_service.get_training_count", return_value=1)
    publish_states = mocker.spy(operations, "set_item_states")
    job, _ = operations.create_job(
        training_operation_db, kind="training", item_ids=["p1"]
    )

    response = client.post(
        "/training/add-batch",
        json={
            "job_id": job["job_id"],
            "examples": [{"photo_id": "p1", "develop_settings": {}}],
            "rebuild_policies": False,
        },
    )

    assert response.status_code == 200
    stored = operations.get_job(training_operation_db, job["job_id"])
    assert stored["state"] == "running"
    assert stored["items"][0]["state"] == "succeeded"
    assert publish_states.call_count == 2
    assert [call.args[2][0]["state"] for call in publish_states.call_args_list] == [
        "running",
        "succeeded",
    ]
    completed = operations.complete_submission(training_operation_db, job["job_id"])
    assert completed["state"] == "succeeded"


def test_batch_rejects_photo_not_predeclared_by_training_job(
    client, mocker, training_operation_db
):
    add_example = mocker.patch("routes.training.training_service.add_training_example")
    job, _ = operations.create_job(
        training_operation_db, kind="training", item_ids=["p1"]
    )

    response = client.post(
        "/training/add-batch",
        json={
            "job_id": job["job_id"],
            "examples": [{"photo_id": "p2", "develop_settings": {}}],
            "rebuild_policies": False,
        },
    )

    assert response.status_code == 400
    assert "not admitted" in response.get_json()["error"]
    add_example.assert_not_called()


def test_batch_honors_training_job_cancel_before_work(
    client, mocker, training_operation_db
):
    add_example = mocker.patch("routes.training.training_service.add_training_example")
    job, _ = operations.create_job(
        training_operation_db, kind="training", item_ids=["p1"]
    )
    operations.request_cancel(training_operation_db, job["job_id"])

    response = client.post(
        "/training/add-batch",
        json={
            "job_id": job["job_id"],
            "examples": [{"photo_id": "p1", "develop_settings": {}}],
            "rebuild_policies": False,
        },
    )

    assert response.status_code == 409
    add_example.assert_not_called()


def test_retried_training_chunk_skips_already_succeeded_item(
    client, mocker, training_operation_db
):
    add_example = mocker.patch("routes.training.training_service.add_training_example")
    mocker.patch("routes.training.training_service.get_training_count", return_value=1)
    job, _ = operations.create_job(
        training_operation_db, kind="training", item_ids=["p1", "p2"]
    )
    operations.set_item_state(training_operation_db, job["job_id"], "p1", "succeeded")

    response = client.post(
        "/training/add-batch",
        json={
            "job_id": job["job_id"],
            "examples": [
                {"photo_id": "p1", "develop_settings": {}},
                {"photo_id": "p2", "develop_settings": {}},
            ],
            "rebuild_policies": False,
        },
    )

    assert response.status_code == 200
    results = response.get_json()["results"]["results"]
    assert results[0]["warning"] == "Already completed in this operation"
    assert add_example.call_count == 1
    assert add_example.call_args.kwargs["photo_id"] == "p2"


def test_delete_training_clears_examples_and_derived_policies(client, mocker):
    backup = mocker.patch("services.db.create_persistent_backup")
    reset = mocker.patch("services.policy_runtime.reset_policy_state", return_value=3)
    clear = mocker.patch(
        "routes.training.training_service.clear_all_training_examples", return_value=7
    )

    response = client.delete("/training")

    assert response.status_code == 200
    payload = response.get_json()["results"]
    assert payload["removed"] == 7
    assert payload["styles_removed"] == 3
    reset.assert_called_once_with()
    clear.assert_called_once_with()
    backup.assert_called_once_with(reason="pre-delete-training")
