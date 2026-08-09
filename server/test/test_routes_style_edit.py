"""Tests for routes/style_edit.py — covers POST /style_edit."""

import io
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
