import numpy as np

from services.policy_recommendations import (
    PolicyCandidate,
    build_policy_recommendation_payload,
    rank_policy_candidates,
    retrieve_policy_neighbors,
)


def _candidate(
    photo_id,
    embedding,
    responsibilities=(0.9, 0.1),
    *,
    entropy=0.2,
    coverage=0.0,
    partition="sdr|camera-a|standard",
    **metadata,
):
    return PolicyCandidate(
        photo_id=photo_id,
        embedding=np.asarray(embedding, dtype=np.float64),
        metadata=metadata,
        responsibilities=np.asarray(responsibilities, dtype=np.float64),
        assignment_entropy=entropy,
        coverage_gain=coverage,
        hard_partition_key=partition,
    )


def test_ambiguous_membership_cannot_be_rescued_by_quality_or_coverage():
    candidates = [
        _candidate(
            "ambiguous",
            [1.0, 0.0, 0.0],
            responsibilities=(0.52, 0.48),
            entropy=0.99,
            coverage=1.0,
            rating=5,
            pick_status=1,
            is_edited=True,
        ),
        _candidate("admitted", [0.0, 1.0, 0.0]),
    ]
    selected, diagnostics = rank_policy_candidates(
        candidates,
        policy_index=0,
        target_count=5,
        hard_partition_key="sdr|camera-a|standard",
    )

    assert [item.photo_id for item in selected] == ["admitted"]
    assert diagnostics.ambiguous_count == 1


def test_partition_reject_and_training_duplicate_are_hard_gates():
    candidates = [
        _candidate("wrong-partition", [0.0, 1.0], partition="hdr|camera-a|standard"),
        _candidate("duplicate", [1.0, 0.0]),
        _candidate("valid", [0.0, 1.0]),
    ]
    selected, diagnostics = rank_policy_candidates(
        candidates,
        policy_index=0,
        target_count=5,
        existing_embeddings=[np.asarray([1.0, 0.0])],
        hard_partition_key="sdr|camera-a|standard",
    )

    assert [item.photo_id for item in selected] == ["valid"]
    assert diagnostics.partition_rejected_count == 1
    assert diagnostics.duplicate_rejected_count == 1


def test_burst_deduplication_keeps_user_preferred_hero():
    candidates = [
        _candidate(
            "unrated",
            [1.0, 0.0, 0.0],
            capture_time=100.0,
            rating=0,
        ),
        _candidate(
            "hero",
            [0.999, 0.02, 0.0],
            capture_time=104.0,
            rating=5,
            is_edited=True,
        ),
    ]
    selected, diagnostics = rank_policy_candidates(
        candidates,
        policy_index=0,
        target_count=5,
        hard_partition_key="sdr|camera-a|standard",
    )

    assert [item.photo_id for item in selected] == ["hero"]
    assert diagnostics.burst_suppressed_count == 1


def test_coverage_reranks_only_equally_admissible_candidates():
    candidates = [
        _candidate("covered", [1.0, 0.0, 0.0], coverage=0.0),
        _candidate("gap", [0.0, 1.0, 0.0], coverage=1.0),
    ]
    selected, _ = rank_policy_candidates(
        candidates,
        policy_index=0,
        target_count=2,
        hard_partition_key="sdr|camera-a|standard",
    )

    assert [item.photo_id for item in selected] == ["gap", "covered"]
    assert "fills_coverage_gap" in selected[0].reasons


def test_diversity_suppression_is_deterministic():
    candidates = [
        _candidate("a", [1.0, 0.0], coverage=0.5),
        _candidate("b", [0.999, 0.01], coverage=0.4),
        _candidate("c", [0.0, 1.0], coverage=0.3),
    ]
    selected, diagnostics = rank_policy_candidates(
        candidates,
        policy_index=0,
        target_count=3,
        hard_partition_key="sdr|camera-a|standard",
    )

    assert [item.photo_id for item in selected] == ["a", "c"]
    assert diagnostics.diversity_suppressed_count == 1


def test_explicit_reject_is_excluded_but_low_rating_is_only_ranked_lower():
    candidates = [
        _candidate("rejected", [1.0, 0.0, 0.0], pick_status=-1, rating=5),
        _candidate("low-rating", [0.0, 1.0, 0.0], rating=2),
    ]
    selected, diagnostics = rank_policy_candidates(
        candidates,
        policy_index=0,
        target_count=3,
        hard_partition_key="sdr|camera-a|standard",
    )

    assert [item.photo_id for item in selected] == ["low-rating"]
    assert diagnostics.quality_rejected_count == 1


def test_neighbor_retrieval_batches_anchors_and_merges_duplicate_hits():
    class FakeCollection:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "ids": [["existing", "shared", "first"], ["shared", "second"]],
                "metadatas": [
                    [{}, {"uuid": "shared-uuid"}, {"uuid": "first-uuid"}],
                    [{"uuid": "shared-uuid"}, {"uuid": "second-uuid"}],
                ],
                "distances": [[0.01, 0.20, 0.25], [0.10, 0.15]],
            }

    collection = FakeCollection()
    neighbors = retrieve_policy_neighbors(
        collection,
        [np.asarray([1.0, 0.0]), np.asarray([0.0, 1.0])],
        existing_photo_ids={"existing"},
        results_per_anchor=10,
    )

    assert len(collection.calls) == 1
    assert len(collection.calls[0]["query_embeddings"]) == 2
    assert [item.photo_id for item in neighbors] == ["shared", "second", "first"]
    assert neighbors[0].anchor_hits == 2
    assert neighbors[0].cosine_distance == 0.10


def test_neighbor_retrieval_enforces_global_candidate_bound():
    class FakeCollection:
        def query(self, **_kwargs):
            return {
                "ids": [["c", "a", "b"]],
                "metadatas": [[{}, {}, {}]],
                "distances": [[0.3, 0.1, 0.2]],
            }

    neighbors = retrieve_policy_neighbors(
        FakeCollection(),
        [np.asarray([1.0, 0.0])],
        maximum_candidates=2,
    )
    assert [item.photo_id for item in neighbors] == ["a", "b"]


def test_v2_payload_preserves_lightroom_compatibility_and_diagnostics():
    candidates = [
        _candidate("gap", [1.0, 0.0], coverage=1.0),
        _candidate("covered", [0.0, 1.0], coverage=0.0),
    ]
    ranked, diagnostics = rank_policy_candidates(
        candidates,
        policy_index=0,
        target_count=2,
        hard_partition_key="sdr|camera-a|standard",
    )
    payload = build_policy_recommendation_payload(
        policy_id="policy-1",
        policy_name="Warm restrained color",
        camera_profile="Adobe Standard",
        current_count=12,
        needed_count=8,
        ranked_candidates=ranked,
        diagnostics=diagnostics,
        policy_descriptors=[
            {
                "descriptor": "Copper tones",
                "score": 0.9,
                "provenance": "user",
            }
        ],
        photo_identities={"gap": {"lr_uuid": "uuid-gap"}},
    )

    assert payload["recommendation_version"] == "policy-v2"
    assert payload["style_id"] == payload["policy_id"]
    assert payload["style_name"] == payload["policy_name"]
    assert payload["recommended_photo_ids"][0]["globalPhotoId"] == "gap"
    assert payload["recommended_photo_ids"][0]["lr_uuid"] == "uuid-gap"
    assert payload["coverage_focused_count"] == 1
