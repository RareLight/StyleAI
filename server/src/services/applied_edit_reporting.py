"""Shared catalog-local orchestration for applied-edit quality reports."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import platform
from typing import Any, Callable

from .edit_history import iter_inference_history_batches
from .edit_quality_evaluation import evaluate_applied_edit_histories


DEFAULT_CONFIDENCE_BIN_COUNT = 5
DEFAULT_MINIMUM_REVIEWED_PER_GENERATION = 30


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_reusable_report(
    path: Path, *, dataset_fingerprint: str, parameters: dict[str, Any]
) -> dict[str, Any] | None:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(report, dict):
        return None
    if report.get("schema_version") != "applied-edit-quality-v2":
        return None
    if report.get("dataset_fingerprint") != dataset_fingerprint:
        return None
    if report.get("parameters") != parameters:
        return None
    return report


def _validate_options(
    *,
    created_after: str | None,
    confidence_bin_count: int,
    minimum_reviewed_per_generation: int,
) -> None:
    if created_after is not None:
        if not isinstance(created_after, str) or not created_after.strip():
            raise ValueError("created_after must be a non-empty ISO-8601 timestamp")
        try:
            datetime.fromisoformat(created_after.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("created_after must be an ISO-8601 timestamp") from exc
    if not isinstance(confidence_bin_count, int) or isinstance(
        confidence_bin_count, bool
    ):
        raise ValueError("confidence_bin_count must be an integer")
    if confidence_bin_count < 2 or confidence_bin_count > 20:
        raise ValueError("confidence_bin_count must be between 2 and 20")
    if not isinstance(minimum_reviewed_per_generation, int) or isinstance(
        minimum_reviewed_per_generation, bool
    ):
        raise ValueError("minimum_reviewed_per_generation must be an integer")
    if minimum_reviewed_per_generation < 1:
        raise ValueError("minimum_reviewed_per_generation must be positive")


def report_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded, Lightroom-facing summary of one full report."""
    dataset = report.get("dataset") or {}
    outcomes = report.get("user_outcomes") or {}
    reliability = report.get("application_reliability") or {}
    calibration = report.get("confidence_calibration") or {}
    generations = (report.get("generation_comparison") or {}).get("generations") or []
    family_metrics = (report.get("delivered_target_corrections") or {}).get(
        "per_family_normalized_absolute_error"
    ) or {}
    ranked_families = sorted(
        (
            {
                "family": str(family),
                "count": int(values.get("count") or 0),
                "mean_normalized_absolute_error": values.get("mean"),
            }
            for family, values in family_metrics.items()
            if isinstance(values, dict) and int(values.get("count") or 0) > 0
        ),
        key=lambda row: (
            -(float(row["mean_normalized_absolute_error"] or 0.0)),
            row["family"],
        ),
    )[:5]
    generation_summaries = []
    minimum = int(
        (report.get("generation_comparison") or {}).get(
            "minimum_reviewed_per_generation"
        )
        or DEFAULT_MINIMUM_REVIEWED_PER_GENERATION
    )
    for generation in generations:
        reviewed = int(generation.get("reviewed") or 0)
        generation_summaries.append(
            {
                "generation_id": generation.get("generation_id"),
                "reviewed": reviewed,
                "accepted": int(generation.get("accepted") or 0),
                "modified_and_kept": int(generation.get("modified_and_kept") or 0),
                "rejected": int(generation.get("rejected") or 0),
                "acceptance_rate": generation.get("acceptance_rate"),
                "kept_rate": generation.get("kept_rate"),
                "evidence_status": generation.get("evidence_status"),
                "reviews_needed": max(0, minimum - reviewed),
            }
        )
    burst = report.get("burst_coherence") or {}
    policy_agreement = burst.get("policy_agreement") or {}
    geometry = burst.get("geometry_disagreement") or {}
    warnings = []
    if int(policy_agreement.get("cross_policy_or_partition_leakage") or 0) > 0:
        warnings.append("Cross-policy or rendering-partition leakage was observed.")
    if int(geometry.get("disagreements") or 0) > 0:
        warnings.append("Burst members showed geometry disagreement.")
    if int(dataset.get("reviewed_inferences") or 0) == 0:
        warnings.append("No explicit AI edit reviews are available yet.")
    return {
        "schema_version": report.get("schema_version"),
        "dataset_fingerprint": report.get("dataset_fingerprint"),
        "dataset": {
            "inferences": int(dataset.get("inferences") or 0),
            "applied_inferences": int(dataset.get("applied_inferences") or 0),
            "reviewed_inferences": int(dataset.get("reviewed_inferences") or 0),
            "review_coverage": dataset.get("review_coverage"),
        },
        "application_reliability": {
            "apply_confirmed": int(reliability.get("apply_confirmed") or 0),
            "apply_unconfirmed": int(reliability.get("apply_unconfirmed") or 0),
            "apply_failed": int(reliability.get("apply_failed") or 0),
            "not_applied": int(reliability.get("not_applied") or 0),
            "confirmed_rate": reliability.get("confirmed_rate"),
        },
        "user_outcomes": {
            "accepted": int(outcomes.get("accepted") or 0),
            "modified_and_kept": int(outcomes.get("modified_and_kept") or 0),
            "rejected": int(outcomes.get("rejected") or 0),
            "acceptance_rate": outcomes.get("acceptance_rate"),
            "kept_rate": outcomes.get("kept_rate"),
            "rejection_rate": outcomes.get("rejection_rate"),
        },
        "confidence_calibration": {
            "sample_count": int(calibration.get("sample_count") or 0),
            "expected_calibration_error": calibration.get("expected_calibration_error"),
            "brier_score": calibration.get("brier_score"),
        },
        "generations": generation_summaries,
        "top_correction_families": ranked_families,
        "warnings": warnings,
        "deployment_status": "evaluation_only",
    }


