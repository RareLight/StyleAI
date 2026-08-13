from styleai_server import app


def test_metadata_benchmark_is_an_allowed_operation_kind(mocker):
    app.config["TESTING"] = True
    created_job = {
        "job_id": "benchmark-1",
        "kind": "metadata_benchmark",
        "state": "queued",
    }
    mocker.patch("routes.operations.config.DB_PATH", "/catalog/styleai.db")
    create = mocker.patch(
        "routes.operations.operations.create_job", return_value=(created_job, True)
    )

    with app.test_client() as client:
        response = client.post(
            "/operations",
            json={
                "kind": "metadata_benchmark",
                "item_ids": ["ollama::model::photo-1"],
                "coalesce": False,
            },
        )

    assert response.status_code == 202
    assert response.get_json()["results"]["job"]["kind"] == "metadata_benchmark"
    assert create.call_args.kwargs["kind"] == "metadata_benchmark"


def test_operation_status_and_scoped_cancel(mocker):
    app.config["TESTING"] = True
    job = {
        "job_id": "job-1",
        "kind": "index",
        "state": "running",
        "cancel_requested": False,
    }
    mocker.patch("routes.operations.config.DB_PATH", "/catalog/styleai.db")
    get_job = mocker.patch("routes.operations.operations.get_job", return_value=job)
    cancel = mocker.patch(
        "routes.operations.operations.request_cancel",
        return_value={**job, "cancel_requested": True},
    )

    with app.test_client() as client:
        status_response = client.get("/operations/job-1")
        cancel_response = client.post("/operations/job-1/cancel")

    assert status_response.status_code == 200
    assert status_response.get_json()["results"]["job"]["job_id"] == "job-1"
    assert cancel_response.status_code == 202
    assert cancel_response.get_json()["results"]["job"]["cancel_requested"] is True
    get_job.assert_called_once_with("/catalog/styleai.db", "job-1", include_items=True)
    cancel.assert_called_once_with("/catalog/styleai.db", "job-1")
