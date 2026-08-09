import json

import numpy as np
import pytest

from services.policy_catalog_evaluation import (
    _PredictionRow,
    _outer_fold_count,
    _score_predictions,
    evaluate_catalog_training_examples,
)
from services import source_embeddings


def _catalog_examples(count=24):
    examples = []
    for index in range(count):
        angle = 0.08 * index
        embedding = np.asarray(
            [
                np.cos(angle),
                np.sin(angle),
                0.2 + index / 200.0,
                0.4,
                0.1,
                0.3,
            ],
            dtype=np.float64,
        )
        embedding /= np.linalg.norm(embedding)
        examples.append(
            {
                "photo_id": f"catalog-{index:03d}",
                "embedding": embedding.tolist(),
                "metadata": {
                    "source_provenance": "raw_preview",
                    "source_embedding_provenance": "raw_preview",
                    "source_embedding_fingerprint": f"catalog-fingerprint-{index}",
                    "source_embedding_schema": source_embeddings.SOURCE_EMBEDDING_SCHEMA_VERSION,
                    "source_embedding_model": source_embeddings.SOURCE_EMBEDDING_MODEL_ID,
                    "source_embedding_preprocess": source_embeddings.SOURCE_EMBEDDING_PREPROCESS_VERSION,
                    "camera_profile": "Adobe Color",
                    "camera_make": "Example",
                    "camera_model": "Camera",
                    "lens": "Prime",
                    "capture_time": float(index * 60),
                    "exp_luminance_mean": 0.2 + index / 100.0,
                    "exp_contrast": 0.3 + index / 200.0,
                    "canonical_settings": json.dumps(
                        {
                            "exposure": -0.5 + index * 0.04,
                            "contrast": -12.0 + index * 1.1,
                            "white_balance": (
                                "Custom" if index % 4 == 0 else "As Shot"
                            ),
                        }
                    ),
                },
            }
        )
    return examples


def test_catalog_evaluation_runs_production_artifacts_without_photo_ids():
    report = evaluate_catalog_training_examples(
        _catalog_examples(),
        requested_folds=2,
        seed=7,
    )

    assert report["schema_version"] == "catalog-policy-evaluation-v1"
    assert len(report["dataset_fingerprint"]) == 64
    assert report["dataset"]["curated_examples"] == 24
    assert report["selective_prediction"]["evaluated_examples"] == 24
    assert report["selective_prediction"]["coverage"] == pytest.approx(1.0)
    assert np.isfinite(report["fidelity"]["normalized_rmse"])
    assert "basic" in report["fidelity"]["per_family"]
    assert "worst_examples" not in report["fidelity"]["outliers"]
    assert report["partitions"][0]["fold_count"] == 2


def test_score_predictions_reports_outliers_and_categorical_accuracy():
    rows = [
        _PredictionRow(
            photo_id="first",
            confidence=0.9,
            entropy=0.1,
            actual={"exposure": 0.0, "white_balance_is_custom": 0.0},
            predicted={"exposure": 0.1, "white_balance_is_custom": 0.2},
        ),
        _PredictionRow(
            photo_id="second",
            confidence=0.8,
            entropy=0.2,
            actual={"exposure": 0.0, "white_balance_is_custom": 1.0},
            predicted={"exposure": 2.0, "white_balance_is_custom": 0.1},
        ),
    ]

    report = _score_predictions(
        rows,
        scales={"exposure": 1.0, "white_balance_is_custom": 1.0},
        catastrophic_error_threshold=0.5,
        include_photo_ids=True,
    )

    assert report["white_balance_accuracy"] == pytest.approx(0.5)
    assert report["outliers"]["count"] == 1
    assert report["outliers"]["worst_examples"][0]["photo_id"] == "second"


def test_outer_folds_preserve_minimum_training_support():
    assert _outer_fold_count(24, 3) == 3
    assert _outer_fold_count(13, 3) == 0
