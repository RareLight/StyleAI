import base64
import queue
import pytest

from core.migrations import run_migrations
from styleai_server import app
from services import index as index_service
from services import image_cache
from services import operations


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def index_operation_db(tmp_path, mocker):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.index.config.DB_PATH", db_path)
    return db_path


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
    assert payload == {
        "status": "backpressure",
        "enqueued": 0,
        "rejected": 1,
        "accepted_photo_ids": [],
        "rejected_items": [
            {
                "photo_id": "rejected",
                "reason": "index queue is full",
                "retryable": True,
            }
        ],
    }


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
        "accepted_photo_ids": [],
        "rejected_items": [
            {
                "photo_id": "a",
                "reason": "metadata image cache is full",
                "retryable": True,
            }
        ],
    }
    assert bounded_queue.empty()


def test_index_queue_reports_interleaved_admission_by_photo_id(client, monkeypatch):
    bounded_queue = queue.Queue(maxsize=3)
    monkeypatch.setattr(index_service, "index_queue", bounded_queue)
    index_service._index_queue_accepting.set()
    admitted = {"a", "c"}
    monkeypatch.setattr(
        image_cache,
        "store_image",
        lambda photo_id, _data: photo_id in admitted,
    )
    encoded = base64.b64encode(b"image").decode("ascii")

    response = client.post(
        "/index_queue",
        json={
            "images": [
                {"image": encoded, "photo_id": photo_id, "filename": f"{photo_id}.jpg"}
                for photo_id in ("a", "b", "c")
            ],
            "options": {"cache_images": True},
        },
    )

    assert response.status_code == 202
    payload = response.get_json()["results"]
    assert payload["accepted_photo_ids"] == ["a", "c"]
    assert payload["enqueued"] == 2
    assert payload["rejected"] == 1
    assert payload["rejected_items"] == [
        {
            "photo_id": "b",
            "reason": "metadata image cache is full",
            "retryable": True,
        }
    ]
    assert [bounded_queue.get_nowait()["uuid"] for _ in range(2)] == ["a", "c"]
    index_service.active_embeddings_uuids.difference_update(admitted)


def test_index_queue_terminal_rejection_updates_exact_operation_item(
    client, monkeypatch, index_operation_db
):
    bounded_queue = queue.Queue(maxsize=2)
    monkeypatch.setattr(index_service, "index_queue", bounded_queue)
    index_service._index_queue_accepting.set()
    job, _ = operations.create_job(
        index_operation_db, kind="index", item_ids=["invalid-photo"]
    )

    response = client.post(
        "/index_queue",
        json={
            "job_id": job["job_id"],
            "images": [{"photo_id": "invalid-photo", "filename": "invalid.jpg"}],
        },
    )

    assert response.status_code == 202
    payload = response.get_json()["results"]
    assert payload["accepted_photo_ids"] == []
    assert payload["rejected_items"] == [
        {
            "photo_id": "invalid-photo",
            "reason": "image data is required",
            "retryable": False,
        }
    ]
    stored = operations.get_job(index_operation_db, job["job_id"])
    assert stored["items"][0]["state"] == "failed"


def test_index_queue_updates_only_predeclared_operation_items(
    client, monkeypatch, index_operation_db
):
    bounded_queue = queue.Queue(maxsize=2)
    monkeypatch.setattr(index_service, "index_queue", bounded_queue)
    index_service._index_queue_accepting.set()
    job, _ = operations.create_job(index_operation_db, kind="index", item_ids=["p1"])
    encoded = base64.b64encode(b"image").decode("ascii")

    rejected = client.post(
        "/index_queue",
        json={
            "job_id": job["job_id"],
            "images": [{"image": encoded, "photo_id": "p2", "filename": "p2.jpg"}],
        },
    )
    accepted = client.post(
        "/index_queue",
        json={
            "job_id": job["job_id"],
            "images": [{"image": encoded, "photo_id": "p1", "filename": "p1.jpg"}],
        },
    )

    assert rejected.status_code == 400
    assert accepted.status_code == 202
    stored = operations.get_job(index_operation_db, job["job_id"])
    assert stored["state"] == "running"
    assert stored["items"][0]["state"] == "queued"
    queued = bounded_queue.get_nowait()
    assert queued["job_id"] == job["job_id"]
    bounded_queue.task_done()
    index_service.active_embeddings_uuids.discard("p1")


