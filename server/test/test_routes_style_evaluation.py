"""Tests for durable applied-edit evaluation routes."""

import pytest

from core.migrations import run_migrations
from services import operations
from styleai_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_start_applied_edit_evaluation_job(client, mocker, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_evaluation.config.DB_PATH", db_path)
    start = mocker.patch("routes.style_evaluation._start_evaluation_job")

    response = client.post(
        "/style_edit/evaluation",
        json={
            "confidence_bin_count": 5,
            "minimum_reviewed_per_generation": 30,
        },
    )

    assert response.status_code == 202
    result = response.get_json()["results"]
    assert result["state"] == "queued"
    assert result["attached"] is False
    start.assert_called_once_with(
        result["job_id"],
        {
            "created_after": None,
            "confidence_bin_count": 5,
            "minimum_reviewed_per_generation": 30,
        },
    )


def test_poll_completed_applied_edit_evaluation_job(client, mocker, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_evaluation.config.DB_PATH", db_path)
    job, _ = operations.create_job(db_path, kind="applied_edit_evaluation")
    operations.set_job_state(
        db_path,
        job["job_id"],
        "succeeded",
        details={
            "report_path": "/catalog/styleai.db/evaluation_reports/report.json",
            "summary": {"deployment_status": "evaluation_only"},
        },
    )

    response = client.get(f"/style_edit/evaluation?job_id={job['job_id']}")

    assert response.status_code == 200
    result = response.get_json()["results"]
    assert result["state"] == "succeeded"
    assert result["summary"]["deployment_status"] == "evaluation_only"


@pytest.mark.parametrize(
    "payload",
    (
        {"confidence_bin_count": 1},
        {"confidence_bin_count": True},
        {"minimum_reviewed_per_generation": 0},
        {"created_after": "yesterday-ish"},
    ),
)
def test_applied_edit_evaluation_rejects_invalid_options(
    client, mocker, tmp_path, payload
):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_evaluation.config.DB_PATH", db_path)

    response = client.post("/style_edit/evaluation", json=payload)

    assert response.status_code == 400
    assert response.get_json()["results"] is None


def test_applied_edit_evaluation_worker_persists_summary(mocker, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_evaluation.config.DB_PATH", db_path)
    job, _ = operations.create_job(db_path, kind="applied_edit_evaluation")
    mocker.patch(
        "routes.style_evaluation.applied_edit_reporting.generate_applied_edit_report",
        return_value={
            "report_path": "/catalog/styleai.db/evaluation_reports/report.json",
            "summary": {"dataset": {"reviewed_inferences": 4}},
        },
    )

    from routes.style_evaluation import _run_evaluation_job

    _run_evaluation_job(
        job["job_id"],
        {
            "created_after": None,
            "confidence_bin_count": 5,
            "minimum_reviewed_per_generation": 30,
        },
    )

    stored = operations.get_job(db_path, job["job_id"], include_items=False)
    assert stored["state"] == "succeeded"
    assert stored["details"]["summary"]["dataset"]["reviewed_inferences"] == 4
