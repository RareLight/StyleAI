import base64
import io
from types import SimpleNamespace

from PIL import Image

from providers.base import MetadataGenerationResponse
from services.metadata_benchmark import (
    inspect_proxy,
    round_benchmark_timings,
    run_benchmark_batch,
)
from styleai_server import app


def _jpeg_bytes(color=(20, 40, 60)):
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), color).save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def _options():
    return {
        "provider": "ollama",
        "model": "vision:test",
        "language": "English",
        "temperature": 0.1,
        "generate_keywords": True,
        "generate_caption": True,
        "generate_title": True,
        "generate_alt_text": True,
        "submit_keywords": False,
        "submit_folder_names": False,
    }


def test_inspect_proxy_returns_reproducible_non_pixel_provenance():
    image = _jpeg_bytes()

    first = inspect_proxy(image)
    second = inspect_proxy(image)

    assert first == second
    assert first["width"] == 12
    assert first["height"] == 8
    assert first["byte_count"] == len(image)
    assert len(first["sha256"]) == 64
    assert "image" not in first


def test_benchmark_service_returns_outputs_without_index_service(mocker):
    analysis = mocker.Mock()
    analysis.providers = {"ollama": object()}
    analysis.generate_metadata_single.return_value = MetadataGenerationResponse(
        uuid="photo-1",
        success=True,
        keywords=["Dog", "Golden Retriever"],
        title="Running Retriever",
        caption="A retriever runs through a park.",
        alt_text="A golden retriever runs across a green park.",
        input_tokens=120,
        output_tokens=30,
        retry_count=1,
        timing={"inference_ms": 250.0},
        inference={
            "used_draft_model": "draft:test",
            "total_draft_tokens": 20,
            "accepted_draft_tokens": 15,
        },
    )

    results = run_benchmark_batch(
        [
            {
                "photo_id": "photo-1",
                "source_photo_id": "stable-photo-1",
                "filename": "one.jpg",
                "image_data": _jpeg_bytes(),
                "decode_ms": 1.25,
            }
        ],
        _options(),
        analysis_service=analysis,
    )

    assert results[0]["status"] == "succeeded"
    assert results[0]["keywords"] == ["Dog", "Golden Retriever"]
    assert results[0]["retry_count"] == 1
    assert results[0]["timing"]["inference_ms"] == 250
    assert results[0]["timing"]["base64_decode_ms"] == 1
    assert results[0]["timing"]["proxy_inspection_ms"] >= 0
    assert results[0]["timing"]["benchmark_item_total_ms"] >= 0
    assert results[0]["source_photo_id"] == "stable-photo-1"
    assert results[0]["benchmark_variant"] == "baseline"
    assert results[0]["inference"]["accepted_draft_tokens"] == 15
    assert results[0]["proxy"]["width"] == 12
    analysis.generate_metadata_single.assert_called_once()


def test_benchmark_timing_precision_uses_units():
    assert round_benchmark_timings(
        {
            "inference_ms": 123.6,
            "provider_seconds": 12.25,
            "tokens_per_second": 7.654,
            "photos_per_minute": 3.456,
            "input_tokens": 42,
        }
    ) == {
        "inference_ms": 124,
        "provider_seconds": 12.3,
        "tokens_per_second": 7.7,
        "photos_per_minute": 3.5,
        "input_tokens": 42,
    }


def test_benchmark_service_publishes_each_item_before_inference(mocker):
    analysis = mocker.Mock()
    analysis.providers = {"ollama": object()}
    analysis.generate_metadata_single.return_value = MetadataGenerationResponse(
        uuid="photo", success=True
    )
    started = []
    items = [
        {"photo_id": "photo-1", "image_data": _jpeg_bytes()},
        {"photo_id": "photo-2", "image_data": _jpeg_bytes()},
    ]

    run_benchmark_batch(
        items,
        _options(),
        analysis_service=analysis,
        on_item_started=lambda item, index, total: started.append(
            (item["photo_id"], index, total)
        ),
    )

    assert started == [("photo-1", 1, 2), ("photo-2", 2, 2)]


