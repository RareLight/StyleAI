import numpy as np
import pytest

from services.policy_recommendation_evaluation import (
    REVIEW_SCHEMA_VERSION,
    RankingConfiguration,
    calibrate_recommendations,
    evaluate_ranking_configuration,
    parse_review_document,
)


def _review_document(review_count=3):
    reviews = []
    for review_index in range(review_count):
        prefix = f"review-{review_index}"
        reviews.append(
            {
                "review_id": prefix,
                "policy_index": 0,
                "target_count": 3,
                "hard_partition_key": "sdr|adobe color",
                "candidates": [
                    {
                        "photo_id": f"{prefix}-strong",
                        "embedding": [1.0, 0.0, 0.0],
                        "responsibilities": [0.90, 0.10],
                        "assignment_entropy": 0.25,
                        "coverage_gain": 0.2,
                        "hard_partition_key": "sdr|adobe color",
                        "metadata": {"rating": 3},
                        "policy_match": True,
                        "useful": True,
                    },
                    {
                        "photo_id": f"{prefix}-moderate",
                        "embedding": [0.0, 1.0, 0.0],
                        "responsibilities": [0.72, 0.28],
                        "assignment_entropy": 0.65,
                        "coverage_gain": 0.8,
                        "hard_partition_key": "sdr|adobe color",
                        "metadata": {"rating": 4},
                        "policy_match": True,
                        "useful": True,
                    },
                    {
                        "photo_id": f"{prefix}-leakage",
                        "embedding": [0.0, 0.0, 1.0],
                        "responsibilities": [0.65, 0.35],
                        "assignment_entropy": 0.70,
                        "coverage_gain": 1.0,
                        "hard_partition_key": "sdr|adobe color",
                        "metadata": {"rating": 5},
                        "policy_match": False,
                        "useful": False,
                    },
                ],
            }
        )
    return {"schema_version": REVIEW_SCHEMA_VERSION, "reviews": reviews}


def test_parse_review_document_validates_versioned_labels():
    reviews = parse_review_document(_review_document())

    assert len(reviews) == 3
    assert reviews[0].candidates[0].policy_match is True
    np.testing.assert_allclose(
        reviews[0].candidates[0].candidate.responsibilities,
        [0.9, 0.1],
    )

    invalid = _review_document()
    invalid["reviews"][0]["candidates"][0]["useful"] = "yes"
    with pytest.raises(ValueError, match="true, false, or null"):
        parse_review_document(invalid)


def test_configuration_metrics_expose_precision_leakage_and_usefulness():
    reviews = parse_review_document(_review_document())
    metrics = evaluate_ranking_configuration(
        reviews,
        RankingConfiguration(
            minimum_confidence=0.60,
            minimum_margin=0.10,
            maximum_entropy=0.80,
        ),
    )

    assert metrics["policy_precision"] == pytest.approx(2 / 3)
    assert metrics["policy_precision_wilson_lower"] < metrics["policy_precision"]
    assert metrics["policy_leakage"] == pytest.approx(1 / 3)
    assert metrics["useful_precision"] == pytest.approx(2 / 3)


def test_calibration_selects_high_precision_configuration_and_cross_validates():
    reviews = parse_review_document(_review_document())
    loose = RankingConfiguration(
        minimum_confidence=0.60,
        minimum_margin=0.10,
        maximum_entropy=0.80,
    )
    strict = RankingConfiguration(
        minimum_confidence=0.70,
        minimum_margin=0.15,
        maximum_entropy=0.80,
    )

    report = calibrate_recommendations(
        reviews,
        target_policy_precision=0.95,
        minimum_labeled_selected=2,
        requested_folds=3,
        configurations=[loose, strict],
    )

    assert report["recommended"]["configuration"] == strict.as_dict()
    assert report["recommended"]["metrics"]["policy_precision"] == pytest.approx(1.0)
    assert report["recommended"]["meets_precision_target"] is False
    assert report["recommended"]["deployment_status"] == "evaluation_only"
    assert report["cross_validation"]["fold_count"] == 3
    assert report["cross_validation"]["held_out_mean_metrics"][
        "policy_precision"
    ] == pytest.approx(1.0)
    assert "review-0" not in str(report)
