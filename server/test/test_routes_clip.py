import pytest
from flask import Flask

from routes import clip


@pytest.mark.parametrize("cached", [False, True])
def test_clip_download_reuses_existing_weights(monkeypatch, mocker, cached):
    monkeypatch.setattr(clip, "is_model_cached", lambda: cached)
    download = mocker.patch.object(clip, "start_download_clip_model")
    app = Flask(__name__)
    app.register_blueprint(clip.clip_bp)

    with app.test_client() as client:
        status = client.get("/clip/status")
        response = client.post("/clip/download/start")

    assert status.json["clip"] == ("ready" if cached else "not_ready")
    assert response.status_code == 200
    assert response.json["download"] == ("cached" if cached else "started")
    assert download.call_count == (0 if cached else 1)