def test_benchmark_service_honors_scoped_cancellation():
    cancel_signal = SimpleNamespace(is_set=lambda: True)

    try:
        run_benchmark_batch(
            [
                {
                    "photo_id": "photo-1",
                    "filename": "one.jpg",
                    "image_data": _jpeg_bytes(),
                }
            ],
            _options(),
            analysis_service=SimpleNamespace(providers={"ollama": object()}),
            cancel_signal=cancel_signal,
        )
    except InterruptedError as exc:
        assert "canceled" in str(exc)
    else:
        raise AssertionError("benchmark service ignored cancellation")


def test_benchmark_service_does_not_fallback_to_another_provider():
    analysis = SimpleNamespace(providers={"lmstudio": object()})

    try:
        run_benchmark_batch([], _options(), analysis_service=analysis)
    except ValueError as exc:
        assert "provider is unavailable" in str(exc)
    else:
        raise AssertionError("benchmark accepted an unavailable provider")


def test_benchmark_route_returns_standard_non_persisting_results(mocker):
    app.config["TESTING"] = True
    generated = [
        {
            "photo_id": "photo-1",
            "filename": "one.jpg",
            "status": "succeeded",
            "provider": "ollama",
            "model": "vision:test",
            "proxy": {"sha256": "a" * 64, "byte_count": 10},
            "keywords": ["Dog"],
            "title": "Dog",
            "caption": "A dog.",
            "alt_text": "A dog outdoors.",
            "input_tokens": 10,
            "output_tokens": 4,
            "retry_count": 0,
            "timing": {"benchmark_item_total_ms": 20.0},
        }
    ]
    run = mocker.patch(
        "routes.metadata_benchmark.run_benchmark_batch", return_value=generated
    )
    payload = base64.b64encode(_jpeg_bytes()).decode("ascii")

    with app.test_client() as client:
        response = client.post(
            "/metadata_benchmark/run_batch",
            json={
                "tasks": [
                    {
                        "photo_id": "photo-1",
                        "source_photo_id": "stable-photo-1",
                        "filename": "one.jpg",
                        "image": payload,
                        "options": {
                            "provider": "lmstudio",
                            "model": "wrong-model",
                            "user_context": "snowy trail",
                        },
                    }
                ],
                "options": {
                    **_options(),
                    "temperature": "0.1",
                },
            },
        )

    assert response.status_code == 200
    body = response.get_json()
    assert body["error"] is None
    assert body["results"]["success_count"] == 1
    assert body["results"]["items"][0]["keywords"] == ["Dog"]
    assert body["results"]["items"][0]["timing"]["admission_wait_ms"] >= 0
    run.assert_called_once()
    decoded_item = run.call_args.args[0][0]
    assert decoded_item["source_photo_id"] == "stable-photo-1"
    assert decoded_item["decode_ms"] >= 0
    assert decoded_item["options"]["provider"] == "ollama"
    assert decoded_item["options"]["model"] == "vision:test"
    assert decoded_item["options"]["user_context"] == "snowy trail"


