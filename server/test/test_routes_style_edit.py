"""Tests for routes/style_edit.py — covers POST /style_edit."""

import io
import pytest
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
