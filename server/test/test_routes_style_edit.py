"""Tests for routes/style_edit.py — covers POST /style_edit."""

import io
from types import SimpleNamespace

import pytest

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


def test_no_policy_match_can_use_explicit_local_llm_fallback(mocker):
    mocker.patch("routes.style_edit._get_clip_embedding", return_value=[1.0, 0.0])
    mocker.patch(
        "routes.style_edit.style_engine.generate_style_edit",
        return_value=StyleEngineResult(
            recipe={},
            confidence=0.0,
            matched_count=0,
            engine="none",
            warning="No compatible policy",
        ),
    )
    query = mocker.patch(
        "services.training.query_similar_training_examples",
        return_value=[],
    )
    analysis = mocker.Mock()
    analysis.generate_edit_recipe_single.return_value = SimpleNamespace(
        success=True,
        recipe={"global": {"exposure": 0.25}},
        warning=None,
        input_tokens=10,
        output_tokens=5,
    )
    mocker.patch("services.metadata.get_analysis_service", return_value=analysis)
    mocker.patch("routes.style_edit._persist_edit_recipe")
    mocker.patch(
        "routes.style_edit._success_payload",
        return_value={"status": "ok", "recipe": {"global": {"exposure": 0.25}}},
    )

    from routes.style_edit import _run_single_style_edit

    result = _run_single_style_edit(
        "photo-1",
        b"jpeg",
        "photo.jpg",
        {},
        camera_profile="Adobe Color",
        use_llm_fallback=True,
    )

    assert result["engine"] == "llm"
    query.assert_called_once_with(
        [1.0, 0.0],
        n_results=3,
        camera_profile="Adobe Color",
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
