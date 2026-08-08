import base64
import queue
import pytest

from styleai_server import app
from services import index as index_service
from services import image_cache


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
                    "options": {
                        "date_time_unix": 12345,
                        "raw_filepath": "/catalog/a.raw",
                        "camera_profile": "Adobe Landscape",
                        "camera_make": "Canon",
                        "camera_model": "EOS R5",
                        "focal_length": "35",
                        "lens": "RF 35mm F1.8",
                        "iso": "400",
                        "aperture": "2.8",
                        "shutter_speed": "1/125",
                        "rating": "5",
                        "pick_status": "1",
                        "is_edited": "true",
                    },
                },
                {"image": img_b64, "photo_id": "b", "filename": "b.jpg"},
            ],
            "options": {"provider": "ollama", "model": "qwen3-vl"},
        },
    )
    assert response.status_code == 200
    _json = response.get_json()
    payload = _json.get("results") if _json.get("results") is not None else _json
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
                    "options": {
                        "date_time_unix": 12345,
                        "raw_filepath": "/catalog/a.raw",
                        "camera_profile": "Adobe Landscape",
                        "camera_make": "Canon",
                        "camera_model": "EOS R5",
                        "focal_length": "35",
                        "lens": "RF 35mm F1.8",
                        "iso": "400",
                        "aperture": "2.8",
                        "shutter_speed": "1/125",
                        "rating": "5",
                        "pick_status": "1",
                        "is_edited": "true",
                    },
                },
                {"image": img_b64, "photo_id": "b", "filename": "b.jpg"},
            ],
            "options": {
                "provider": "ollama",
                "model": "qwen3-vl",
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
    assert per_image_options[0]["provider"] == "ollama"
    assert per_image_options[0]["compute_metadata"] is True
    assert per_image_options[0]["raw_filepath"] == "/catalog/a.raw"
    assert per_image_options[0]["camera_profile"] == "Adobe Landscape"
    assert per_image_options[0]["camera_make"] == "Canon"
    assert per_image_options[0]["camera_model"] == "EOS R5"
    assert per_image_options[0]["focal_length"] == 35.0
    assert per_image_options[0]["lens"] == "RF 35mm F1.8"
    assert per_image_options[0]["iso"] == 400.0
    assert per_image_options[0]["aperture"] == 2.8
    assert per_image_options[0]["shutter_speed"] == "1/125"
    assert per_image_options[0]["rating"] == 5
    assert per_image_options[0]["pick_status"] == 1
    assert per_image_options[0]["is_edited"] is True

    # Image B options
    assert per_image_options[1]["date_time_unix"] is None
    assert per_image_options[1]["provider"] == "ollama"
    assert per_image_options[1]["compute_metadata"] is True


def test_index_queue_rejects_before_decoding_when_full(client, monkeypatch):
    bounded_queue = queue.Queue(maxsize=1)
    bounded_queue.put({"uuid": "already-queued"})
    monkeypatch.setattr(index_service, "index_queue", bounded_queue)
    index_service._index_queue_accepting.set()

    response = client.post(
        "/index_queue",
        json={
            "images": [
                {
                    "image": "not-valid-base64-because-it-must-not-be-decoded",
                    "photo_id": "rejected",
                    "filename": "rejected.jpg",
                }
            ]
        },
    )

    assert response.status_code == 202
    payload = response.get_json()["results"]
    assert payload == {"status": "backpressure", "enqueued": 0, "rejected": 1}


def test_index_queue_backpressures_when_metadata_cache_is_full(client, monkeypatch):
    bounded_queue = queue.Queue(maxsize=2)
    monkeypatch.setattr(index_service, "index_queue", bounded_queue)
    monkeypatch.setattr(image_cache, "store_image", lambda _uuid, _data: False)
    index_service._index_queue_accepting.set()
    img_b64 = base64.b64encode(b"fake image data").decode("ascii")

    response = client.post(
        "/index_queue",
        json={
            "images": [{"image": img_b64, "photo_id": "a", "filename": "a.jpg"}],
            "options": {"cache_images": True},
        },
    )

    assert response.status_code == 202
    assert response.get_json()["results"] == {
        "status": "backpressure",
        "enqueued": 0,
        "rejected": 1,
    }
    assert bounded_queue.empty()


def test_metadata_batch_missing_image_fails_without_consuming_present_image(
    client, mocker
):
    image_cache.clear()
    assert image_cache.store_image("present", b"present image")
    process = mocker.patch("routes.index.process_image_task")

    response = client.post(
        "/metadata/generate_batch",
        json={
            "tasks": [
                {"photo_id": "present", "filename": "present.jpg"},
                {"photo_id": "missing", "filename": "missing.jpg"},
            ]
        },
    )

    assert response.status_code == 409
    assert "expired or was not admitted" in response.get_json()["error"]
    assert image_cache.get_image("present") == b"present image"
    process.assert_not_called()
    image_cache.clear()


def test_metadata_batch_accepts_inline_images_without_cache(client, mocker):
    image_cache.clear()
    process = mocker.patch(
        "routes.index.process_image_task", return_value=(2, 0, [], [])
    )

    response = client.post(
        "/metadata/generate_batch",
        json={
            "tasks": [
                {
                    "photo_id": "first",
                    "filename": "first.jpg",
                    "image": base64.b64encode(b"first image").decode("ascii"),
                },
                {
                    "photo_id": "second",
                    "filename": "second.jpg",
                    "image": base64.b64encode(b"second image").decode("ascii"),
                },
            ]
        },
    )

    assert response.status_code == 200
    triplets = process.call_args.args[0]
    assert triplets == [
        (b"first image", "first", "first.jpg", None),
        (b"second image", "second", "second.jpg", None),
    ]


def test_metadata_batch_rejects_unbounded_task_count(client, mocker):
    process = mocker.patch("routes.index.process_image_task")

    response = client.post(
        "/metadata/generate_batch",
        json={
            "tasks": [
                {
                    "photo_id": f"photo-{index}",
                    "filename": f"photo-{index}.jpg",
                    "image": base64.b64encode(b"image").decode("ascii"),
                }
                for index in range(13)
            ]
        },
    )

    assert response.status_code == 413
    assert "limited to 12 photos" in response.get_json()["error"]
    process.assert_not_called()


def test_metadata_batch_supports_mixed_inline_and_cached_images(client, mocker):
    image_cache.clear()
    assert image_cache.store_image("cached", b"cached image")
    process = mocker.patch(
        "routes.index.process_image_task", return_value=(2, 0, [], [])
    )

    response = client.post(
        "/metadata/generate_batch",
        json={
            "tasks": [
                {
                    "photo_id": "inline",
                    "filename": "inline.jpg",
                    "image": base64.b64encode(b"inline image").decode("ascii"),
                },
                {"photo_id": "cached", "filename": "cached.jpg"},
            ]
        },
    )

    assert response.status_code == 200
    triplets = process.call_args.args[0]
    assert triplets == [
        (b"inline image", "inline", "inline.jpg", None),
        (b"cached image", "cached", "cached.jpg", None),
    ]
    assert image_cache.get_image("cached") is None


def test_stop_index_queue_releases_pending_images(monkeypatch):
    bounded_queue = queue.Queue(maxsize=2)
    item = {"uuid": "queued", "image_bytes": b"image"}
    bounded_queue.put(item)
    monkeypatch.setattr(index_service, "index_queue", bounded_queue)
    index_service.active_embeddings_uuids.add("queued")
    image_cache.clear()
    assert image_cache.store_image("queued", b"cached")
    index_service._index_queue_accepting.set()

    assert index_service.stop_index_queue() == 1
    assert bounded_queue.empty()
    assert item == {}
    assert "queued" not in index_service.active_embeddings_uuids
    assert image_cache.get_image("queued") is None
    assert index_service.is_index_queue_accepting() is False
    index_service._index_queue_accepting.set()


def test_index_queue_status_reports_capacity(client, monkeypatch):
    bounded_queue = queue.Queue(maxsize=3)
    bounded_queue.put({"uuid": "queued"})
    monkeypatch.setattr(index_service, "index_queue", bounded_queue)
    index_service._index_queue_accepting.set()
    index_service.active_embeddings_uuids.add("active")

    response = client.get("/index_queue/status")

    assert response.status_code == 200
    assert response.get_json()["results"] == {
        "accepting": True,
        "queued": 1,
        "capacity": 3,
        "active": 1,
    }
    index_service.active_embeddings_uuids.discard("active")
