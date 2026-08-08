import numpy as np

from services import policy_feedback, policy_store
from services.policy_recommendation_evaluation import parse_review_document
from services.policy_recommendations import PolicyCandidate, RankedPolicyCandidate


class FakeCollection:
    def __init__(self, embeddings):
        self.embeddings = embeddings

    def get(self, *, ids, include):
        assert include == ["embeddings"]
        found = [photo_id for photo_id in ids if photo_id in self.embeddings]
        return {
            "ids": found,
            "embeddings": [self.embeddings[photo_id] for photo_id in found],
        }


def _candidate(photo_id):
    return PolicyCandidate(
        photo_id=photo_id,
        embedding=np.asarray([1.0, 0.0]),
        metadata={"rating": 5, "ignored_large_field": "not persisted"},
        responsibilities=np.asarray([0.9, 0.1]),
        assignment_entropy=0.2,
        coverage_gain=0.5,
        hard_partition_key="sdr",
    )


def test_capture_label_and_export_recommendation_review(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    ranked = RankedPolicyCandidate(
        photo_id="candidate-1",
        score=0.9,
        membership_confidence=0.9,
        membership_margin=0.8,
        quality_score=1.0,
        coverage_gain=0.5,
        reasons=("high policy membership",),
    )
    review_id = policy_feedback.capture_recommendation_review(
        db_path=db_path,
        generation_id="generation-1",
        policy_id="policy-1",
        policy_index=0,
        hard_partition_key="sdr",
        target_count=1,
        existing_photo_ids=["trained-1"],
        candidates=[_candidate("candidate-1")],
        ranked_candidates=[ranked],
        algorithm_version="v2",
        feature_schema_version="f1",
    )
    policy_feedback.record_feedback(
        db_path=db_path,
        review_id=review_id,
        policy_id="policy-1",
        labels=[
            {
                "photo_id": "candidate-1",
                "policy_match": True,
                "useful": True,
            }
        ],
    )
    document = policy_feedback.export_review_document(
        db_path=db_path,
        collection=FakeCollection({"trained-1": [0.0, 1.0], "candidate-1": [1.0, 0.0]}),
    )

    parsed = parse_review_document(document)
    assert len(parsed) == 1
    assert parsed[0].candidates[0].useful is True
    assert document["reviews"][0]["candidates"][0]["metadata"] == {"rating": 5}
    assert document["provenance"][0]["generation_id"] == "generation-1"


def test_export_fails_if_catalog_embedding_was_removed(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    connection = policy_store.connect_policy_store(db_path)
    try:
        policy_store.upsert_recommendation_review(
            connection,
            review_id="review-1",
            generation_id="generation-1",
            policy_id="policy-1",
            policy_index=0,
            hard_partition_key="sdr",
            target_count=1,
            existing_photo_ids=[],
            algorithm_version="v2",
            feature_schema_version="f1",
            recommendation_version="policy-v2",
            candidates=[
                {
                    "photo_id": "missing",
                    "responsibilities": [1.0],
                    "assignment_entropy": 0.0,
                    "policy_match": True,
                    "useful": True,
                }
            ],
        )
        policy_store.record_recommendation_feedback(
            connection,
            review_id="review-1",
            policy_id="policy-1",
            labels=[
                {
                    "photo_id": "missing",
                    "policy_match": True,
                    "useful": True,
                }
            ],
        )
    finally:
        connection.close()

    try:
        policy_feedback.export_review_document(
            db_path=db_path,
            collection=FakeCollection({}),
        )
    except ValueError as exc:
        assert "missing from this catalog" in str(exc)
    else:
        raise AssertionError("missing embeddings must fail export")
