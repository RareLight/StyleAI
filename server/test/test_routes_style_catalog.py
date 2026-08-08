"""Tests for routes/style_catalog.py — covers GET /styles, GET /styles/<id>, POST /styles/discover, /styles/upgrades/recommendations."""

import pytest
from styleai_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_list_styles(client, mocker):
    mocker.patch(
        "routes.style_catalog.policy_runtime.list_active_policies",
        return_value=[{"id": "style-1", "name": "Cinematic"}],
    )

    response = client.get("/styles")
    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data.get("error") is None
    res = json_data["results"]
    assert res["count"] == 1
    assert res["styles"][0]["id"] == "style-1"


def test_get_style_found_and_not_found(client, mocker):
    mock_get = mocker.patch("routes.style_catalog.policy_runtime.get_active_policy")
    mock_get.return_value = {"id": "style-1", "name": "Cinematic"}

    res_ok = client.get("/styles/style-1")
    assert res_ok.status_code == 200

    mock_get.return_value = None
    res_404 = client.get("/styles/missing-id")
    assert res_404.status_code == 404


def test_get_upgrade_recommendations(client, mocker):
    mocker.patch(
        "routes.style_catalog.policy_runtime.get_upgrade_recommendations",
        return_value=[{"style_id": "style-1", "upgrade_score": 0.95}],
    )

    response = client.get("/styles/upgrades/recommendations")
    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data.get("error") is None
    res = json_data.get("results")
    # If wrapped or direct envelope
    data = res.get("results") if isinstance(res, dict) and "results" in res else res
    assert len(data) == 1
    assert data[0]["style_id"] == "style-1"


def test_record_upgrade_feedback(client, mocker):
    record = mocker.patch(
        "routes.style_catalog.policy_runtime.record_upgrade_feedback",
        return_value={"updated": 2, "requested": 2},
    )
    response = client.post(
        "/styles/upgrades/feedback",
        json={
            "review_id": "review-1",
            "policy_id": "policy-1",
            "labels": [
                {
                    "globalPhotoId": "photo-1",
                    "policy_match": True,
                    "useful": True,
                },
                {
                    "photo_id": "photo-2",
                    "policy_match": False,
                    "useful": False,
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.get_json()["results"]["updated"] == 2
    assert record.call_args.kwargs["labels"][0]["photo_id"] == "photo-1"


def test_record_upgrade_feedback_rejects_invalid_payload(client):
    response = client.post(
        "/styles/upgrades/feedback",
        json={"review_id": "review-1"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_discover_styles(client, mocker):
    mocker.patch(
        "routes.style_catalog.policy_runtime.request_rebuild",
        return_value={"status": "queued", "phase": "queued"},
    )

    response = client.post("/styles/discover", json={"photo_ids": ["p1"]})
    assert response.status_code == 202
    json_data = response.get_json()
    assert json_data.get("error") is None
    res = json_data["results"]
    assert res["status"] == "accepted"
    assert res["discovery"]["status"] == "queued"


def test_discovery_status(client, mocker):
    mocker.patch(
        "routes.style_catalog.policy_runtime.discovery_status",
        return_value={
            "status": "running",
            "phase": "fitting_partitions",
            "eligible_partitions": 4,
            "completed_partitions": 2,
        },
    )

    response = client.get("/styles/discover/status")

    assert response.status_code == 200
    result = response.get_json()["results"]
    assert result["discovery"]["completed_partitions"] == 2
