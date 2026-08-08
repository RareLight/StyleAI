"""Tests for routes/training.py — covers POST /training/add, GET /training/list, GET /training/stats."""

import json
import pytest
from core.migrations import run_migrations
from services import operations
from styleai_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


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
