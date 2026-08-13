import json
import math
from pathlib import Path

from jsonschema.validators import Draft202012Validator

from services import edit_history
from services.edit_quality_evaluation import evaluate_applied_edit_histories


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
APPLIED_EDIT_QUALITY_SCHEMA = (
    REPOSITORY_ROOT / "docs" / "schemas" / "applied-edit-quality-v2.schema.json"
)


def _applied_history(
    db_path,
    photo_id,
    target_exposure,
    outcome,
    observed_exposure,
    *,
    confidence=0.8,
    generation_id="generation-1",
):
    inference_id = edit_history.create_recipe_inference(
        db_path=db_path,
        photo_id=photo_id,
        recipe={"global": {"exposure": target_exposure, "contrast": 10.0}},
        current_settings={"Exposure2012": 0.0, "Contrast2012": 0.0},
        engine="policy_v2",
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
        generation_id=generation_id,
        policy_id="policy-1",
        confidence=confidence,
        entropy=0.2,
    )
    edit_history.record_application(
        db_path=db_path,
        inference_id=inference_id,
        event_kind="apply_confirmed",
        idempotency_key=f"application:{inference_id}",
        current_settings={
            "Exposure2012": target_exposure,
            "Contrast2012": 10.0,
        },
    )
    edit_history.record_user_outcome(
        db_path=db_path,
        inference_id=inference_id,
        outcome=outcome,
        current_settings={
            "Exposure2012": observed_exposure,
            "Contrast2012": 10.0 if outcome != "rejected" else 0.0,
        },
    )


def _histories(db_path, page_size=500):
    return [
        history
        for batch in edit_history.iter_inference_history_batches(
            db_path=db_path,
            page_size=page_size,
        )
        for history in batch
    ]