def test_index_queue_validates_only_submitted_operation_items(
    client, monkeypatch, mocker, index_operation_db
):
    bounded_queue = queue.Queue(maxsize=2)
    monkeypatch.setattr(index_service, "index_queue", bounded_queue)
    index_service._index_queue_accepting.set()
    job, _ = operations.create_job(
        index_operation_db,
        kind="index",
        item_ids=["p1", *[f"other-{index}" for index in range(100)]],
    )
    get_job = mocker.spy(operations, "get_job")
    get_items = mocker.spy(operations, "get_job_items")

    response = client.post(
        "/index_queue",
        json={
            "job_id": job["job_id"],
            "images": [
                {
                    "image": base64.b64encode(b"image").decode("ascii"),
                    "photo_id": "p1",
                    "filename": "p1.jpg",
                }
            ],
        },
    )

    assert response.status_code == 202
    assert get_job.call_args_list[0].kwargs == {"include_items": False}
    get_items.assert_called_once_with(index_operation_db, job["job_id"], ["p1"])
    bounded_queue.get_nowait()
    bounded_queue.task_done()
    index_service.active_embeddings_uuids.discard("p1")


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


def test_metadata_batch_reports_terminal_status_for_each_photo(client, mocker):
    image_cache.clear()

    def process(_triplets, *, options, item_results):
        item_results.extend(
            [
                {"photo_id": "good", "filename": "good.jpg", "status": "succeeded"},
                {
                    "photo_id": "bad",
                    "filename": "bad.jpg",
                    "status": "failed",
                    "error": "provider failed",
                },
            ]
        )
        return 1, 1, ["bad.jpg: provider failed"], []

    mocker.patch("routes.index.process_image_task", side_effect=process)

    response = client.post(
        "/metadata/generate_batch",
        json={
            "tasks": [
                {
                    "photo_id": "good",
                    "filename": "good.jpg",
                    "image": base64.b64encode(b"good image").decode("ascii"),
                },
                {
                    "photo_id": "bad",
                    "filename": "bad.jpg",
                    "image": base64.b64encode(b"bad image").decode("ascii"),
                },
            ]
        },
    )

    assert response.status_code == 200
    payload = response.get_json()["results"]
    assert payload["success_count"] == 1
    assert payload["failure_count"] == 1
    assert payload["items"] == [
        {"photo_id": "good", "filename": "good.jpg", "status": "succeeded"},
        {
            "photo_id": "bad",
            "filename": "bad.jpg",
            "status": "failed",
            "error": "provider failed",
        },
    ]


