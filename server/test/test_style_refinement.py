from __future__ import annotations

from core.migrations import run_migrations
from services import edit_history, policy_store, style_refinement


def _active_generation(db_path: str, generation_id: str = "generation-active") -> None:
    run_migrations(db_path)
    connection = policy_store.connect_policy_store(db_path)
    try:
        connection.execute(
            """
            INSERT INTO policy_v2_generations (
                generation_id, status, algorithm_version,
                feature_schema_version, target_schema_version,
                metrics_json, created_at, activated_at
            ) VALUES (?, 'active', 'v2', 'features-v1', 'targets-v1', '{}', ?, ?)
            """,
            (generation_id, "2026-08-27 12:00:00", "2026-08-27 12:00:00"),
        )
        connection.commit()
    finally:
        connection.close()


def _applied_inference(db_path: str, inference_id: str = "inference-1") -> str:
    edit_history.create_inference(
        db_path=db_path,
        inference_id=inference_id,
        photo_id=f"photo-{inference_id}",
        generation_id="generation-active",
        policy_id="policy-1",
        hard_partition_key="sdr|adobe color",
        engine="policy_v2",
        algorithm_version="v2",
        feature_schema_version="features-v1",
        target_schema_version="targets-v1",
        modeled_keys=["contrast", "exposure"],
        pre_edit_state={"contrast": 0.0, "exposure": 0.0},
        pre_edit_fingerprint="before",
        target_state={"contrast": 30.0, "exposure": 1.0},
        target_fingerprint="target",
    )
    edit_history.append_event(
        db_path=db_path,
        inference_id=inference_id,
        event_kind="apply_confirmed",
        idempotency_key=f"apply:{inference_id}",
        observed_state={"contrast": 30.0, "exposure": 1.0},
        observed_fingerprint="target",
    )
    return inference_id


def test_quick_session_is_blinded_resumable_and_evaluation_only(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _active_generation(db_path)
    inference_id = _applied_inference(db_path)

    session = style_refinement.create_session(
        db_path=db_path,
        inference_ids=[inference_id],
        mode="quick",
    )
    pair = style_refinement.next_pair(db_path=db_path, session_id=session["session_id"])

    assert session["pair_count"] == 1
    assert session["attached"] is False
    assert session["evaluation_only"] is True
    assert pair["recipe_a"]["global"] != pair["recipe_b"]["global"]
    assert "identity_a" not in pair
    assert "identity_b" not in pair

    first = style_refinement.record_choice(
        db_path=db_path,
        session_id=session["session_id"],
        pair_id=pair["pair_id"],
        choice="prefer_a",
        winner_keepable=True,
        idempotency_key="choice-1",
    )
    retry = style_refinement.record_choice(
        db_path=db_path,
        session_id=session["session_id"],
        pair_id=pair["pair_id"],
        choice="prefer_a",
        winner_keepable=True,
        idempotency_key="choice-1",
    )

    assert first["recorded"] is True
    assert retry == {
        "choice_id": first["choice_id"],
        "recorded": False,
        "choice": "prefer_a",
    }
    assert (
        style_refinement.next_pair(db_path=db_path, session_id=session["session_id"])
        is None
    )
    summary = style_refinement.finalize_session(
        db_path=db_path, session_id=session["session_id"]
    )
    assert summary["state"] == "finalized"
    assert summary["choice_counts"]["prefer_a"] == 1


def test_interrupted_session_creation_attaches_and_resumes(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _active_generation(db_path)
    inference_id = _applied_inference(db_path)

    first = style_refinement.create_session(
        db_path=db_path, inference_ids=[inference_id], mode="quick"
    )
    resumed = style_refinement.create_session(
        db_path=db_path, inference_ids=[inference_id], mode="quick"
    )

    assert resumed["session_id"] == first["session_id"]
    assert resumed["attached"] is True
    assert resumed["remaining_count"] == 1


def test_corrected_choice_appends_instead_of_overwriting(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _active_generation(db_path)
    inference_id = _applied_inference(db_path)
    session = style_refinement.create_session(
        db_path=db_path, inference_ids=[inference_id], mode="quick"
    )
    pair = style_refinement.next_pair(db_path=db_path, session_id=session["session_id"])
    style_refinement.record_choice(
        db_path=db_path,
        session_id=session["session_id"],
        pair_id=pair["pair_id"],
        choice="prefer_a",
        idempotency_key="choice-first",
    )
    style_refinement.record_choice(
        db_path=db_path,
        session_id=session["session_id"],
        pair_id=pair["pair_id"],
        choice="prefer_b",
        idempotency_key="choice-corrected",
    )

    connection = policy_store.connect_policy_store(db_path)
    try:
        rows = connection.execute(
            "SELECT choice FROM policy_v2_refinement_choices ORDER BY choice_sequence"
        ).fetchall()
    finally:
        connection.close()
    assert [row["choice"] for row in rows] == ["prefer_a", "prefer_b"]


def test_correction_validation_requires_fresh_matching_readback(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _active_generation(db_path)
    inference_id = _applied_inference(db_path)
    edit_history.record_user_outcome(
        db_path=db_path,
        inference_id=inference_id,
        outcome="modified_and_kept",
        current_settings={"Contrast2012": 15.0, "Exposure2012": 0.5},
    )

    stale = style_refinement.preflight(
        db_path=db_path,
        inference_ids=[inference_id],
        mode="correction_validation",
        current_settings={inference_id: {"Contrast2012": 14.0, "Exposure2012": 0.5}},
    )
    fresh = style_refinement.preflight(
        db_path=db_path,
        inference_ids=[inference_id],
        mode="correction_validation",
        current_settings={inference_id: {"Contrast2012": 15.0, "Exposure2012": 0.5}},
    )

    assert stale["eligible_count"] == 0
    assert stale["exclusions"] == {"stale_or_unverified_readback": 1}
    assert fresh["eligible_count"] == 1


def test_active_generation_change_pauses_pair_delivery(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _active_generation(db_path)
    inference_id = _applied_inference(db_path)
    session = style_refinement.create_session(
        db_path=db_path, inference_ids=[inference_id], mode="quick"
    )
    connection = policy_store.connect_policy_store(db_path)
    try:
        connection.execute(
            "UPDATE policy_v2_generations SET status = 'retired' WHERE status = 'active'"
        )
        connection.execute(
            """
            INSERT INTO policy_v2_generations (
                generation_id, status, algorithm_version,
                feature_schema_version, target_schema_version,
                metrics_json, created_at, activated_at
            ) VALUES ('generation-new', 'active', 'v2', 'features-v1',
                      'targets-v1', '{}', '2026-08-27 13:00:00', '2026-08-27 13:00:00')
            """
        )
        connection.commit()
    finally:
        connection.close()

    import pytest

    with pytest.raises(RuntimeError, match="active style generation changed"):
        style_refinement.next_pair(db_path=db_path, session_id=session["session_id"])
