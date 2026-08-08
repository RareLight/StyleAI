"""Catalog-local capture and export of recommendation review evidence."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from . import policy_store
from .policy_recommendation_evaluation import REVIEW_SCHEMA_VERSION
from .policy_recommendations import PolicyCandidate, RankedPolicyCandidate


RECOMMENDATION_VERSION = "policy-v2"
_RANKING_METADATA_KEYS = frozenset(
    {
        "burst_group_id",
        "capture_time",
        "dateTimeOriginal",
        "rating",
        "pick_status",
        "is_edited",
        "has_develop_settings",
    }
)


def _review_id(
    *,
    generation_id: str,
    policy_id: str,
    candidate_photo_ids: list[str],
) -> str:
    digest = hashlib.sha256()
    digest.update(generation_id.encode("utf-8"))
    digest.update(b"\0")
    digest.update(policy_id.encode("utf-8"))
    for photo_id in sorted(candidate_photo_ids):
        digest.update(b"\0")
        digest.update(photo_id.encode("utf-8"))
    return f"review-{digest.hexdigest()[:24]}"


def capture_recommendation_review(
    *,
    db_path: str,
    generation_id: str,
    policy_id: str,
    policy_index: int,
    hard_partition_key: str,
    target_count: int,
    existing_photo_ids: list[str],
    candidates: list[PolicyCandidate],
    ranked_candidates: list[RankedPolicyCandidate],
    algorithm_version: str,
    feature_schema_version: str,
    maximum_snapshot_candidates: int = 250,
) -> str | None:
    """Persist one bounded replay snapshot and return its stable review ID."""
    if (
        target_count <= 0
        or not candidates
        or not ranked_candidates
        or maximum_snapshot_candidates <= 0
    ):
        return None
    ranked_photo_ids = {candidate.photo_id for candidate in ranked_candidates}
    ordered_candidates = sorted(
        candidates,
        key=lambda candidate: (
            -float(np.asarray(candidate.responsibilities)[policy_index]),
            float(candidate.assignment_entropy),
            candidate.photo_id,
        ),
    )
    snapshot_candidates = [
        candidate
        for candidate in ordered_candidates
        if candidate.photo_id in ranked_photo_ids
    ]
    snapshot_photo_ids = {candidate.photo_id for candidate in snapshot_candidates}
    for candidate in ordered_candidates:
        if len(snapshot_candidates) >= maximum_snapshot_candidates:
            break
        if candidate.photo_id not in snapshot_photo_ids:
            snapshot_candidates.append(candidate)
            snapshot_photo_ids.add(candidate.photo_id)
    review_id = _review_id(
        generation_id=generation_id,
        policy_id=policy_id,
        candidate_photo_ids=[candidate.photo_id for candidate in snapshot_candidates],
    )
    rank_by_photo_id = {
        candidate.photo_id: index
        for index, candidate in enumerate(ranked_candidates, start=1)
    }
    rows = []
    for candidate in snapshot_candidates:
        metadata = {
            key: value
            for key, value in candidate.metadata.items()
            if key in _RANKING_METADATA_KEYS and value is not None
        }
        rows.append(
            {
                "photo_id": candidate.photo_id,
                "responsibilities": np.asarray(
                    candidate.responsibilities,
                    dtype=np.float64,
                ).tolist(),
                "assignment_entropy": float(candidate.assignment_entropy),
                "coverage_gain": float(candidate.coverage_gain),
                "hard_partition_key": candidate.hard_partition_key,
                "source_ambiguous": bool(candidate.source_ambiguous),
                "metadata": metadata,
                "recommended_rank": rank_by_photo_id.get(candidate.photo_id),
            }
        )
    connection = policy_store.connect_policy_store(db_path)
    try:
        policy_store.upsert_recommendation_review(
            connection,
            review_id=review_id,
            generation_id=generation_id,
            policy_id=policy_id,
            policy_index=policy_index,
            hard_partition_key=hard_partition_key,
            target_count=target_count,
            existing_photo_ids=existing_photo_ids,
            algorithm_version=algorithm_version,
            feature_schema_version=feature_schema_version,
            recommendation_version=RECOMMENDATION_VERSION,
            candidates=rows,
        )
    finally:
        connection.close()
    return review_id


def record_feedback(
    *,
    db_path: str,
    review_id: str,
    policy_id: str,
    labels: list[dict[str, Any]],
) -> dict[str, int]:
    connection = policy_store.connect_policy_store(db_path)
    try:
        return policy_store.record_recommendation_feedback(
            connection,
            review_id=review_id,
            policy_id=policy_id,
            labels=labels,
        )
    finally:
        connection.close()


def _embedding_map(collection: Any, photo_ids: list[str]) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for offset in range(0, len(photo_ids), 250):
        response = collection.get(
            ids=photo_ids[offset : offset + 250],
            include=["embeddings"],
        )
        response_ids = response.get("ids") or []
        embeddings = response.get("embeddings")
        if embeddings is None:
            embeddings = []
        for index, photo_id in enumerate(response_ids):
            if index >= len(embeddings) or embeddings[index] is None:
                continue
            values = np.asarray(embeddings[index], dtype=np.float64)
            if values.ndim == 1 and len(values) and np.all(np.isfinite(values)):
                result[str(photo_id)] = values.tolist()
    return result


def export_review_document(
    *,
    db_path: str,
    collection: Any,
) -> dict[str, Any]:
    """Materialize labelled review snapshots into the versioned analysis format."""
    connection = policy_store.connect_policy_store(db_path)
    try:
        stored_reviews = policy_store.list_recommendation_reviews(
            connection,
            labelled_only=True,
        )
    finally:
        connection.close()
    if not stored_reviews:
        raise ValueError("no labelled recommendation reviews are available")

    all_photo_ids = list(
        dict.fromkeys(
            photo_id
            for review in stored_reviews
            for photo_id in [
                *review["existing_photo_ids"],
                *(candidate["photo_id"] for candidate in review["candidates"]),
            ]
        )
    )
    embeddings = _embedding_map(collection, all_photo_ids)
    missing = [photo_id for photo_id in all_photo_ids if photo_id not in embeddings]
    if missing:
        preview = ", ".join(missing[:3])
        raise ValueError(
            f"{len(missing)} review embeddings are missing from this catalog "
            f"(first: {preview})"
        )

    reviews = []
    for stored in stored_reviews:
        candidates = []
        for item in stored["candidates"]:
            candidates.append(
                {
                    "photo_id": item["photo_id"],
                    "embedding": embeddings[item["photo_id"]],
                    "responsibilities": item["responsibilities"],
                    "assignment_entropy": item["assignment_entropy"],
                    "coverage_gain": item["coverage_gain"],
                    "hard_partition_key": item["hard_partition_key"],
                    "source_ambiguous": item["source_ambiguous"],
                    "metadata": item["metadata"],
                    "policy_match": item["policy_match"],
                    "useful": item["useful"],
                }
            )
        reviews.append(
            {
                "review_id": stored["review_id"],
                "policy_index": stored["policy_index"],
                "target_count": stored["target_count"],
                "hard_partition_key": stored["hard_partition_key"],
                "existing_embeddings": [
                    embeddings[photo_id] for photo_id in stored["existing_photo_ids"]
                ],
                "candidates": candidates,
            }
        )
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "reviews": reviews,
        "provenance": [
            {
                "review_id": stored["review_id"],
                "generation_id": stored["generation_id"],
                "policy_id": stored["policy_id"],
                "algorithm_version": stored["algorithm_version"],
                "feature_schema_version": stored["feature_schema_version"],
                "recommendation_version": stored["recommendation_version"],
            }
            for stored in stored_reviews
        ],
    }
