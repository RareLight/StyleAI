import sys
import os
import pytest
from unittest.mock import MagicMock

# Monkeypatch sys.argv BEFORE importing app to satisfy argparse in config.py
sys.argv = ["geniusai_server.py", "--db-path", "/tmp/mock_db_path"]

from geniusai_server import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

# --- Admin / Diagnostic Endpoints ---

def test_ping(client):
    response = client.get('/ping')
    assert response.status_code == 200
    assert response.data.decode('utf-8').strip() == "pong"

def test_version(client):
    response = client.get('/version')
    assert response.status_code == 200
    data = response.get_json()
    assert "backend_version" in data

def test_version_check(client):
    response = client.post('/version/check', json={
        "plugin_version": "1.0.0",
        "plugin_release_tag": "v1.0.0",
        "plugin_build": 12345
    })
    assert response.status_code == 200

# --- Database Endpoints ---

def test_db_stats(client, mocker):
    mocker.patch('service_db.get_database_stats', return_value={"total": 0})
    response = client.get('/db/stats')
    assert response.status_code == 200
    assert response.get_json() == {"total": 0}

def test_get_ids(client, mocker):
    mocker.patch('service_chroma.get_all_image_ids', return_value=["id1", "id2"])
    response = client.get('/get/ids')
    assert response.status_code == 200
    assert response.get_json() == ["id1", "id2"]

# --- Search / Cull Endpoints ---

def test_search(client, mocker):
    mocker.patch('service_search.search_images', return_value=[])
    response = client.post('/search', json={
        "term": "test search",
        "n_results": 5
    })
    assert response.status_code == 200
    assert response.get_json() == []

def test_find_similar(client, mocker):
    mocker.patch('service_search.find_similar_images', return_value=[])
    response = client.post('/find_similar', json={
        "photo_id": "test_id",
        "n_results": 5
    })
    assert response.status_code == 200
    assert response.get_json() == {"results": []}

def test_group_similar(client, mocker):
    mocker.patch('service_search.group_similar_images', return_value=[])
    response = client.post('/group_similar', json={
        "photo_ids": ["id1", "id2"]
    })
    assert response.status_code == 200

def test_cull(client, mocker):
    mocker.patch('service_search.cull_images', return_value={"groups": []})
    response = client.post('/cull', json={
        "photo_ids": ["id1", "id2"]
    })
    assert response.status_code == 200
    assert response.get_json() == {"groups": []}

# --- Indexing Endpoints ---

def test_index_unprocessed(client, mocker):
    mocker.patch('routes_index.get_photo_ids_needing_processing', return_value=["id2"])
    response = client.post('/index/check-unprocessed', json={
        "photo_ids": ["id1", "id2"]
    })
    assert response.status_code == 200
    assert "photo_ids" in response.get_json()
    assert response.get_json()["photo_ids"] == ["id2"]

def test_remove(client, mocker):
    mocker.patch('service_chroma.delete_image', return_value=True)
    mocker.patch('service_chroma.delete_faces_by_photo_uuid', return_value=True)
    response = client.post('/remove', json={
        "photo_id": "id1"
    })
    assert response.status_code == 200

# --- Faces Endpoints ---

def test_faces_query(client, mocker):
    mocker.patch('service_face.detect_faces', return_value=[{"embedding": [0.1, 0.2]}])
    # Based on routes_faces implementation
    mocker.patch('service_chroma.query_faces', return_value={"ids": [["f1"]], "distances": [[0.5]], "metadatas": [[{"photo_id": "p1"}]]})
    
    response = client.post('/faces/query', json={
        "image": "fakebase64",
        "n_results": 10
    })
    assert response.status_code in [200, 400] # Usually 200 if base64 is bypassed or 400 if decode fails

def test_faces_cluster(client, mocker):
    mocker.patch('service_persons.run_clustering', return_value={"summary": "ok"})
    response = client.post('/faces/cluster')
    assert response.status_code == 200

def test_list_persons(client, mocker):
    mocker.patch('service_persons.list_persons', return_value=[])
    response = client.get('/faces/persons')
    assert response.status_code == 200