def test_metadata_batch_reports_expected_cancellation_without_http_error(
    client, mocker
):
    image_cache.clear()

    def process(_triplets, *, options, item_results):
        item_results.append(
            {
                "photo_id": "canceled",
                "filename": "canceled.jpg",
                "status": "canceled",
                "error": "operation job has been canceled",
            }
        )
        return 0, 0, [], []

    mocker.patch("routes.index.process_image_task", side_effect=process)

    response = client.post(
        "/metadata/generate_batch",
        json={
            "tasks": [
                {
                    "photo_id": "canceled",
                    "filename": "canceled.jpg",
                    "image": base64.b64encode(b"image").decode("ascii"),
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.get_json()["results"]
    assert payload["status"] == "canceled"
    assert payload["success_count"] == 0
    assert payload["failure_count"] == 0


def test_metadata_job_waits_for_lightroom_handoff(client, mocker, index_operation_db):
    def process(_triplets, *, options, item_results):
        item_results.append({"photo_id": "p1", "status": "succeeded"})
        return 1, 0, [], []

    mocker.patch("routes.index.process_image_task", side_effect=process)
    job, _ = operations.create_job(index_operation_db, kind="index", item_ids=["p1"])

    response = client.post(
        "/metadata/generate_batch",
        json={
            "job_id": job["job_id"],
            "tasks": [
                {
                    "photo_id": "p1",
                    "filename": "p1.jpg",
                    "image": base64.b64encode(b"image").decode("ascii"),
                }
            ],
        },
    )

    assert response.status_code == 200
    stored = operations.get_job(index_operation_db, job["job_id"])
    assert stored["state"] == "running"
    assert stored["items"][0]["state"] == "committing"


def test_metadata_job_id_reaches_every_worker_option(
    client, mocker, index_operation_db
):
    captured_options = []

    def process(_triplets, *, options, item_results):
        captured_options.extend(options)
        item_results.extend(
            [
                {"photo_id": "p1", "status": "succeeded"},
                {"photo_id": "p2", "status": "succeeded"},
            ]
        )
        return 2, 0, [], []

    mocker.patch("routes.index.process_image_task", side_effect=process)
    job, _ = operations.create_job(
        index_operation_db, kind="index", item_ids=["p1", "p2"]
    )

    publish_states = mocker.spy(operations, "set_item_states")
    response = client.post(
        "/metadata/generate_batch",
        json={
            "job_id": job["job_id"],
            "tasks": [
                {
                    "photo_id": photo_id,
                    "filename": f"{photo_id}.jpg",
                    "image": base64.b64encode(b"image").decode("ascii"),
                }
                for photo_id in ("p1", "p2")
            ],
        },
    )

    assert response.status_code == 200
    assert [option["job_id"] for option in captured_options] == [
        job["job_id"],
        job["job_id"],
    ]
    published_batches = [
        call.args[2]
        for call in publish_states.call_args_list
        if len(call.args) >= 3 and call.args[1] == job["job_id"]
    ]
    assert [update["state"] for update in published_batches[0]] == [
        "running",
        "running",
    ]
    assert [update["state"] for update in published_batches[1]] == [
        "committing",
        "committing",
    ]


def test_metadata_cancel_while_waiting_for_embedding_releases_cached_image(
    client, mocker, index_operation_db
):
    image_cache.clear()
    assert image_cache.store_image("p1", b"cached")
    index_service.active_embeddings_uuids.add("p1")
    process = mocker.patch("routes.index.process_image_task")
    signal = mocker.patch("services.operations.JobCancelSignal").return_value
    signal.is_set.return_value = True
    job, _ = operations.create_job(index_operation_db, kind="index", item_ids=["p1"])

    try:
        response = client.post(
            "/metadata/generate_batch",
            json={
                "job_id": job["job_id"],
                "tasks": [{"photo_id": "p1", "filename": "p1.jpg"}],
            },
        )
    finally:
        index_service.active_embeddings_uuids.discard("p1")

    assert response.status_code == 409
    process.assert_not_called()
    assert image_cache.get_image("p1") is None
    stored = operations.get_job(index_operation_db, job["job_id"])
    assert stored["items"][0]["state"] == "canceled"


def test_metadata_job_rejects_unadmitted_photo_before_processing(
    client, mocker, index_operation_db
):
    process = mocker.patch("routes.index.process_image_task")
    job, _ = operations.create_job(index_operation_db, kind="index", item_ids=["p1"])

    response = client.post(
        "/metadata/generate_batch",
        json={
            "job_id": job["job_id"],
            "tasks": [
                {
                    "photo_id": "p2",
                    "filename": "p2.jpg",
                    "image": base64.b64encode(b"image").decode("ascii"),
                }
            ],
        },
    )

    assert response.status_code == 400
    assert "not admitted" in response.get_json()["error"]
    process.assert_not_called()


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
