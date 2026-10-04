import pytest

from core.migrations import run_migrations
from services import edit_history, policy_store
from styleai_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def _fixture(db_path: str) -> str:
    run_migrations(db_path)
    connection = policy_store.connect_policy_store(db_path)
    try:
        connection.execute(
            """
            INSERT INTO policy_v2_generations (
                generation_id, status, algorithm_version,
                feature_schema_version, target_schema_version,
                metrics_json, created_at, activated_at
            ) VALUES ('generation-active', 'active', 'v2', 'f1', 't1', '{}', 'now', 'now')
            """
        )
        connection.commit()
    finally:
        connection.close()
    edit_history.create_inference(
        db_path=db_path,
        inference_id="inference-1",
        photo_id="photo-1",
        generation_id="generation-active",
        policy_id="policy-1",
        hard_partition_key="default",
        engine="policy_v2",
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
        modeled_keys=["exposure"],
        pre_edit_state={"exposure": 0.0},
        pre_edit_fingerprint="before",
        target_state={"exposure": 1.0},
        target_fingerprint="after",
    )
    edit_history.append_event(
        db_path=db_path,
        inference_id="inference-1",
        event_kind="apply_confirmed",
        idempotency_key="apply-1",
        observed_state={"exposure": 1.0},
        observed_fingerprint="after",
    )
    return "inference-1"


def test_refinement_session_route_flow(client, mocker, tmp_path):
    db_path = str(tmp_path / "styleai.db")
    inference_id = _fixture(db_path)
    mocker.patch("routes.style_refinement.config.DB_PATH", db_path)

    response = client.post(
        "/style_refinement/sessions",
        json={"mode": "quick", "inference_ids": [inference_id]},
    )
    assert response.status_code == 200
    session = response.get_json()["results"]
    assert session["pair_count"] == 1

    pair_response = client.get(
        f"/style_refinement/sessions/{session['session_id']}/next"
    )
    pair_result = pair_response.get_json()["results"]
    pair = pair_result["pair"]
    assert pair_response.status_code == 200
    assert pair_result["done"] is False
    assert set(pair) >= {"pair_id", "recipe_a", "recipe_b"}

    choice_response = client.post(
        f"/style_refinement/sessions/{session['session_id']}/choices",
        json={
            "pair_id": pair["pair_id"],
            "choice": "equivalent",
            "idempotency_key": "route-choice-1",
        },
    )
    assert choice_response.status_code == 200
    assert choice_response.get_json()["error"] is None

    finalized = client.post(
        f"/style_refinement/sessions/{session['session_id']}/finalize"
    )
    assert finalized.status_code == 200
    assert finalized.get_json()["results"]["choice_counts"]["equivalent"] == 1


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"mode": "bad", "inference_ids": ["one"]},
        {"mode": "quick", "inference_ids": "one"},
        {"mode": "quick", "inference_ids": ["one"], "requested_count": True},
    ),
)
def test_refinement_session_rejects_malformed_requests(
    client, mocker, tmp_path, payload
):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    mocker.patch("routes.style_refinement.config.DB_PATH", db_path)

    response = client.post("/style_refinement/sessions", json=payload)

    assert response.status_code in {400, 500}
    body = response.get_json()
    assert body == {
        "results": None,
        "error": body["error"],
        "warning": None,
    }
