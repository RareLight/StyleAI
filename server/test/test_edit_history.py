import pytest

from services import edit_history, policy_store


def _create(db_path, inference_id="inference-1"):
    return edit_history.create_inference(
        db_path=db_path,
        inference_id=inference_id,
        photo_id="photo-1",
        generation_id="generation-1",
        policy_id="policy-1",
        hard_partition_key="profile:adobe-color|sdr",
        engine="policy_v2",
        algorithm_version="v2",
        feature_schema_version="features-v1",
        target_schema_version="targets-v1",
        modeled_keys=["contrast", "exposure"],
        pre_edit_state={"contrast": 0.0, "exposure": 0.0},
        pre_edit_fingerprint="before",
        target_state={"contrast": 10.0, "exposure": 0.5},
        target_fingerprint="after",
        confidence=0.9,
        entropy=0.1,
        summary="Warm policy",
    )


def test_inference_and_generated_event_are_atomic(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    assert _create(db_path) == "inference-1"

    stored = edit_history.get_inference(db_path=db_path, inference_id="inference-1")
    assert stored["photo_id"] == "photo-1"
    assert stored["modeled_keys"] == ["contrast", "exposure"]
    assert stored["events"][0]["event_kind"] == "generated"


def test_rendering_intent_and_confirmed_readback_are_immutable(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    current = {
        "schema_version": "rendering-state-v1",
        "profile": {
            "profile_id": "base",
            "display_name": "Base",
            "camera_make": "Nikon",
            "camera_model": "Z 7",
        },
        "is_hdr": False,
    }
    target = {
        **current,
        "profile": {
            **current["profile"],
            "profile_id": "contrast",
            "display_name": "Contrast",
        },
        "is_hdr": True,
    }
    recipe = {
        "global": {"exposure": 0.5},
        "rendering_intent": {
            "current": current,
            "effective": target,
            "selector_algorithm_version": "selector-v1",
        },
    }
    inference_id = edit_history.create_recipe_inference(
        db_path=db_path,
        photo_id="photo-rendering",
        recipe=recipe,
        current_settings={"Exposure2012": 0.0},
        engine="policy_v2",
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )
    event = edit_history.record_application(
        db_path=db_path,
        inference_id=inference_id,
        event_kind="apply_confirmed",
        idempotency_key=f"application:{inference_id}",
        current_settings={
            "Exposure2012": 0.5,
            "CameraProfile": "Contrast",
            "HDREditMode": 1,
        },
    )
    stored = edit_history.get_inference(db_path=db_path, inference_id=inference_id)

    assert stored["current_rendering_state"] == current
    assert stored["target_rendering_state"] == target
    assert stored["rendering_selector_version"] == "selector-v1"
    assert event["details"]["observed_rendering_state"]["is_hdr"] is True


def test_event_append_is_idempotent_and_preserves_observed_state(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _create(db_path)
    first = edit_history.append_event(
        db_path=db_path,
        inference_id="inference-1",
        event_kind="apply_confirmed",
        idempotency_key="apply:inference-1",
        observed_state={"contrast": 10.0, "exposure": 0.5},
        observed_fingerprint="after",
        details={"global_applied": True},
    )
    retry = edit_history.append_event(
        db_path=db_path,
        inference_id="inference-1",
        event_kind="apply_confirmed",
        idempotency_key="apply:inference-1",
        observed_state={"contrast": 10.0, "exposure": 0.5},
        observed_fingerprint="after",
    )

    assert retry["event_id"] == first["event_id"]
    stored = edit_history.get_inference(db_path=db_path, inference_id="inference-1")
    assert len(stored["events"]) == 2
    assert stored["events"][1]["observed_state"]["exposure"] == 0.5


def test_idempotency_key_cannot_be_reused_for_another_event(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _create(db_path)
    edit_history.append_event(
        db_path=db_path,
        inference_id="inference-1",
        event_kind="apply_failed",
        idempotency_key="application-1",
    )
    with pytest.raises(ValueError, match="different event"):
        edit_history.append_event(
            db_path=db_path,
            inference_id="inference-1",
            event_kind="not_applied",
            idempotency_key="application-1",
        )


def test_edit_history_survives_policy_reset(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _create(db_path)
    connection = policy_store.connect_policy_store(db_path)
    try:
        policy_store.reset_policy_v2(connection)
    finally:
        connection.close()

    assert (
        edit_history.get_inference(db_path=db_path, inference_id="inference-1")
        is not None
    )


def test_confirmed_application_uses_modeled_lightroom_readback(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    inference_id = edit_history.create_recipe_inference(
        db_path=db_path,
        photo_id="photo-1",
        recipe={"global": {"exposure": 0.5, "contrast": 10.0}},
        current_settings={"Exposure2012": 0.0, "Contrast2012": 0.0},
        engine="policy_v2",
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )
    event = edit_history.record_application(
        db_path=db_path,
        inference_id=inference_id,
        event_kind="apply_confirmed",
        idempotency_key=f"application:{inference_id}",
        current_settings={
            "Exposure2012": 0.5,
            "Contrast2012": 10.0,
            "Clarity2012": 99.0,
        },
    )

    assert event["observed_state"] == {"contrast": 10.0, "exposure": 0.5}
    stored = edit_history.get_inference(db_path=db_path, inference_id=inference_id)
    assert event["observed_fingerprint"] == stored["target_fingerprint"]


def test_confirmed_application_requires_readback(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _create(db_path)
    with pytest.raises(ValueError, match="requires Lightroom readback"):
        edit_history.record_application(
            db_path=db_path,
            inference_id="inference-1",
            event_kind="apply_confirmed",
            idempotency_key="application:inference-1",
        )


def _create_applied_recipe(db_path):
    created = edit_history.create_recipe_inference(
        db_path=db_path,
        photo_id="photo-1",
        recipe={"global": {"exposure": 0.5, "contrast": 10.0}},
        current_settings={"Exposure2012": 0.0, "Contrast2012": 0.0},
        engine="policy_v2",
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )
    edit_history.record_application(
        db_path=db_path,
        inference_id=created,
        event_kind="apply_confirmed",
        idempotency_key=f"application:{created}",
        current_settings={"Exposure2012": 0.5, "Contrast2012": 10.0},
    )
    return created


def test_reconciliation_detects_undo_and_is_idempotent(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    inference_id = _create_applied_recipe(db_path)

    first = edit_history.reconcile_photo_state(
        db_path=db_path,
        photo_id="photo-1",
        current_settings={"Exposure2012": 0.0, "Contrast2012": 0.0},
    )
    retry = edit_history.reconcile_photo_state(
        db_path=db_path,
        photo_id="photo-1",
        current_settings={"Exposure2012": 0.0, "Contrast2012": 0.0},
    )

    assert first == {
        "photo_id": "photo-1",
        "inference_id": inference_id,
        "state": "reverted",
        "recorded": True,
        "event_id": first["event_id"],
    }
    assert retry["state"] == "reverted"
    assert retry["recorded"] is False


def test_reconciliation_tracks_divergence_and_later_return_to_applied(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _create_applied_recipe(db_path)

    diverged = edit_history.reconcile_photo_state(
        db_path=db_path,
        photo_id="photo-1",
        current_settings={"Exposure2012": 0.2, "Contrast2012": 5.0},
    )
    restored = edit_history.reconcile_photo_state(
        db_path=db_path,
        photo_id="photo-1",
        current_settings={"Exposure2012": 0.5, "Contrast2012": 10.0},
    )

    assert diverged["state"] == "diverged"
    assert restored["state"] == "apply_confirmed"
    assert restored["recorded"] is True


def test_reconciliation_uses_only_newest_applied_inference(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _create_applied_recipe(db_path)
    newest = _create_applied_recipe(db_path)

    result = edit_history.reconcile_photo_state(
        db_path=db_path,
        photo_id="photo-1",
        current_settings={"Exposure2012": 0.0, "Contrast2012": 0.0},
    )

    assert result["inference_id"] == newest


def test_reconciliation_returns_untracked_without_writing(tmp_path):
    result = edit_history.reconcile_photo_state(
        db_path=str(tmp_path / "styleai.db"),
        photo_id="photo-missing",
        current_settings={"Exposure2012": 0.0},
    )

    assert result == {
        "photo_id": "photo-missing",
        "inference_id": None,
        "state": "untracked",
        "recorded": False,
    }


def test_explicit_outcome_records_readback_and_is_idempotent(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    inference_id = _create_applied_recipe(db_path)

    first = edit_history.record_user_outcome(
        db_path=db_path,
        inference_id=inference_id,
        outcome="accepted",
        current_settings={"Exposure2012": 0.5, "Contrast2012": 10.0},
    )
    retry = edit_history.record_user_outcome(
        db_path=db_path,
        inference_id=inference_id,
        outcome="accepted",
        current_settings={"Exposure2012": 0.5, "Contrast2012": 10.0},
    )

    assert first["recorded"] is True
    assert first["state"] == "apply_confirmed"
    assert retry["recorded"] is False
    stored = edit_history.get_inference(db_path=db_path, inference_id=inference_id)
    assert stored["events"][-1]["explicit_user_action"] is True


def test_accepted_rejects_modified_modeled_state(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    inference_id = _create_applied_recipe(db_path)

    with pytest.raises(ValueError, match="modified_and_kept"):
        edit_history.record_user_outcome(
            db_path=db_path,
            inference_id=inference_id,
            outcome="accepted",
            current_settings={"Exposure2012": 0.4, "Contrast2012": 8.0},
        )


def test_modified_outcome_and_corrected_rejection_remain_append_only(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    inference_id = _create_applied_recipe(db_path)

    modified = edit_history.record_user_outcome(
        db_path=db_path,
        inference_id=inference_id,
        outcome="modified_and_kept",
        current_settings={"Exposure2012": 0.4, "Contrast2012": 8.0},
    )
    rejected = edit_history.record_user_outcome(
        db_path=db_path,
        inference_id=inference_id,
        outcome="rejected",
        current_settings={"Exposure2012": 0.0, "Contrast2012": 0.0},
    )

    assert modified["state"] == "diverged"
    assert rejected["state"] == "reverted"
    stored = edit_history.get_inference(db_path=db_path, inference_id=inference_id)
    assert [event["event_kind"] for event in stored["events"][-2:]] == [
        "modified_and_kept",
        "rejected",
    ]


def test_outcome_requires_an_application_event(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _create(db_path)

    with pytest.raises(ValueError, match="must be applied"):
        edit_history.record_user_outcome(
            db_path=db_path,
            inference_id="inference-1",
            outcome="rejected",
            current_settings={"Exposure2012": 0.0, "Contrast2012": 0.0},
        )
