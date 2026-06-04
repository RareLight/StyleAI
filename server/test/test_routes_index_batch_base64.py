import base64
import pytest

from styleai_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_base64_batch_returns_envelope(client, mocker):
    mocker.patch(
        "routes.index.process_image_task",
        return_value=(2, 0, [], []),
    )
    img_b64 = base64.b64encode(b"fake image data").decode("ascii")
    response = client.post(
        "/index_base64_batch",
        json={
            "images": [
                {
                    "image": img_b64,
                    "photo_id": "a",
                    "filename": "a.jpg",
                    "options": {"date_time_unix": 12345},
                },
                {"image": img_b64, "photo_id": "b", "filename": "b.jpg"},
            ],
            "options": {"provider": "gemini", "model": "gemini-1.5-flash"},
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "processed"
    assert payload["success_count"] == 2
    assert payload["failure_count"] == 0


def test_index_base64_batch_merges_options(client, mocker):
    mock_process = mocker.patch(
        "routes.index.process_image_task",
        return_value=(2, 0, [], []),
    )
    img_b64 = base64.b64encode(b"fake image data").decode("ascii")
    client.post(
        "/index_base64_batch",
        json={
            "images": [
                {
                    "image": img_b64,
                    "photo_id": "a",
                    "filename": "a.jpg",
                    "options": {"date_time_unix": 12345},
                },
                {"image": img_b64, "photo_id": "b", "filename": "b.jpg"},
            ],
            "options": {
                "provider": "gemini",
                "model": "gemini-1.5-flash",
                "tasks": ["metadata"],
            },
        },
    )

    assert mock_process.called
    called_args, called_kwargs = mock_process.call_args
    assert "options" in called_kwargs
    per_image_options = called_kwargs["options"]
    assert len(per_image_options) == 2

    # Image A options
    assert per_image_options[0]["date_time_unix"] == 12345
    assert per_image_options[0]["provider"] == "gemini"
    assert per_image_options[0]["compute_metadata"] is True

    # Image B options
    assert per_image_options[1]["date_time_unix"] is None
    assert per_image_options[1]["provider"] == "gemini"
    assert per_image_options[1]["compute_metadata"] is True