def test_applied_edit_report_separates_preferences_from_numeric_targets(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _applied_history(db_path, "accepted", 0.5, "accepted", 0.5)
    _applied_history(db_path, "modified", 0.5, "modified_and_kept", 0.7)
    _applied_history(db_path, "rejected", 0.5, "rejected", 0.0)

    report = evaluate_applied_edit_histories(_histories(db_path, page_size=1))

    assert report["dataset"] == {
        "inferences": 3,
        "applied_inferences": 3,
        "reviewed_inferences": 3,
        "review_coverage": 1.0,
    }
    assert report["user_outcomes"]["kept_rate"] == 2 / 3
    assert report["user_outcomes"]["rejection_rate"] == 1 / 3
    corrections = report["delivered_target_corrections"]
    assert corrections["evaluated_reviews"] == 2
    exposure = corrections["per_target"]["exposure"]
    assert math.isclose(exposure["mean_correction"], 0.1)
    assert math.isclose(exposure["raw_rmse"], math.sqrt(0.02))


def test_latest_explicit_outcome_is_the_effective_judgment(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _applied_history(db_path, "photo-1", 0.5, "modified_and_kept", 0.7)
    history = _histories(db_path)[0]
    edit_history.record_user_outcome(
        db_path=db_path,
        inference_id=history["inference_id"],
        outcome="rejected",
        current_settings={"Exposure2012": 0.0, "Contrast2012": 0.0},
    )

    report = evaluate_applied_edit_histories(_histories(db_path))

    assert report["user_outcomes"]["rejected"] == 1
    assert report["user_outcomes"]["modified_and_kept"] == 0
    assert report["delivered_target_corrections"]["evaluated_reviews"] == 0


def test_empty_applied_edit_report_is_well_defined():
    report = evaluate_applied_edit_histories([])

    assert report["dataset"]["review_coverage"] is None
    assert report["user_outcomes"]["acceptance_rate"] is None
    assert report["delivered_target_corrections"]["evaluated_reviews"] == 0
    assert report["schema_version"] == "applied-edit-quality-v2"
    assert report["burst_coherence"]["admitted_photos"] == 0


def test_generated_report_conforms_to_applied_edit_quality_v2_schema():
    schema = json.loads(APPLIED_EDIT_QUALITY_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    Draft202012Validator(schema).validate(evaluate_applied_edit_histories([]))


def test_burst_report_tracks_coverage_leakage_and_geometry_disagreement(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _applied_history(db_path, "representative", 0.5, "accepted", 0.5)
    _applied_history(db_path, "member", 0.5, "modified_and_kept", 0.6)
    histories = _histories(db_path)
    representative, member = histories
    for history in histories:
        history["burst_group_id"] = "edit-burst:one"
        history["representative_photo_id"] = representative["photo_id"]
        history["absolute_target"] = {"exposure": 0.5}
        history["policy_agreement"] = {
            "same_policy": True,
            "same_partition": True,
        }
    representative["reuse_tier"] = "independent"
    representative["absolute_target"]["crop"] = {"angle": 0.0}
    member["reuse_tier"] = "policy_coherent"
    member["absolute_target"]["crop"] = {"angle": 1.0}

    report = evaluate_applied_edit_histories(histories)
    burst = report["burst_coherence"]

    assert burst["eligible_photos"] == 2
    assert burst["admitted_photos"] == 1
    assert burst["selective_coverage"] == 0.5
    assert burst["policy_agreement"]["cross_policy_or_partition_leakage"] == 0
    assert burst["geometry_disagreement"]["disagreements"] == 1


def test_confidence_calibration_and_generation_evidence_gates(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    _applied_history(
        db_path,
        "accepted-high",
        0.5,
        "accepted",
        0.5,
        confidence=0.9,
        generation_id="generation-a",
    )
    _applied_history(
        db_path,
        "rejected-low",
        0.5,
        "rejected",
        0.0,
        confidence=0.1,
        generation_id="generation-a",
    )
    _applied_history(
        db_path,
        "modified-high",
        0.5,
        "modified_and_kept",
        0.7,
        confidence=0.8,
        generation_id="generation-b",
    )

    report = evaluate_applied_edit_histories(
        _histories(db_path),
        confidence_bin_count=5,
        minimum_reviewed_per_generation=2,
    )

    calibration = report["confidence_calibration"]
    assert calibration["sample_count"] == 3
    assert math.isclose(calibration["brier_score"], 0.22)
    comparisons = report["generation_comparison"]
    assert comparisons["deployment_status"] == "evaluation_only"
    assert comparisons["comparable_generation_count"] == 1
    by_generation = {row["generation_id"]: row for row in comparisons["generations"]}
    assert by_generation["generation-a"]["evidence_status"] == (
        "sufficient_for_comparison"
    )
    assert by_generation["generation-b"]["evidence_status"] == ("insufficient_reviews")


def test_quality_evaluator_rejects_invalid_calibration_configuration():
    try:
        evaluate_applied_edit_histories([], confidence_bin_count=1)
    except ValueError as exc:
        assert "bin_count" in str(exc)
    else:
        raise AssertionError("invalid confidence bins were accepted")


def test_rendering_quality_separates_auto_from_unapplied_suggestions():
    current = {
        "profile": {"profile_id": "base", "display_name": "Base"},
        "is_hdr": False,
    }
    proposed = {
        "profile": {"profile_id": "contrast", "display_name": "Contrast"},
        "is_hdr": True,
    }

    def history(inference_id, *, profile_mode, hdr_mode, effective, observed):
        return {
            "inference_id": inference_id,
            "created_at": inference_id,
            "target_state": {"exposure": 0.5},
            "rendering_intent": {
                "current": current,
                "proposed": proposed,
                "effective": effective,
                "profile_mode": profile_mode,
                "hdr_mode": hdr_mode,
            },
            "events": [
                {
                    "event_id": f"outcome-{inference_id}",
                    "event_kind": "accepted",
                    "details": {"observed_rendering_state": observed},
                }
            ],
        }

    report = evaluate_applied_edit_histories(
        [
            history(
                "auto",
                profile_mode="auto",
                hdr_mode="auto",
                effective=proposed,
                observed={"profile": proposed["profile"], "is_hdr": False},
            ),
            history(
                "suggest",
                profile_mode="suggest",
                hdr_mode="suggest",
                effective=current,
                observed=current,
            ),
        ],
        minimum_reviewed_per_generation=1,
    )

    rendering = report["rendering_outcomes"]
    assert rendering["slider_metrics_are_separate"] is True
    assert rendering["profile"]["auto"]["accepted"] == 1
    assert rendering["profile"]["suggest"]["left_current"] == 1
    assert rendering["hdr"]["auto"]["returned_to_original"] == 1
    assert rendering["hdr"]["hdr_activation"]["return_rate"] == 1.0
    assert rendering["deployment_status"] == "evaluation_only"
