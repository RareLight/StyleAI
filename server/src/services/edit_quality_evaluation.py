"""Versioned, catalog-local evaluation of real applied StyleAI edits."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import math
from typing import Any, Iterable

import numpy as np

from .edit_events import TERMINAL_USER_OUTCOMES


APPLIED_EDIT_QUALITY_SCHEMA_VERSION = "applied-edit-quality-v2"


def _target_family(key: str) -> str:
    if key == "white_balance_is_custom":
        return "white_balance"
    if key.startswith("crop_") or key == "rotation_is_applied":
        return "crop"
    if key.startswith("curve_") or key.startswith("tone_curve_"):
        return "tone_curve"
    if key.startswith("hsl_"):
        return "hsl"
    if key.startswith("cg_"):
        return "color_grading"
    if key.startswith(("sharpen", "noise_reduction", "color_noise_reduction")):
        return "detail"
    if key.startswith(("manual_distortion", "manual_vignette")):
        return "lens"
    if key.startswith(("vignette", "grain")):
        return "effects"
    return "basic"


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


def _finite_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None}
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": len(values),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "p95": float(np.quantile(array, 0.95)),
    }


def _latest_event(history: dict[str, Any], kinds: set[str]) -> dict[str, Any] | None:
    return next(
        (
            event
            for event in reversed(history["events"])
            if event["event_kind"] in kinds
        ),
        None,
    )


def _reviewed_rows(histories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for history in histories:
        outcome = _latest_event(history, set(TERMINAL_USER_OUTCOMES))
        if outcome is None:
            continue
        rows.append({"history": history, "outcome": outcome})
    return rows


def _target_scales(histories: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for history in histories:
        for key, value in history["target_state"].items():
            values[key].append(float(value))
    result = {}
    for key, target_values in values.items():
        array = np.asarray(target_values, dtype=np.float64)
        robust_range = float(np.quantile(array, 0.90) - np.quantile(array, 0.10))
        result[key] = max(robust_range, float(np.ptp(array)) * 0.05, 1.0)
    return result


def _correction_metrics(
    reviewed: list[dict[str, Any]],
    scales: dict[str, float],
) -> dict[str, Any]:
    corrections: dict[str, list[float]] = defaultdict(list)
    normalized_absolute: dict[str, list[float]] = defaultdict(list)
    row_errors: list[float] = []
    evaluated_rows = 0
    for row in reviewed:
        outcome = row["outcome"]
        if outcome["event_kind"] not in {"accepted", "modified_and_kept"}:
            continue
        observed = outcome.get("observed_state")
        if not isinstance(observed, dict):
            continue
        target = row["history"]["target_state"]
        squared = []
        for key in sorted(set(target) & set(observed)):
            correction = float(observed[key]) - float(target[key])
            corrections[key].append(correction)
            normalized = abs(correction) / scales[key]
            normalized_absolute[key].append(normalized)
            squared.append(normalized**2)
        if squared:
            evaluated_rows += 1
            row_errors.append(float(math.sqrt(np.mean(squared))))

    per_target = {}
    family_values: dict[str, list[float]] = defaultdict(list)
    for key, values in sorted(corrections.items()):
        array = np.asarray(values, dtype=np.float64)
        normalized = normalized_absolute[key]
        family_values[_target_family(key)].extend(normalized)
        per_target[key] = {
            "count": len(values),
            "mean_correction": float(np.mean(array)),
            "raw_rmse": float(np.sqrt(np.mean(np.square(array)))),
            "normalized_mae": float(np.mean(normalized)),
            "normalization_scale": scales[key],
        }
    return {
        "evaluated_reviews": evaluated_rows,
        "row_normalized_rmse": _finite_summary(row_errors),
        "per_target": per_target,
        "per_family_normalized_absolute_error": {
            family: _finite_summary(values)
            for family, values in sorted(family_values.items())
        },
    }


def _group_outcomes(
    reviewed: list[dict[str, Any]],
    field: str,
) -> list[dict[str, Any]]:
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    for row in reviewed:
        value = row["history"].get(field)
        key = "unassigned" if value in {None, ""} else str(value)
        groups[key][row["outcome"]["event_kind"]] += 1
    return [
        {
            field: key,
            "reviewed": sum(counts.values()),
            "accepted": counts["accepted"],
            "modified_and_kept": counts["modified_and_kept"],
            "rejected": counts["rejected"],
            "acceptance_rate": _safe_ratio(counts["accepted"], sum(counts.values())),
        }
        for key, counts in sorted(groups.items())
    ]


def _confidence_calibration(
    reviewed: list[dict[str, Any]],
    *,
    bin_count: int = 5,
) -> dict[str, Any]:
    if bin_count < 2 or bin_count > 20:
        raise ValueError("bin_count must be between 2 and 20")
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(bin_count)]
    for row in reviewed:
        confidence = row["history"].get("confidence")
        if confidence is None:
            continue
        confidence = float(confidence)
        if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
            continue
        accepted = row["outcome"]["event_kind"] == "accepted"
        index = min(int(confidence * bin_count), bin_count - 1)
        bins[index].append((confidence, accepted))

    reports = []
    absolute_calibration_error = 0.0
    brier_terms = []
    sample_count = sum(len(values) for values in bins)
    for index, values in enumerate(bins):
        accepted_count = sum(accepted for _, accepted in values)
        mean_confidence = (
            float(np.mean([confidence for confidence, _ in values])) if values else None
        )
        acceptance_rate = _safe_ratio(accepted_count, len(values))
        if values:
            absolute_calibration_error += (
                len(values)
                / sample_count
                * abs(float(mean_confidence) - float(acceptance_rate))
            )
            brier_terms.extend(
                (confidence - float(accepted)) ** 2 for confidence, accepted in values
            )
        reports.append(
            {
                "lower": index / bin_count,
                "upper": (index + 1) / bin_count,
                "count": len(values),
                "mean_confidence": mean_confidence,
                "acceptance_rate": acceptance_rate,
            }
        )
    return {
        "definition": "probability_of_acceptance_without_modeled_changes",
        "sample_count": sample_count,
        "expected_calibration_error": (
            absolute_calibration_error if sample_count else None
        ),
        "brier_score": float(np.mean(brier_terms)) if brier_terms else None,
        "bins": reports,
    }


def _generation_comparison(
    reviewed: list[dict[str, Any]],
    *,
    minimum_reviewed: int = 30,
) -> dict[str, Any]:
    if minimum_reviewed < 1:
        raise ValueError("minimum_reviewed must be positive")
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for row in reviewed:
        generation = row["history"].get("generation_id") or "unassigned"
        grouped[str(generation)][row["outcome"]["event_kind"]] += 1
    generations = []
    for generation_id, counts in sorted(grouped.items()):
        reviewed_count = sum(counts.values())
        accepted = counts["accepted"]
        kept = accepted + counts["modified_and_kept"]
        acceptance_interval = _wilson_interval(accepted, reviewed_count)
        kept_interval = _wilson_interval(kept, reviewed_count)
        generations.append(
            {
                "generation_id": generation_id,
                "reviewed": reviewed_count,
                "accepted": accepted,
                "modified_and_kept": counts["modified_and_kept"],
                "rejected": counts["rejected"],
                "acceptance_rate": _safe_ratio(accepted, reviewed_count),
                "acceptance_wilson_95": list(acceptance_interval),
                "kept_rate": _safe_ratio(kept, reviewed_count),
                "kept_wilson_95": list(kept_interval),
                "evidence_status": (
                    "sufficient_for_comparison"
                    if reviewed_count >= minimum_reviewed
                    else "insufficient_reviews"
                ),
            }
        )
    comparable = sum(
        row["evidence_status"] == "sufficient_for_comparison" for row in generations
    )
    return {
        "minimum_reviewed_per_generation": minimum_reviewed,
        "comparable_generation_count": comparable,
        "deployment_status": "evaluation_only",
        "generations": generations,
    }


def _burst_coherence_report(
    histories: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
    scales: dict[str, float],
) -> dict[str, Any]:
    tiers = Counter(
        str(history.get("reuse_tier") or "independent") for history in histories
    )
    eligible = [history for history in histories if history.get("burst_group_id")]
    admitted = [
        history
        for history in histories
        if history.get("reuse_tier") in {"policy_coherent", "global_target_reuse"}
    ]
    fallback_reasons = Counter(
        str(history["burst_fallback_reason"])
        for history in histories
        if history.get("burst_fallback_reason")
    )
    leakage = 0
    policy_agreement_samples = 0
    for history in admitted:
        agreement = history.get("policy_agreement") or {}
        if "same_policy" in agreement or "same_partition" in agreement:
            policy_agreement_samples += 1
            if not agreement.get("same_policy") or not agreement.get("same_partition"):
                leakage += 1

    geometry_disagreements = 0
    geometry_comparisons = 0
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for history in histories:
        if history.get("burst_group_id"):
            groups[str(history["burst_group_id"])].append(history)
    for members in groups.values():
        by_photo = {str(member["photo_id"]): member for member in members}
        for member in members:
            representative = by_photo.get(
                str(member.get("representative_photo_id") or "")
            )
            if representative is None or representative is member:
                continue
            member_target = member.get("absolute_target") or {}
            representative_target = representative.get("absolute_target") or {}
            geometry_comparisons += 1
            if member_target.get("crop") != representative_target.get("crop"):
                geometry_disagreements += 1

    per_tier_corrections = {}
    for tier in ("independent", "policy_coherent", "global_target_reuse"):
        tier_reviewed = [
            row
            for row in reviewed
            if str(row["history"].get("reuse_tier") or "independent") == tier
        ]
        per_tier_corrections[tier] = _correction_metrics(tier_reviewed, scales)
    return {
        "contract_version": "edit-burst-evaluation-v1",
        "eligible_photos": len(eligible),
        "admitted_photos": len(admitted),
        "selective_coverage": _safe_ratio(len(admitted), len(eligible)),
        "tier_counts": dict(sorted(tiers.items())),
        "fallback_reasons": dict(sorted(fallback_reasons.items())),
        "policy_agreement": {
            "evaluated_members": policy_agreement_samples,
            "cross_policy_or_partition_leakage": leakage,
            "leakage_rate": _safe_ratio(leakage, policy_agreement_samples),
        },
        "geometry_disagreement": {
            "comparisons": geometry_comparisons,
            "disagreements": geometry_disagreements,
            "rate": _safe_ratio(geometry_disagreements, geometry_comparisons),
            "geometry_is_never_reused": True,
        },
        "per_tier_delivered_target_corrections": per_tier_corrections,
        "avoided_policy_predictions": 0,
        "timing": {
            "available_in_immutable_history": False,
            "source": "bounded service and Lightroom logs",
        },
        "deployment_status": "evaluation_only",
    }


def _rendering_value(state: dict[str, Any], dimension: str) -> Any:
    if dimension == "hdr":
        return bool(state.get("is_hdr"))
    profile = state.get("profile") or {}
    return profile.get("profile_id") or profile.get("display_name")


def _rendering_dimension_report(
    reviewed: list[dict[str, Any]],
    *,
    dimension: str,
    minimum_reviewed: int,
) -> dict[str, Any]:
    auto: Counter[str] = Counter()
    suggest: Counter[str] = Counter()
    activation: Counter[str] = Counter()
    for row in reviewed:
        history = row["history"]
        intent = history.get("rendering_intent") or {}
        mode = intent.get("hdr_mode" if dimension == "hdr" else "profile_mode")
        if mode not in {"suggest", "auto"}:
            continue
        current = intent.get("current") or history.get("current_rendering_state") or {}
        proposed = intent.get("proposed") or current
        effective = (
            intent.get("effective") or history.get("target_rendering_state") or current
        )
        observed = row["outcome"].get("details", {}).get("observed_rendering_state")
        if not isinstance(observed, dict):
            continue
        current_value = _rendering_value(current, dimension)
        proposed_value = _rendering_value(proposed, dimension)
        effective_value = _rendering_value(effective, dimension)
        observed_value = _rendering_value(observed, dimension)
        if mode == "suggest":
            if proposed_value == current_value:
                continue
            decision = (
                "manually_selected"
                if observed_value == proposed_value
                else "left_current"
                if observed_value == current_value
                else "replaced"
            )
            suggest[decision] += 1
            continue
        if effective_value == current_value:
            continue
        decision = (
            "accepted"
            if observed_value == effective_value
            else "returned_to_original"
            if observed_value == current_value
            else "replaced"
        )
        auto[decision] += 1
        if dimension == "hdr" and current_value is False and effective_value is True:
            activation[decision] += 1

    auto_total = sum(auto.values())
    suggest_total = sum(suggest.values())
    auto_interval = _wilson_interval(auto["accepted"], auto_total)
    suggest_interval = _wilson_interval(suggest["manually_selected"], suggest_total)
    report = {
        "auto": {
            "reviewed_decisions": auto_total,
            "accepted": auto["accepted"],
            "returned_to_original": auto["returned_to_original"],
            "replaced": auto["replaced"],
            "acceptance_rate": _safe_ratio(auto["accepted"], auto_total),
            "acceptance_wilson_95": list(auto_interval),
            "evidence_status": (
                "sufficient_for_comparison"
                if auto_total >= minimum_reviewed
                else "insufficient_reviews"
            ),
        },
        "suggest": {
            "reviewed_proposals": suggest_total,
            "manually_selected": suggest["manually_selected"],
            "left_current": suggest["left_current"],
            "replaced": suggest["replaced"],
            "manual_selection_rate": _safe_ratio(
                suggest["manually_selected"], suggest_total
            ),
            "manual_selection_wilson_95": list(suggest_interval),
            "interpretation": (
                "Leaving a suggestion unchanged is not an explicit rejection."
            ),
        },
    }
    if dimension == "hdr":
        activation_total = sum(activation.values())
        report["hdr_activation"] = {
            "reviewed_activations": activation_total,
            "accepted": activation["accepted"],
            "returned_to_original": activation["returned_to_original"],
            "replaced": activation["replaced"],
            "return_rate": _safe_ratio(
                activation["returned_to_original"], activation_total
            ),
            "return_rate_wilson_95": list(
                _wilson_interval(activation["returned_to_original"], activation_total)
            ),
        }
    return report


def evaluate_applied_edit_histories(
    histories: Iterable[dict[str, Any]],
    *,
    confidence_bin_count: int = 5,
    minimum_reviewed_per_generation: int = 30,
) -> dict[str, Any]:
    """Measure observable reliability and explicit user outcomes."""
    rows = list(histories)
    for history in rows:
        if not history.get("inference_id") or not isinstance(
            history.get("events"), list
        ):
            raise ValueError("every inference history must be complete")
    rows.sort(key=lambda item: (item["created_at"], item["inference_id"]))
    digest = hashlib.sha256(APPLIED_EDIT_QUALITY_SCHEMA_VERSION.encode("utf-8"))
    for history in rows:
        digest.update(history["inference_id"].encode("utf-8"))
        for event in history["events"]:
            digest.update(event["event_id"].encode("utf-8"))

    application_counts: Counter[str] = Counter()
    applied_count = 0
    for history in rows:
        application = _latest_event(
            history,
            {"apply_confirmed", "apply_unconfirmed", "apply_failed", "not_applied"},
        )
        if application:
            application_counts[application["event_kind"]] += 1
            applied_count += int(
                application["event_kind"] in {"apply_confirmed", "apply_unconfirmed"}
            )
    reviewed = _reviewed_rows(rows)
    outcomes = Counter(row["outcome"]["event_kind"] for row in reviewed)
    scales = _target_scales(rows)
    return {
        "schema_version": APPLIED_EDIT_QUALITY_SCHEMA_VERSION,
        "dataset_fingerprint": digest.hexdigest(),
        "dataset": {
            "inferences": len(rows),
            "applied_inferences": applied_count,
            "reviewed_inferences": len(reviewed),
            "review_coverage": _safe_ratio(len(reviewed), applied_count),
        },
        "application_reliability": {
            "apply_confirmed": application_counts["apply_confirmed"],
            "apply_unconfirmed": application_counts["apply_unconfirmed"],
            "apply_failed": application_counts["apply_failed"],
            "not_applied": application_counts["not_applied"],
            "confirmed_rate": _safe_ratio(
                application_counts["apply_confirmed"],
                applied_count,
            ),
        },
        "user_outcomes": {
            "accepted": outcomes["accepted"],
            "modified_and_kept": outcomes["modified_and_kept"],
            "rejected": outcomes["rejected"],
            "acceptance_rate": _safe_ratio(outcomes["accepted"], len(reviewed)),
            "kept_rate": _safe_ratio(
                outcomes["accepted"] + outcomes["modified_and_kept"],
                len(reviewed),
            ),
            "rejection_rate": _safe_ratio(outcomes["rejected"], len(reviewed)),
        },
        "rendering_outcomes": {
            "profile": _rendering_dimension_report(
                reviewed,
                dimension="profile",
                minimum_reviewed=minimum_reviewed_per_generation,
            ),
            "hdr": _rendering_dimension_report(
                reviewed,
                dimension="hdr",
                minimum_reviewed=minimum_reviewed_per_generation,
            ),
            "slider_metrics_are_separate": True,
            "deployment_status": "evaluation_only",
        },
        "burst_coherence": _burst_coherence_report(rows, reviewed, scales),
        "delivered_target_corrections": _correction_metrics(reviewed, scales),
        "confidence_calibration": _confidence_calibration(
            reviewed,
            bin_count=confidence_bin_count,
        ),
        "generation_comparison": _generation_comparison(
            reviewed,
            minimum_reviewed=minimum_reviewed_per_generation,
        ),
        "breakdowns": {
            field: _group_outcomes(reviewed, field)
            for field in (
                "generation_id",
                "policy_id",
                "engine",
                "hard_partition_key",
            )
        },
    }