def generate_applied_edit_report(
    *,
    db_path: str,
    output: str | Path | None = None,
    created_after: str | None = None,
    confidence_bin_count: int = DEFAULT_CONFIDENCE_BIN_COUNT,
    minimum_reviewed_per_generation: int = DEFAULT_MINIMUM_REVIEWED_PER_GENERATION,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Generate one versioned report and return its path plus bounded summary."""
    _validate_options(
        created_after=created_after,
        confidence_bin_count=confidence_bin_count,
        minimum_reviewed_per_generation=minimum_reviewed_per_generation,
    )
    database_path = Path(db_path).expanduser().resolve()
    if not database_path.is_dir():
        raise ValueError(f"database directory does not exist: {database_path}")
    histories = []
    for batch in iter_inference_history_batches(
        db_path=str(database_path),
        created_after=created_after,
    ):
        if cancel_check is not None and cancel_check():
            raise InterruptedError("applied-edit evaluation canceled")
        histories.extend(batch)
    if cancel_check is not None and cancel_check():
        raise InterruptedError("applied-edit evaluation canceled")
    report = evaluate_applied_edit_histories(
        histories,
        confidence_bin_count=confidence_bin_count,
        minimum_reviewed_per_generation=minimum_reviewed_per_generation,
    )
    parameters = {
        "created_after": created_after,
        "confidence_bin_count": confidence_bin_count,
        "minimum_reviewed_per_generation": minimum_reviewed_per_generation,
    }
    report_identity = hashlib.sha256(
        json.dumps(
            {
                "schema_version": report["schema_version"],
                "dataset_fingerprint": report["dataset_fingerprint"],
                "parameters": parameters,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    output_path = (
        Path(output).expanduser().resolve()
        if output is not None
        else database_path
        / "evaluation_reports"
        / f"applied-edits-{report_identity[:12]}.json"
    )
    reusable = _read_reusable_report(
        output_path,
        dataset_fingerprint=report["dataset_fingerprint"],
        parameters=parameters,
    )
    if reusable is not None:
        return {
            "report_path": str(output_path),
            "report": reusable,
            "summary": report_summary(reusable),
            "reused": True,
        }
    report["generated_at_utc"] = datetime.now(UTC).isoformat()
    report["report_fingerprint"] = report_identity
    report["system"] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    report["parameters"] = parameters
    if cancel_check is not None and cancel_check():
        raise InterruptedError("applied-edit evaluation canceled")
    _atomic_json(output_path, report)
    return {
        "report_path": str(output_path),
        "report": report,
        "summary": report_summary(report),
        "reused": False,
    }
