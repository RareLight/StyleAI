"""Tests for routes/style_edit.py — covers POST /style_edit."""

import io
import json
import pytest

from core.migrations import run_migrations
from services import operations
from services.source_embeddings import SOURCE_METRIC_KEYS
from services.style_engine import StyleEngineResult
from styleai_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def available_policy_generation(mocker):
    mocker.patch(
        "routes.style_edit.policy_runtime.active_generation_id",
        return_value="generation-test",
    )
    mocker.patch(
        "routes.style_edit.policy_runtime._load_generation_artifacts",
        return_value={"partition": object()},
    )


def test_style_edit_missing_images_returns_400(client):
    response = client.post("/style_edit", data={"photo_id": "photo-1"})
    assert response.status_code == 400
    json_data = response.get_json()
    assert "error" in json_data


def test_style_edit_mismatched_lengths_returns_400(client):
    data = {
        "image": (io.BytesIO(b"fakejpeg"), "test.jpg"),
        "photo_id": ["photo-1", "photo-2"],
    }
    response = client.post("/style_edit", data=data, content_type="multipart/form-data")
    assert response.status_code == 400
    json_data = response.get_json()
    assert "Mismatch" in json_data["error"]


@pytest.mark.parametrize("field", ("profile_mode", "hdr_mode"))
def test_style_edit_rejects_invalid_rendering_mode(client, field):
    response = client.post(
        "/style_edit",
        data={
            "image": (io.BytesIO(b"fakejpeg"), "test.jpg"),
            "photo_id": "photo-1",
            field: "sometimes",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["results"] is None
    assert field in payload["error"]


def test_style_edit_single_photo_success(client, mocker):
    mocker.patch(
        "routes.style_edit._run_single_style_edit",
        return_value={
            "recipe": {"exposure": 0.5, "contrast": 10},
            "confidence": 0.85,
            "source": "ml_predictive",
        },
    )

    data = {
        "image": (io.BytesIO(b"fakejpegbytes"), "test.jpg"),
        "photo_id": "photo-123",
        "camera_profile": "Adobe Standard",
    }
    response = client.post("/style_edit", data=data, content_type="multipart/form-data")
    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data.get("error") is None
    res = json_data["results"]
    assert res["recipe"]["exposure"] == 0.5
    assert res["confidence"] == 0.85
    assert res["source"] == "ml_predictive"


def test_style_edit_batch_photos_success(client, mocker):
    mocker.patch(
        "routes.style_edit._run_single_style_edit",
        side_effect=[
            {"photo_id": "photo-1", "recipe": {"exposure": 0.2}, "confidence": 0.8},
            {"photo_id": "photo-2", "recipe": {"exposure": -0.1}, "confidence": 0.9},
        ],
    )

    data = {
        "image": [
            (io.BytesIO(b"fake1"), "test1.jpg"),
            (io.BytesIO(b"fake2"), "test2.jpg"),
        ],
        "photo_id": ["photo-1", "photo-2"],
    }
    response = client.post("/style_edit", data=data, content_type="multipart/form-data")
    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data.get("error") is None
    res = json_data["results"]
    assert res["status"] == "ok"
    assert res["batch_size"] == 2
    items = res["results"]
    assert len(items) == 2
    assert items[0]["photo_id"] == "photo-1"
    assert items[1]["photo_id"] == "photo-2"


def test_versioned_batch_preserves_per_photo_contracts(client, mocker, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_edit.config.DB_PATH", db_path)
    job, _ = operations.create_job(
        db_path, kind="edit", item_ids=["photo-1", "photo-2"]
    )
    run_batch = mocker.patch(
        "routes.style_edit._run_coherent_style_edit_batch",
        return_value=(
            [
                {
                    "status": "success",
                    "photo_id": "photo-1",
                    "engine": "policy_v2",
                    "confidence": 0.9,
                },
                {
                    "status": "error",
                    "photo_id": "photo-2",
                    "engine": "none",
                    "error": "low_confidence",
                },
            ],
            {"tier_counts": {"independent": 2}},
        ),
    )
    item_contracts = [
        {
            "photo_id": "photo-1",
            "capture_time": 100.0,
            "camera_make": "Canon",
            "camera_model": "R5",
            "camera_profile": "Adobe Color",
            "lens": "70-200",
            "iso": 400,
            "aperture": 2.8,
            "shutter_speed": "1/1000",
            "current_settings": {"Exposure2012": -0.25},
            "raw_filepath": "/photos/one.cr3",
        },
        {
            "photo_id": "photo-2",
            "capture_time": 100.1,
            "camera_make": "Canon",
            "camera_model": "R5",
            "camera_profile": "Adobe Color",
            "current_settings": {"Exposure2012": 0.1},
            "raw_filepath": "/photos/two.cr3",
        },
    ]

    response = client.post(
        "/style_edit",
        data={
            "image": [
                (io.BytesIO(b"first"), "one.jpg"),
                (io.BytesIO(b"second"), "two.jpg"),
            ],
            "photo_id": ["photo-1", "photo-2"],
            "job_id": job["job_id"],
            "items_json": json.dumps(item_contracts),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["error"] is None
    assert payload["results"]["contract_version"] == "style-edit-batch-v1"
    structured = run_batch.call_args.args[0]
    assert structured[0]["options"]["current_settings"] == {"Exposure2012": -0.25}
    assert structured[1]["options"]["raw_filepath"] == "/photos/two.cr3"
    assert structured[0]["options"]["generation_id"] == "generation-test"
    assert structured[1]["options"]["generation_id"] == "generation-test"
    stored = operations.get_job(db_path, job["job_id"])
    assert stored["details"]["generation_id"] == "generation-test"
    assert [item["state"] for item in stored["items"]] == ["committing", "failed"]


def test_versioned_batch_rejects_mismatched_item_order(client, tmp_path, mocker):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_edit.config.DB_PATH", db_path)
    job, _ = operations.create_job(db_path, kind="edit", item_ids=["photo-1"])

    response = client.post(
        "/style_edit",
        data={
            "image": (io.BytesIO(b"first"), "one.jpg"),
            "photo_id": "photo-1",
            "job_id": job["job_id"],
            "items_json": json.dumps([{"photo_id": "photo-other"}]),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "multipart order" in response.get_json()["error"]


def test_versioned_batch_retry_uses_job_pinned_generation(client, tmp_path, mocker):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_edit.config.DB_PATH", db_path)
    job, _ = operations.create_job(
        db_path,
        kind="edit",
        item_ids=["photo-1"],
        details={"generation_id": "generation-old"},
    )
    run_batch = mocker.patch(
        "routes.style_edit._run_coherent_style_edit_batch",
        return_value=(
            [{"status": "success", "photo_id": "photo-1", "confidence": 0.9}],
            {"tier_counts": {"independent": 1}},
        ),
    )

    response = client.post(
        "/style_edit",
        data={
            "image": (io.BytesIO(b"first"), "one.jpg"),
            "photo_id": "photo-1",
            "job_id": job["job_id"],
            "items_json": json.dumps([{"photo_id": "photo-1"}]),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert (
        run_batch.call_args.args[0][0]["options"]["generation_id"] == "generation-old"
    )
    assert (
        operations.get_job(db_path, job["job_id"])["details"]["generation_id"]
        == "generation-old"
    )


@pytest.mark.parametrize(
    ("items_json", "expected"),
    (
        ("not-json", "valid JSON"),
        (
            json.dumps([{"photo_id": "photo-1", "profile_mode": "sometimes"}]),
            "profile_mode",
        ),
    ),
)
def test_versioned_batch_rejects_malformed_contracts(
    client, tmp_path, mocker, items_json, expected
):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_edit.config.DB_PATH", db_path)
    job, _ = operations.create_job(db_path, kind="edit", item_ids=["photo-1"])

    response = client.post(
        "/style_edit",
        data={
            "image": (io.BytesIO(b"first"), "one.jpg"),
            "photo_id": "photo-1",
            "job_id": job["job_id"],
            "items_json": items_json,
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert expected in response.get_json()["error"]


def test_versioned_batch_enforces_item_and_image_byte_bounds(client, tmp_path, mocker):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_edit.config.DB_PATH", db_path)
    job, _ = operations.create_job(
        db_path, kind="edit", item_ids=["photo-1", "photo-2"]
    )
    mocker.patch("routes.style_edit.config.STYLEAI_INDEX_QUEUE_CAPACITY", 1)
    response = client.post(
        "/style_edit",
        data={
            "image": [
                (io.BytesIO(b"one"), "one.jpg"),
                (io.BytesIO(b"two"), "two.jpg"),
            ],
            "photo_id": ["photo-1", "photo-2"],
            "job_id": job["job_id"],
            "items_json": json.dumps(
                [{"photo_id": "photo-1"}, {"photo_id": "photo-2"}]
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "at most 1" in response.get_json()["error"]

    mocker.patch("routes.style_edit.config.STYLEAI_INDEX_QUEUE_CAPACITY", 32)
    mocker.patch("routes.style_edit.config.STYLEAI_METADATA_CACHE_BYTES", 4)
    response = client.post(
        "/style_edit",
        data={
            "image": (io.BytesIO(b"large"), "one.jpg"),
            "photo_id": "photo-1",
            "job_id": job["job_id"],
            "items_json": json.dumps([{"photo_id": "photo-1"}]),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "image-byte limit" in response.get_json()["error"]


def test_style_edit_updates_durable_operation_item(client, mocker, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_edit.config.DB_PATH", db_path)
    job, _ = operations.create_job(db_path, kind="edit", item_ids=["photo-1"])
    mocker.patch(
        "routes.style_edit._run_single_style_edit",
        return_value={
            "status": "success",
            "photo_id": "photo-1",
            "engine": "policy_v2",
            "confidence": 0.9,
        },
    )

    response = client.post(
        "/style_edit",
        data={
            "image": (io.BytesIO(b"fakejpeg"), "test.jpg"),
            "photo_id": "photo-1",
            "job_id": job["job_id"],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    stored = operations.get_job(db_path, job["job_id"])
    assert stored["items"][0]["state"] == "committing"


def test_style_edit_honors_scoped_operation_cancel(client, mocker, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_edit.config.DB_PATH", db_path)
    job, _ = operations.create_job(db_path, kind="edit", item_ids=["photo-1"])
    operations.request_cancel(db_path, job["job_id"])
    run_edit = mocker.patch("routes.style_edit._run_single_style_edit")

    response = client.post(
        "/style_edit",
        data={
            "image": (io.BytesIO(b"fakejpeg"), "test.jpg"),
            "photo_id": "photo-1",
            "job_id": job["job_id"],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert response.get_json()["error"] == "canceled"
    run_edit.assert_not_called()


def test_low_confidence_policy_abstains_without_generative_fallback(mocker):
    source_metrics = {key: 0.0 for key in SOURCE_METRIC_KEYS}
    mocker.patch(
        "routes.style_edit._get_canonical_source_embedding",
        return_value=(
            [1.0, 0.0],
            {"source_embedding_provenance": "raw_preview"},
            source_metrics,
        ),
    )
    mocker.patch(
        "routes.style_edit.style_engine.generate_style_edit",
        return_value=StyleEngineResult(
            recipe={"global": {"exposure": 0.25}},
            confidence=0.1,
            matched_count=3,
            engine="policy_v2",
        ),
    )
    metadata_service = mocker.patch("services.metadata.get_analysis_service")

    from routes.style_edit import _run_single_style_edit

    result = _run_single_style_edit(
        "photo-1",
        b"jpeg",
        "photo.jpg",
        {},
        camera_profile="Adobe Color",
    )

    assert result["engine"] == "none"
    assert result["error"] == "low_confidence"
    assert result["source_embedding_cache_hit"] is True
    metadata_service.assert_not_called()


def test_cache_miss_computes_once_and_cache_failure_does_not_fail_edit(mocker):
    from services import source_embeddings
    from routes.style_edit import _run_single_style_edit

    metrics = {key: 0.0 for key in SOURCE_METRIC_KEYS}
    source = source_embeddings.NeutralSource(
        image_bytes=b"neutral-preview",
        provenance=source_embeddings.RAW_PREVIEW_PROVENANCE,
        fingerprint="source-fingerprint",
    )
    mocker.patch(
        "routes.style_edit._get_canonical_source_embedding",
        return_value=(None, {}, None),
    )
    mocker.patch(
        "routes.style_edit.source_embeddings.resolve_neutral_source",
        return_value=source,
    )
    mocker.patch(
        "services.training.compute_exposure_metrics",
        return_value=metrics,
    )
    image = mocker.MagicMock()
    mocker.patch(
        "routes.style_edit.source_embeddings.decode_for_embedding",
        return_value=image,
    )
    mocker.patch("server_lifecycle.get_model", return_value=mocker.MagicMock())
    mocker.patch("server_lifecycle.get_processor", return_value=mocker.MagicMock())
    analysis = mocker.MagicMock()
    analysis._generate_image_embeddings.return_value = [[1.0, 0.0]]
    mocker.patch("services.metadata.get_analysis_service", return_value=analysis)
    cache = mocker.patch(
        "routes.style_edit._cache_canonical_source_embedding",
        side_effect=RuntimeError("cache unavailable"),
    )
    mocker.patch(
        "routes.style_edit.style_engine.generate_style_edit",
        return_value=StyleEngineResult(
            recipe={"global": {"exposure": 0.25}},
            confidence=0.1,
            matched_count=3,
            engine="policy_v2",
        ),
    )

    result = _run_single_style_edit(
        "photo-1",
        b"rendered-preview",
        "photo.jpg",
        {"raw_filepath": "/photos/photo.raw"},
        camera_profile="Adobe Color",
    )

    assert result["error"] == "low_confidence"
    assert result["source_embedding_cache_hit"] is False
    assert "embedding_inference" in result["timings_ms"]
    assert "embedding_cache_write" in result["timings_ms"]
    cache.assert_called_once()
    image.close.assert_called_once()


def test_batch_source_cache_misses_use_one_embedding_call(mocker):
    from routes.style_edit import _prepare_batch_source_embeddings
    from services import source_embeddings

    mocker.patch(
        "routes.style_edit._get_canonical_source_embedding",
        return_value=(None, {}, None),
    )
    sources = [
        source_embeddings.NeutralSource(b"raw-one", "raw_preview", "one"),
        source_embeddings.NeutralSource(b"raw-two", "raw_preview", "two"),
    ]
    mocker.patch(
        "routes.style_edit.source_embeddings.resolve_neutral_source",
        side_effect=sources,
    )
    metrics = {key: 0.0 for key in SOURCE_METRIC_KEYS}
    mocker.patch(
        "routes.style_edit.training_service.compute_exposure_metrics",
        return_value=metrics,
    )
    images = [mocker.MagicMock(), mocker.MagicMock()]
    mocker.patch(
        "routes.style_edit.source_embeddings.decode_for_embedding",
        side_effect=images,
    )
    mocker.patch("server_lifecycle.get_model", return_value=mocker.MagicMock())
    mocker.patch("server_lifecycle.get_processor", return_value=mocker.MagicMock())
    analysis = mocker.MagicMock()
    analysis._generate_image_embeddings.return_value = [[1.0, 0.0], [0.9, 0.1]]
    mocker.patch("services.metadata.get_analysis_service", return_value=analysis)
    mocker.patch(
        "routes.style_edit.operations.JobCancelSignal.is_set", return_value=False
    )
    cache = mocker.patch("routes.style_edit._cache_canonical_source_embedding")
    items = [
        {
            "photo_id": "photo-1",
            "image_bytes": b"rendered-one",
            "filename": "one.jpg",
            "options": {"raw_filepath": "/photos/one.cr3"},
        },
        {
            "photo_id": "photo-2",
            "image_bytes": b"rendered-two",
            "filename": "two.jpg",
            "options": {"raw_filepath": "/photos/two.cr3"},
        },
    ]

    prepared = _prepare_batch_source_embeddings(items, "operation-1")

    analysis._generate_image_embeddings.assert_called_once()
    assert [item["embedding"] for item in prepared] == [
        [1.0, 0.0],
        [0.9, 0.1],
    ]
    assert cache.call_count == 2
    for image in images:
        image.close.assert_called_once()


@pytest.mark.parametrize(
    ("member_tier", "expected_exposure"),
    (("policy_coherent", -0.25), ("global_target_reuse", 0.5)),
)
def test_batch_coherence_keeps_member_prediction_or_reuses_only_safe_target(
    mocker, member_tier, expected_exposure
):
    from routes.style_edit import _run_coherent_style_edit_batch
    from services import edit_burst_coherence

    metrics = {key: 0.25 for key in SOURCE_METRIC_KEYS}
    evidence = [
        edit_burst_coherence.BurstEvidence(
            photo_id=photo_id,
            capture_time=100.0 + index * 0.1,
            embedding=(1.0, index * 0.01),
            source_provenance="raw_preview",
            source_metrics=metrics,
            camera_make="Canon",
            camera_model="R5",
            camera_profile="Adobe Color",
            hard_partition_key="sdr|adobe color",
            policy_id="policy-1",
            confidence=0.9,
            entropy=0.1,
        )
        for index, photo_id in enumerate(("representative", "member"))
    ]
    representative_result = StyleEngineResult(
        recipe={"global": {"exposure": 1.0, "temperature": 7000.0}},
        confidence=0.9,
        matched_count=10,
        policy_id="policy-1",
        hard_partition_key="sdr|adobe color",
        absolute_target={"exposure": 1.0, "temperature": 7000.0},
    )
    member_result = StyleEngineResult(
        recipe={
            "global": {
                "exposure": -0.25,
                "temperature": 5100.0,
                "crop": {"angle": 0.5},
            }
        },
        confidence=0.9,
        matched_count=10,
        policy_id="policy-1",
        hard_partition_key="sdr|adobe color",
        absolute_target={
            "exposure": -0.5,
            "temperature": 5200.0,
            "crop": {"angle": 1.0},
        },
    )
    payloads = [
        {
            "status": "success",
            "photo_id": "representative",
            "recipe": representative_result.recipe,
            "_style_result": representative_result,
            "_source_evidence": evidence[0],
            "timings_ms": {},
        },
        {
            "status": "success",
            "photo_id": "member",
            "recipe": member_result.recipe,
            "_style_result": member_result,
            "_source_evidence": evidence[1],
            "timings_ms": {},
        },
    ]
    mocker.patch(
        "routes.style_edit._prepare_batch_source_embeddings",
        return_value=[
            {"embedding": [1.0, 0.0], "source_metrics": metrics},
            {"embedding": [1.0, 0.01], "source_metrics": metrics},
        ],
    )
    mocker.patch(
        "routes.style_edit.edit_burst_coherence.representative_first_order",
        return_value=[0, 1],
    )
    mocker.patch("routes.style_edit._run_single_style_edit_core", side_effect=payloads)
    decisions = {
        "representative": edit_burst_coherence.BurstDecision(
            photo_id="representative",
            group_id="edit-burst:one",
            representative_photo_id="representative",
            group_size=2,
        ),
        "member": edit_burst_coherence.BurstDecision(
            photo_id="member",
            tier=member_tier,
            fallback_reason=None,
            group_id="edit-burst:one",
            representative_photo_id="representative",
            group_size=2,
        ),
    }
    mocker.patch(
        "routes.style_edit.edit_burst_coherence.decide_reuse_tiers",
        return_value=(decisions, {"tier_counts": {member_tier: 1}}),
    )
    mocker.patch(
        "routes.style_edit.operations.pressure_snapshot",
        return_value={"level": "normal"},
    )
    persist = mocker.patch("routes.style_edit._persist_deferred_style_edit")
    items = [
        {
            "photo_id": "representative",
            "image_bytes": b"one",
            "filename": "one.jpg",
            "options": {"current_settings": {}, "style_strength": 0.5},
        },
        {
            "photo_id": "member",
            "image_bytes": b"two",
            "filename": "two.jpg",
            "options": {
                "current_settings": {
                    "Exposure2012": 0.0,
                    "Temperature": 5000.0,
                    "CropAngle": 0.0,
                },
                "style_strength": 0.5,
            },
        },
    ]

    _run_coherent_style_edit_batch(items, job_id="operation-1")

    assert member_result.recipe["global"]["exposure"] == expected_exposure
    assert member_result.recipe["global"]["temperature"] == 5100.0
    assert member_result.recipe["global"]["crop"]["angle"] == 0.5
    assert (
        persist.call_args_list[1].kwargs["burst_provenance"]["selected_tier"]
        == member_tier
    )


@pytest.mark.parametrize(
    ("allow_crop", "allow_rotate", "expected"),
    (
        (True, True, {"left": 0.1, "right": 0.9, "angle": 2.0}),
        (True, False, {"left": 0.1, "right": 0.9}),
        (False, True, {"angle": 2.0}),
        (False, False, None),
    ),
)
def test_crop_and_rotation_permissions_are_independent(
    allow_crop,
    allow_rotate,
    expected,
):
    from routes.style_edit import _filter_recipe_crop_rotate

    recipe = {
        "global": {
            "crop": {
                "left": 0.1,
                "right": 0.9,
                "angle": 2.0,
            }
        }
    }

    _filter_recipe_crop_rotate(
        recipe,
        {
            "allow_auto_crop": allow_crop,
            "allow_auto_rotate": allow_rotate,
        },
    )

    assert recipe["global"].get("crop") == expected


def test_record_application_confirmation(client, mocker):
    record = mocker.patch(
        "routes.style_edit.edit_history.record_application",
        return_value={"event_id": "event-1", "event_kind": "apply_confirmed"},
    )
    response = client.post(
        "/style_edit/events/application",
        json={
            "events": [
                {
                    "edit_inference_id": "inference-1",
                    "idempotency_key": "application:inference-1",
                    "status": "apply_confirmed",
                    "current_settings": {"Exposure2012": 0.5},
                    "global_applied": True,
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.get_json()["results"]["stored"] == 1
    assert record.call_args.kwargs["current_settings"] == {"Exposure2012": 0.5}


def test_record_application_rejects_unbounded_batch(client):
    response = client.post(
        "/style_edit/events/application",
        json={"events": [{}] * 251},
    )
    assert response.status_code == 400


def test_reconcile_selected_edit_states(client, mocker):
    reconcile = mocker.patch(
        "routes.style_edit.edit_history.reconcile_photo_state",
        return_value={
            "photo_id": "photo-1",
            "inference_id": "inference-1",
            "state": "reverted",
            "recorded": True,
        },
    )
    response = client.post(
        "/style_edit/events/reconcile",
        json={
            "items": [
                {
                    "photo_id": "photo-1",
                    "current_settings": {"Exposure2012": 0.0},
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.get_json()["results"]["photos"][0]["state"] == "reverted"
    assert reconcile.call_args.kwargs["photo_id"] == "photo-1"


def test_reconciliation_rejects_unbounded_batch(client):
    response = client.post(
        "/style_edit/events/reconcile",
        json={"items": [{}] * 101},
    )
    assert response.status_code == 400


def test_record_explicit_edit_outcomes(client, mocker):
    record = mocker.patch(
        "routes.style_edit.edit_history.record_user_outcome",
        return_value={
            "photo_id": "photo-1",
            "inference_id": "inference-1",
            "outcome": "modified_and_kept",
            "state": "diverged",
            "recorded": True,
        },
    )
    response = client.post(
        "/style_edit/events/outcomes",
        json={
            "items": [
                {
                    "edit_inference_id": "inference-1",
                    "outcome": "modified_and_kept",
                    "current_settings": {"Exposure2012": 0.4},
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.get_json()["results"]["stored"] == 1
    assert record.call_args.kwargs["outcome"] == "modified_and_kept"


def test_edit_outcomes_reject_unbounded_batch(client):
    response = client.post(
        "/style_edit/events/outcomes",
        json={"items": [{}] * 101},
    )
    assert response.status_code == 400


def test_edit_outcomes_report_per_item_validation_failures(client, mocker):
    mocker.patch(
        "routes.style_edit.edit_history.record_user_outcome",
        side_effect=ValueError("use modified_and_kept"),
    )
    response = client.post(
        "/style_edit/events/outcomes",
        json={
            "items": [
                {
                    "edit_inference_id": "inference-1",
                    "outcome": "accepted",
                    "current_settings": {"Exposure2012": 0.4},
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["results"]["stored"] == 0
    assert payload["results"]["failed"] == 1
    assert payload["results"]["failures"][0]["error"] == "use modified_and_kept"