def test_operation_backed_route_persists_status_but_not_generated_metadata(mocker):
    app.config["TESTING"] = True
    payload = base64.b64encode(_jpeg_bytes()).decode("ascii")
    result = {
        "photo_id": "photo-1",
        "source_photo_id": "stable-1",
        "filename": "one.jpg",
        "status": "succeeded",
        "provider": "ollama",
        "model": "vision:test",
        "proxy": {"sha256": "a" * 64, "byte_count": 10},
        "keywords": ["Private Output"],
        "title": "Private Title",
        "caption": "Private Caption",
        "alt_text": "Private Alt Text",
        "input_tokens": 10,
        "output_tokens": 4,
        "retry_count": 0,
        "timing": {},
    }
    mocker.patch("routes.metadata_benchmark.config.DB_PATH", "/catalog/styleai.db")
    mocker.patch(
        "routes.metadata_benchmark.operations.get_job",
        return_value={
            "job_id": "job-1",
            "kind": "metadata_benchmark",
            "state": "queued",
            "cancel_requested": False,
        },
    )
    mocker.patch(
        "routes.metadata_benchmark.operations.get_job_items",
        return_value=[{"item_id": "model::photo-1"}],
    )
    mocker.patch(
        "routes.metadata_benchmark.operations.JobCancelSignal",
        return_value=SimpleNamespace(is_set=lambda: False),
    )
    set_job = mocker.patch("routes.metadata_benchmark.operations.set_job_state")
    set_items = mocker.patch("routes.metadata_benchmark.operations.set_item_states")

    def run_with_progress(items, options, **kwargs):
        kwargs["on_item_started"](items[0], 1, 1)
        return [result]

    mocker.patch(
        "routes.metadata_benchmark.run_benchmark_batch", side_effect=run_with_progress
    )
    set_item = mocker.patch("routes.metadata_benchmark.operations.set_item_state")

    with app.test_client() as client:
        response = client.post(
            "/metadata_benchmark/run_batch",
            json={
                "job_id": "job-1",
                "tasks": [
                    {
                        "photo_id": "photo-1",
                        "source_photo_id": "stable-1",
                        "operation_item_id": "model::photo-1",
                        "model_index": 2,
                        "photo_index": 14,
                        "filename": "one.jpg",
                        "image": payload,
                    }
                ],
                "options": _options(),
            },
        )

    assert response.status_code == 200
    terminal_updates = set_items.call_args_list[-1].args[2]
    assert terminal_updates == [
        {"item_id": "model::photo-1", "state": "succeeded", "error": None}
    ]
    set_item.assert_called_once_with(
        "/catalog/styleai.db", "job-1", "model::photo-1", "running"
    )
    assert set_job.call_args_list[-1].kwargs["details"] == {
        "current_model_index": 2,
        "current_photo_index": 14,
    }
    assert "Private Output" in response.get_data(as_text=True)


def test_benchmark_route_rejects_duplicate_photos_and_oversized_batches(mocker):
    app.config["TESTING"] = True
    payload = base64.b64encode(_jpeg_bytes()).decode("ascii")
    task = {"photo_id": "same", "filename": "one.jpg", "image": payload}
    options = _options()
    mocker.patch("routes.metadata_benchmark.run_benchmark_batch")

    with app.test_client() as client:
        duplicate = client.post(
            "/metadata_benchmark/run_batch",
            json={"tasks": [task, task], "options": options},
        )
        oversized = client.post(
            "/metadata_benchmark/run_batch",
            json={
                "tasks": [
                    {**task, "photo_id": f"photo-{index}"} for index in range(13)
                ],
                "options": options,
            },
        )

    assert duplicate.status_code == 400
    assert "duplicate" in duplicate.get_json()["error"]
    assert oversized.status_code == 413
    assert "limited to 12" in oversized.get_json()["error"]


def test_benchmark_route_validates_speculative_pairing():
    app.config["TESTING"] = True
    payload = base64.b64encode(_jpeg_bytes()).decode("ascii")
    task = {"photo_id": "photo-1", "image": payload}

    with app.test_client() as client:
        unsupported_provider = client.post(
            "/metadata_benchmark/run_batch",
            json={
                "tasks": [task],
                "options": {
                    **_options(),
                    "draft_model": "draft:test",
                    "benchmark_variant": "speculative",
                },
            },
        )
        missing_draft = client.post(
            "/metadata_benchmark/run_batch",
            json={
                "tasks": [task],
                "options": {
                    **_options(),
                    "provider": "lmstudio",
                    "benchmark_variant": "speculative",
                },
            },
        )

    assert unsupported_provider.status_code == 400
    assert "only for LM Studio" in unsupported_provider.get_json()["error"]
    assert missing_draft.status_code == 400
    assert "require draft_model" in missing_draft.get_json()["error"]
