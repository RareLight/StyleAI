"""Cross-validated calibration for locally labelled policy recommendations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any

import numpy as np

from .policy_recommendations import PolicyCandidate, rank_policy_candidates


REVIEW_SCHEMA_VERSION = "policy-recommendation-review-v1"
CALIBRATION_SCHEMA_VERSION = "policy-recommendation-calibration-v1"


@dataclass(frozen=True)
class RankingConfiguration:
    minimum_confidence: float = 0.60
    minimum_margin: float = 0.15
    maximum_entropy: float = 0.80
    membership_weight: float = 0.65
    coverage_weight: float = 0.20
    quality_weight: float = 0.15

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewedCandidate:
    candidate: PolicyCandidate
    policy_match: bool | None
    useful: bool | None


@dataclass(frozen=True)
class RecommendationReview:
    review_id: str
    policy_index: int
    target_count: int
    hard_partition_key: str
    candidates: tuple[ReviewedCandidate, ...]
    existing_embeddings: np.ndarray | None = None


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true, false, or null")
    return value


def parse_review_document(value: dict[str, Any]) -> list[RecommendationReview]:
    """Parse the versioned local review interchange format."""
    if value.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise ValueError("unsupported recommendation review schema")
    raw_reviews = value.get("reviews")
    if not isinstance(raw_reviews, list) or not raw_reviews:
        raise ValueError("reviews must be a non-empty list")
    reviews: list[RecommendationReview] = []
    seen_ids: set[str] = set()
    for raw_review in raw_reviews:
        if not isinstance(raw_review, dict):
            raise ValueError("every review must be an object")
        review_id = str(raw_review.get("review_id") or "").strip()
        if not review_id or review_id in seen_ids:
            raise ValueError("review IDs must be non-empty and unique")
        seen_ids.add(review_id)
        policy_index = int(raw_review.get("policy_index", -1))
        target_count = int(raw_review.get("target_count", 0))
        if policy_index < 0 or target_count <= 0:
            raise ValueError("policy_index and target_count are invalid")
        partition_key = str(raw_review.get("hard_partition_key") or "default")
        raw_candidates = raw_review.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise ValueError("every review must contain candidates")
        candidates: list[ReviewedCandidate] = []
        seen_photo_ids: set[str] = set()
        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, dict):
                raise ValueError("every candidate must be an object")
            photo_id = str(raw_candidate.get("photo_id") or "").strip()
            if not photo_id or photo_id in seen_photo_ids:
                raise ValueError("candidate photo IDs must be non-empty and unique")
            seen_photo_ids.add(photo_id)
            embedding = np.asarray(raw_candidate.get("embedding"), dtype=np.float64)
            responsibilities = np.asarray(
                raw_candidate.get("responsibilities"),
                dtype=np.float64,
            )
            candidate = PolicyCandidate(
                photo_id=photo_id,
                embedding=embedding,
                metadata=dict(raw_candidate.get("metadata") or {}),
                responsibilities=responsibilities,
                assignment_entropy=float(raw_candidate.get("assignment_entropy")),
                coverage_gain=float(raw_candidate.get("coverage_gain", 0.0)),
                hard_partition_key=str(
                    raw_candidate.get("hard_partition_key") or partition_key
                ),
                source_ambiguous=bool(raw_candidate.get("source_ambiguous", False)),
            )
            candidates.append(
                ReviewedCandidate(
                    candidate=candidate,
                    policy_match=_optional_bool(
                        raw_candidate.get("policy_match"),
                        "policy_match",
                    ),
                    useful=_optional_bool(raw_candidate.get("useful"), "useful"),
                )
            )
        raw_existing = raw_review.get("existing_embeddings")
        existing = (
            None if raw_existing is None else np.asarray(raw_existing, dtype=np.float64)
        )
        reviews.append(
            RecommendationReview(
                review_id=review_id,
                policy_index=policy_index,
                target_count=target_count,
                hard_partition_key=partition_key,
                candidates=tuple(candidates),
                existing_embeddings=existing,
            )
        )
    return reviews


def _ndcg(
    relevance: list[bool], *, relevant_total: int, target_count: int
) -> float | None:
    if not relevance or relevant_total <= 0:
        return None
    gains = np.asarray(relevance, dtype=np.float64)
    discounts = np.log2(np.arange(2, len(gains) + 2))
    score = float(np.sum(gains / discounts))
    ideal_gains = np.zeros(len(gains), dtype=np.float64)
    ideal_gains[: min(relevant_total, target_count, len(gains))] = 1.0
    ideal = float(np.sum(ideal_gains / discounts))
    return score / ideal if ideal > 0 else None


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _wilson_interval(successes: int, total: int) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1.0 + z**2 / total
    center = (proportion + z**2 / (2.0 * total)) / denominator
    radius = (
        z
        * math.sqrt(proportion * (1.0 - proportion) / total + z**2 / (4.0 * total**2))
        / denominator
    )
    return max(0.0, center - radius), min(1.0, center + radius)


def evaluate_ranking_configuration(
    reviews: list[RecommendationReview],
    configuration: RankingConfiguration,
) -> dict[str, Any]:
    """Evaluate one configuration through the production ranker."""
    policy_true = 0
    policy_false = 0
    policy_positive_total = 0
    useful_true = 0
    useful_false = 0
    useful_positive_total = 0
    selected_count = 0
    target_slots = 0
    ndcg_values: list[float] = []
    for review in reviews:
        ranked, _ = rank_policy_candidates(
            [item.candidate for item in review.candidates],
            policy_index=review.policy_index,
            target_count=review.target_count,
            existing_embeddings=review.existing_embeddings,
            hard_partition_key=review.hard_partition_key,
            **configuration.as_dict(),
        )
        by_id = {item.candidate.photo_id: item for item in review.candidates}
        policy_positive_total += sum(
            item.policy_match is True for item in review.candidates
        )
        useful_positive_total += sum(
            item.policy_match is True and item.useful is True
            for item in review.candidates
        )
        relevance: list[bool] = []
        fully_usefulness_labelled = all(
            item.policy_match is not None and item.useful is not None
            for item in review.candidates
        )
        for selected in ranked:
            reviewed = by_id[selected.photo_id]
            selected_count += 1
            if reviewed.policy_match is True:
                policy_true += 1
            elif reviewed.policy_match is False:
                policy_false += 1
            if reviewed.policy_match is not None and reviewed.useful is not None:
                relevant = reviewed.policy_match and reviewed.useful
                useful_true += int(relevant)
                useful_false += int(not relevant)
                if fully_usefulness_labelled:
                    relevance.append(relevant)
        ndcg_value = _ndcg(
            relevance,
            relevant_total=sum(
                item.policy_match is True and item.useful is True
                for item in review.candidates
            ),
            target_count=review.target_count,
        )
        if ndcg_value is not None:
            ndcg_values.append(ndcg_value)
        target_slots += review.target_count
    policy_labeled_selected = policy_true + policy_false
    useful_labeled_selected = useful_true + useful_false
    policy_lower, policy_upper = _wilson_interval(
        policy_true,
        policy_labeled_selected,
    )
    useful_lower, useful_upper = _wilson_interval(
        useful_true,
        useful_labeled_selected,
    )
    return {
        "review_count": len(reviews),
        "selected_count": selected_count,
        "selection_rate": _safe_ratio(selected_count, target_slots),
        "policy_labeled_selected": policy_labeled_selected,
        "policy_precision": _safe_ratio(policy_true, policy_labeled_selected),
        "policy_precision_wilson_lower": policy_lower,
        "policy_precision_wilson_upper": policy_upper,
        "policy_leakage": _safe_ratio(policy_false, policy_labeled_selected),
        "policy_recall": _safe_ratio(policy_true, policy_positive_total),
        "useful_labeled_selected": useful_labeled_selected,
        "useful_precision": _safe_ratio(useful_true, useful_labeled_selected),
        "useful_precision_wilson_lower": useful_lower,
        "useful_precision_wilson_upper": useful_upper,
        "useful_recall": _safe_ratio(useful_true, useful_positive_total),
        "mean_ndcg": float(np.mean(ndcg_values)) if ndcg_values else None,
    }


def default_configuration_grid() -> list[RankingConfiguration]:
    configurations = []
    weight_sets = (
        (0.50, 0.30, 0.20),
        (0.60, 0.25, 0.15),
        (0.65, 0.20, 0.15),
        (0.70, 0.20, 0.10),
        (0.80, 0.10, 0.10),
    )
    for confidence in (0.55, 0.60, 0.70, 0.80):
        for margin in (0.10, 0.15, 0.25):
            for entropy in (0.60, 0.80):
                for membership, coverage, quality in weight_sets:
                    configurations.append(
                        RankingConfiguration(
                            minimum_confidence=confidence,
                            minimum_margin=margin,
                            maximum_entropy=entropy,
                            membership_weight=membership,
                            coverage_weight=coverage,
                            quality_weight=quality,
                        )
                    )
    return configurations


def _metric(value: float | None, fallback: float = -1.0) -> float:
    return fallback if value is None or not math.isfinite(value) else value


def _select_configuration(
    reviews: list[RecommendationReview],
    configurations: list[RankingConfiguration],
    *,
    target_policy_precision: float,
    minimum_labeled_selected: int,
) -> tuple[RankingConfiguration, dict[str, Any], bool]:
    evaluated = [
        (configuration, evaluate_ranking_configuration(reviews, configuration))
        for configuration in configurations
    ]
    qualified = [
        item
        for item in evaluated
        if item[1]["policy_labeled_selected"] >= minimum_labeled_selected
        and _metric(item[1]["policy_precision_wilson_lower"]) >= target_policy_precision
    ]
    pool = qualified or [
        item
        for item in evaluated
        if item[1]["policy_labeled_selected"] >= minimum_labeled_selected
    ]
    if not pool:
        pool = evaluated
    selected = max(
        pool,
        key=lambda item: (
            _metric(item[1]["useful_recall"]),
            _metric(item[1]["mean_ndcg"]),
            _metric(item[1]["policy_recall"]),
            _metric(item[1]["policy_precision"]),
            item[1]["selected_count"],
            tuple(-value for value in item[0].as_dict().values()),
        ),
    )
    return selected[0], selected[1], bool(qualified)


def _review_folds(
    reviews: list[RecommendationReview],
    requested_folds: int,
) -> list[tuple[list[RecommendationReview], list[RecommendationReview]]]:
    fold_count = min(requested_folds, len(reviews))
    if fold_count < 2:
        return []
    ordered = sorted(
        reviews,
        key=lambda review: (
            hashlib.sha256(review.review_id.encode("utf-8")).digest(),
            review.review_id,
        ),
    )
    buckets = [ordered[index::fold_count] for index in range(fold_count)]
    return [
        (
            [
                review
                for index, bucket in enumerate(buckets)
                if index != fold
                for review in bucket
            ],
            buckets[fold],
        )
        for fold in range(fold_count)
    ]


def calibrate_recommendations(
    reviews: list[RecommendationReview],
    *,
    target_policy_precision: float = 0.95,
    minimum_labeled_selected: int = 5,
    requested_folds: int = 3,
    configurations: list[RankingConfiguration] | None = None,
) -> dict[str, Any]:
    """Recommend parameters and estimate them with review-group holdouts."""
    if not 0 < target_policy_precision <= 1:
        raise ValueError("target_policy_precision must be in (0, 1]")
    if minimum_labeled_selected <= 0 or requested_folds < 2:
        raise ValueError("support and fold counts must be positive")
    if not reviews:
        raise ValueError("at least one review is required")
    grid = configurations or default_configuration_grid()
    if not grid:
        raise ValueError("at least one configuration is required")
    baseline = RankingConfiguration()
    baseline_metrics = evaluate_ranking_configuration(reviews, baseline)
    recommended, recommended_metrics, meets_target = _select_configuration(
        reviews,
        grid,
        target_policy_precision=target_policy_precision,
        minimum_labeled_selected=minimum_labeled_selected,
    )

    fold_reports = []
    held_out_metrics = []
    for train_reviews, test_reviews in _review_folds(reviews, requested_folds):
        selected, train_metrics, fold_meets_target = _select_configuration(
            train_reviews,
            grid,
            target_policy_precision=target_policy_precision,
            minimum_labeled_selected=min(
                minimum_labeled_selected,
                max(
                    1,
                    sum(len(review.candidates) for review in train_reviews),
                ),
            ),
        )
        test_metrics = evaluate_ranking_configuration(test_reviews, selected)
        held_out_metrics.append(test_metrics)
        fold_reports.append(
            {
                "configuration": selected.as_dict(),
                "training_metrics": train_metrics,
                "held_out_metrics": test_metrics,
                "training_met_precision_target": fold_meets_target,
            }
        )
    held_out_summary = None
    if held_out_metrics:
        held_out_summary = {
            key: (
                float(
                    np.mean(
                        [
                            value
                            for item in held_out_metrics
                            if (value := item[key]) is not None
                        ]
                    )
                )
                if any(item[key] is not None for item in held_out_metrics)
                else None
            )
            for key in (
                "selection_rate",
                "policy_precision",
                "policy_precision_wilson_lower",
                "policy_precision_wilson_upper",
                "policy_leakage",
                "policy_recall",
                "useful_precision",
                "useful_precision_wilson_lower",
                "useful_precision_wilson_upper",
                "useful_recall",
                "mean_ndcg",
            )
        }

    fingerprint_document = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "reviews": [
            {
                "review_id": review.review_id,
                "policy_index": review.policy_index,
                "target_count": review.target_count,
                "hard_partition_key": review.hard_partition_key,
                "existing_embeddings": (
                    None
                    if review.existing_embeddings is None
                    else review.existing_embeddings.tolist()
                ),
                "candidates": [
                    {
                        "photo_id": item.candidate.photo_id,
                        "embedding": item.candidate.embedding.tolist(),
                        "metadata": item.candidate.metadata,
                        "responsibilities": item.candidate.responsibilities.tolist(),
                        "assignment_entropy": item.candidate.assignment_entropy,
                        "coverage_gain": item.candidate.coverage_gain,
                        "hard_partition_key": item.candidate.hard_partition_key,
                        "source_ambiguous": item.candidate.source_ambiguous,
                        "policy_match": item.policy_match,
                        "useful": item.useful,
                    }
                    for item in review.candidates
                ],
            }
            for review in reviews
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_document,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "dataset_fingerprint": fingerprint,
        "review_count": len(reviews),
        "candidate_count": sum(len(review.candidates) for review in reviews),
        "configuration_count": len(grid),
        "target_policy_precision": target_policy_precision,
        "minimum_labeled_selected": minimum_labeled_selected,
        "baseline": {
            "configuration": baseline.as_dict(),
            "metrics": baseline_metrics,
        },
        "recommended": {
            "configuration": recommended.as_dict(),
            "metrics": recommended_metrics,
            "meets_precision_target": meets_target,
            "deployment_status": "evaluation_only",
        },
        "cross_validation": {
            "fold_count": len(fold_reports),
            "held_out_mean_metrics": held_out_summary,
            "folds": fold_reports,
        },
    }
