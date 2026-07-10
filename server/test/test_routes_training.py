"""Tests for routes/training.py — covers POST /training/add, GET /training/list, GET /training/stats."""

import json
import pytest
from styleai_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


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
    assert kwargs["label"] == "Cinematic (HDR)"


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
