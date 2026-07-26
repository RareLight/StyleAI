"""Tests for routes/server.py — covers B1 (clip_error reporting) and /ping."""

import pytest
from unittest.mock import MagicMock

from styleai_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_ping_returns_pong(client):
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "pong"


def test_version_returns_backend_version(client):
    response = client.get("/version")
    assert response.status_code == 200
    _json = response.get_json()
    payload = _json.get("results") if _json.get("results") is not None else _json
    assert "backend_version" in payload


def test_health_reports_clip_error_when_set(client, mocker):
    """Regression for B1: when load_model fails, _model_load_error must be exposed via /health."""
    import server_lifecycle

    mocker.patch.object(server_lifecycle, "model", None)
    mocker.patch.object(server_lifecycle, "_model_load_error", "boom: weights missing")

    mock_analysis = MagicMock()
    mock_analysis.get_health_status.return_value = {}
    mocker.patch("routes.server.get_analysis_service", return_value=mock_analysis)
    mocker.patch("services.face._get_face_app")

    response = client.get("/health")
    assert response.status_code == 200
    _json = response.get_json()
    data = _json.get("results") if _json.get("results") is not None else _json
    assert data["clip_model"] == "failed"
    assert data["clip_error"] == "boom: weights missing"


def test_health_reports_no_error_when_healthy(client, mocker):
    import server_lifecycle

    mocker.patch.object(server_lifecycle, "model", object())
    mocker.patch.object(server_lifecycle, "_model_load_error", None)

    mock_analysis = MagicMock()
    mock_analysis.get_health_status.return_value = {}
    mocker.patch("routes.server.get_analysis_service", return_value=mock_analysis)
    mocker.patch("services.face._get_face_app")

    response = client.get("/health")
    assert response.status_code == 200
    _json = response.get_json()
    data = _json.get("results") if _json.get("results") is not None else _json
    assert data["clip_model"] == "loaded"
    assert data["clip_error"] is None


def test_cancel_discards_pending_queue_work(client, mocker):
    import server_lifecycle

    mock_discard = mocker.patch("services.index.discard_pending_index_queue", return_value=3)
    server_lifecycle.GLOBAL_CANCEL_EVENT.clear()

    response = client.post("/cancel_all_tasks")

    assert response.status_code == 200
    assert response.get_json()["results"] == {
        "status": "Canceled active tasks.",
        "discarded": 3,
    }
    assert server_lifecycle.GLOBAL_CANCEL_EVENT.is_set()
    mock_discard.assert_called_once()
    server_lifecycle.GLOBAL_CANCEL_EVENT.clear()
