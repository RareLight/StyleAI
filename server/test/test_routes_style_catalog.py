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
        "routes.style_catalog.catalog_service.list_styles",
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
    mock_get = mocker.patch("routes.style_catalog.catalog_service.get_style")
    mock_get.return_value = {"id": "style-1", "name": "Cinematic"}

    res_ok = client.get("/styles/style-1")
    assert res_ok.status_code == 200

    mock_get.return_value = None
    res_404 = client.get("/styles/missing-id")
    assert res_404.status_code == 404


def test_get_upgrade_recommendations(client, mocker):
    mocker.patch(
        "routes.style_catalog.style_upgrades.get_style_upgrade_recommendations",
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


def test_discover_styles(client, mocker):
    mocker.patch(
        "routes.style_catalog.catalog_service.discover_styles_from_examples",
        return_value=[{"id": "s1", "name": "Discovered"}],
    )

    response = client.post("/styles/discover", json={"photo_ids": ["p1"]})
    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data.get("error") is None
    res = json_data["results"]
    assert res["styles_created"] == 1
