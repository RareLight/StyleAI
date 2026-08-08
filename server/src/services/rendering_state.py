"""Versioned camera-profile/HDR contracts and conservative local selectors."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

import numpy as np
from sklearn.metrics import (
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)
from sklearn.model_selection import GroupKFold


RENDERING_STATE_SCHEMA_VERSION = "rendering-state-v1"
RENDERING_SELECTOR_ALGORITHM_VERSION = "rendering-selector-centroid-v2"
RENDERING_SELECTOR_FEATURE_SCHEMA_VERSION = "neutral-source-embedding-v1"
MIN_CLASS_EXAMPLES = 6
MIN_SELECTIVE_COVERAGE = 0.20
SUGGEST_MIN_PRECISION = 0.75
SUGGEST_MIN_PRECISION_LOWER_BOUND = 0.60
PROFILE_MIN_PRECISION = 0.90
HDR_MIN_PRECISION = 0.95
PROFILE_MIN_PRECISION_LOWER_BOUND = 0.75
HDR_MIN_PRECISION_LOWER_BOUND = 0.80
MAX_SELECTOR_VALIDATION_EXAMPLES = 2000


def _clean(value: Any) -> str:
    return str(value or "").strip()


def camera_compatibility_key(camera_make: Any, camera_model: Any) -> str | None:
    make = _clean(camera_make).casefold()
    model = _clean(camera_model).casefold()
    if not make or not model:
        return None
    payload = f"{len(make)}:{make}|{len(model)}:{model}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _profile_representation(settings: dict[str, Any]) -> dict[str, Any]:
    representation: dict[str, Any] = {}
    for key in ("CameraProfile", "CameraProfileRaw", "Look"):
        value = settings.get(key)
        if value is not None:
            representation[key] = value
    return representation


def _profile_identity_representation(
    sdk_representation: dict[str, Any],
) -> dict[str, Any]:
    """Keep only stable identity-bearing fields when Lightroom supplies them."""
    identity: dict[str, Any] = {}
    for key in ("CameraProfile", "CameraProfileRaw"):
        value = sdk_representation.get(key)
        if value is not None:
            identity[key] = value
    look = sdk_representation.get("Look")
    if isinstance(look, dict):
        stable_look = {
            key: look[key]
            for key in ("UUID", "Id", "ID", "Name", "Amount")
            if key in look
        }
        identity["Look"] = stable_look or look
    elif look is not None:
        identity["Look"] = look
    return identity


def _display_name(representation: dict[str, Any], fallback: Any = None) -> str:
    look = representation.get("Look")
    if isinstance(look, dict) and _clean(look.get("Name")):
        return _clean(look["Name"])
    for key in ("CameraProfile", "CameraProfileRaw"):
        if _clean(representation.get(key)):
            return _clean(representation[key])
    return _clean(fallback) or "Unknown Profile"


def profile_id(
    *,
    camera_make: Any,
    camera_model: Any,
    sdk_representation: dict[str, Any],
) -> str:
    compatibility = camera_compatibility_key(camera_make, camera_model)
    if compatibility is None or not sdk_representation:
        return ""
    payload = json.dumps(
        {
            "camera": compatibility,
            "sdk": _profile_identity_representation(sdk_representation),
            "schema": RENDERING_STATE_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def rendering_state_from_settings(
    settings: dict[str, Any],
    *,
    camera_make: Any = None,
    camera_model: Any = None,
    legacy_profile: Any = None,
) -> dict[str, Any]:
    settings = settings if isinstance(settings, dict) else {}
    representation = _profile_representation(settings)
    legacy = _clean(legacy_profile)
    legacy_hdr = legacy.casefold().endswith(" + hdr")
    if legacy_hdr:
        legacy = legacy[:-6].rstrip()
    if not representation and legacy:
        representation = {"CameraProfile": legacy}
    display_name = _display_name(representation, legacy)
    is_hdr = settings.get("HDREditMode") in (1, True) or settings.get("HDR") in (
        1,
        True,
    )
    if not ("HDREditMode" in settings or "HDR" in settings):
        is_hdr = legacy_hdr
    compatibility = camera_compatibility_key(camera_make, camera_model)
    return {
        "schema_version": RENDERING_STATE_SCHEMA_VERSION,
        "profile": {
            "profile_id": profile_id(
                camera_make=camera_make,
                camera_model=camera_model,
                sdk_representation=representation,
            ),
            "display_name": display_name,
            "sdk_representation": representation,
            "camera_make": _clean(camera_make),
            "camera_model": _clean(camera_model),
            "compatibility_key": compatibility,
        },
        "is_hdr": bool(is_hdr),
    }


def rendering_state_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    raw = metadata.get("rendering_state") or metadata.get("rendering_state_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = None
    if (
        isinstance(raw, dict)
        and raw.get("schema_version") == RENDERING_STATE_SCHEMA_VERSION
    ):
        return raw
    settings = metadata.get("develop_settings")
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except (TypeError, ValueError):
            settings = {}
    state = rendering_state_from_settings(
        settings if isinstance(settings, dict) else {},
        camera_make=metadata.get("camera_make"),
        camera_model=metadata.get("camera_model"),
        legacy_profile=metadata.get("camera_profile"),
    )
    if "is_hdr" in metadata:
        state["is_hdr"] = bool(metadata["is_hdr"])
    return state


def rendering_partition_key(state: dict[str, Any]) -> str:
    profile = state.get("profile") if isinstance(state, dict) else {}
    identity = _clean((profile or {}).get("profile_id"))
    if not identity:
        identity = _clean((profile or {}).get("display_name")).casefold() or "default"
    return f"{'hdr' if bool(state.get('is_hdr')) else 'sdr'}|{identity}"


def states_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return rendering_partition_key(left) == rendering_partition_key(right)


@dataclass
class CentroidClassifier:
    labels: tuple[str, ...]
    centroids: np.ndarray
    threshold: float
    auto_threshold: float | None
    validation: dict[str, Any]

    def predict(
        self, embedding: Any, *, mode: str = "suggest"
    ) -> tuple[str | None, float, float, str | None]:
        vector = np.asarray(embedding, dtype=np.float64).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if not len(vector) or not np.all(np.isfinite(vector)) or norm <= 0:
            return None, 0.0, 1.0, "invalid_source_embedding"
        vector /= norm
        if self.centroids.shape[1] != len(vector):
            return None, 0.0, 1.0, "feature_schema_mismatch"
        similarities = self.centroids @ vector
        logits = (similarities - np.max(similarities)) / 0.05
        probabilities = np.exp(logits)
        probabilities /= np.sum(probabilities)
        index = int(np.argmax(probabilities))
        confidence = float(probabilities[index])
        entropy = float(
            -np.sum(probabilities * np.log(np.maximum(probabilities, 1e-12)))
            / max(math.log(len(probabilities)), 1.0)
        )
        threshold = (
            getattr(self, "auto_threshold", None) if mode == "auto" else self.threshold
        )
        if threshold is None:
            return None, confidence, entropy, "auto_not_validated"
        if confidence < threshold:
            return None, confidence, entropy, "low_confidence"
        return self.labels[index], confidence, entropy, None


@dataclass
class RenderingSelectorArtifact:
    generation_id: str
    algorithm_version: str
    feature_schema_version: str
    profiles: dict[str, dict[str, Any]]
    hdr_models: dict[str, CentroidClassifier]
    profile_models: dict[str, CentroidClassifier]
    validation: dict[str, Any]

    def select(
        self,
        *,
        embedding: Any,
        current_state: dict[str, Any],
        camera_make: Any,
        camera_model: Any,
        profile_mode: str,
        hdr_mode: str,
        source_provenance: str,
    ) -> dict[str, Any]:
        current = json.loads(json.dumps(current_state))
        proposed = json.loads(json.dumps(current_state))
        effective = json.loads(json.dumps(current_state))
        compatibility = camera_compatibility_key(camera_make, camera_model)
        reasons: list[str] = []
        hdr_confidence = profile_confidence = 0.0
        hdr_entropy = profile_entropy = 1.0
        if source_provenance != "raw_preview":
            reasons.append("source_not_neutral")
        elif compatibility is None:
            reasons.append("camera_compatibility_unknown")
        else:
            if hdr_mode != "off":
                hdr_model = self.hdr_models.get(compatibility)
                if hdr_model is None:
                    reasons.append("hdr_selector_unavailable")
                else:
                    label, hdr_confidence, hdr_entropy, reason = hdr_model.predict(
                        embedding, mode=hdr_mode
                    )
                    if label is None:
                        reasons.append(f"hdr_{reason}")
                    else:
                        proposed["is_hdr"] = label == "hdr"
                        if hdr_mode == "auto":
                            effective["is_hdr"] = proposed["is_hdr"]
            if profile_mode != "off":
                # Suggestions may describe the coherent proposed HDR/profile
                # combination.  Auto must instead choose a profile for the HDR
                # state Lightroom will actually use.  In particular, an HDR
                # suggestion must not silently steer Profile Auto into an HDR
                # partition while the effective photo remains SDR.
                profile_hdr_state = (
                    effective.get("is_hdr")
                    if profile_mode == "auto"
                    else proposed.get("is_hdr")
                )
                profile_model = self.profile_models.get(
                    f"{compatibility}|{'hdr' if profile_hdr_state else 'sdr'}"
                )
                if profile_model is None:
                    reasons.append("profile_selector_unavailable")
                else:
                    label, profile_confidence, profile_entropy, reason = (
                        profile_model.predict(embedding, mode=profile_mode)
                    )
                    profile = self.profiles.get(label or "")
                    if label is None or profile is None:
                        reasons.append(f"profile_{reason or 'unavailable'}")
                    elif profile.get("compatibility_key") != compatibility:
                        reasons.append("profile_incompatible")
                    else:
                        proposed["profile"] = profile

        if profile_mode == "auto" and not any(
            reason.startswith("profile_") or reason == "source_not_neutral"
            for reason in reasons
        ):
            effective["profile"] = proposed["profile"]
        return {
            "schema_version": RENDERING_STATE_SCHEMA_VERSION,
            "selector_algorithm_version": self.algorithm_version,
            "selector_feature_schema_version": self.feature_schema_version,
            "current": current,
            "proposed": proposed,
            "effective": effective,
            "profile_mode": profile_mode,
            "hdr_mode": hdr_mode,
            "profile_confidence": profile_confidence,
            "profile_entropy": profile_entropy,
            "hdr_confidence": hdr_confidence,
            "hdr_entropy": hdr_entropy,
            "abstention_reason": ",".join(dict.fromkeys(reasons)) or None,
        }


def _fit_centroids(
    x: np.ndarray, labels: np.ndarray
) -> tuple[tuple[str, ...], np.ndarray]:
    classes = tuple(sorted({str(value) for value in labels}))
    centroids = []
    for label in classes:
        centroid = np.mean(x[labels == label], axis=0)
        norm = np.linalg.norm(centroid)
        centroids.append(centroid / norm if norm > 0 else centroid)
    return classes, np.asarray(centroids, dtype=np.float64)


def _probabilities(x: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    similarities = x @ centroids.T
    logits = (similarities - np.max(similarities, axis=1, keepdims=True)) / 0.05
    values = np.exp(logits)
    return values / np.sum(values, axis=1, keepdims=True)


def _wilson_lower_bound(successes: int, total: int, *, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    proportion = successes / total
    denominator = 1.0 + (z * z / total)
    centre = proportion + (z * z / (2.0 * total))
    margin = z * math.sqrt(
        (proportion * (1.0 - proportion) + z * z / (4.0 * total)) / total
    )
    return float((centre - margin) / denominator)


def _bounded_group_indices(groups: np.ndarray, maximum: int) -> np.ndarray:
    """Bound validation without ever splitting a burst group."""
    if len(groups) <= maximum:
        return np.arange(len(groups), dtype=int)
    grouped: dict[str, list[int]] = {}
    for index, group in enumerate(groups):
        grouped.setdefault(str(group), []).append(index)
    ordered = sorted(
        grouped.items(),
        key=lambda item: hashlib.sha256(item[0].encode()).hexdigest(),
    )
    selected: list[int] = []
    for _, indices in ordered:
        if len(selected) + len(indices) > maximum:
            continue
        selected.extend(indices)
    return np.asarray(sorted(selected), dtype=int)


def _admission_metrics(
    *,
    admitted: np.ndarray,
    predicted: np.ndarray,
    truth: np.ndarray,
    classes: tuple[str, ...],
) -> dict[str, Any] | None:
    if not np.any(admitted):
        return None
    admitted_truth = truth[admitted]
    admitted_predictions = predicted[admitted]
    precision, recall, _, support = precision_recall_fscore_support(
        admitted_truth,
        admitted_predictions,
        labels=np.asarray(classes, dtype=object),
        zero_division=0,
    )
    per_class = {}
    for index, label in enumerate(classes):
        predicted_count = int(np.sum(admitted_predictions == label))
        correct_count = int(
            np.sum((admitted_predictions == label) & (admitted_truth == label))
        )
        per_class[label] = {
            "precision": float(precision[index]),
            "precision_lower_bound": _wilson_lower_bound(
                correct_count, predicted_count
            ),
            "recall": float(recall[index]),
            "support": int(support[index]),
            "predicted_count": predicted_count,
        }
    return {
        "selective_accuracy": float(np.mean(admitted_predictions == admitted_truth)),
        "coverage": float(np.mean(admitted)),
        "per_class": per_class,
        "balanced_accuracy": float(
            balanced_accuracy_score(admitted_truth, admitted_predictions)
        ),
        "macro_f1": float(
            f1_score(admitted_truth, admitted_predictions, average="macro")
        ),
        "confusion_matrix": confusion_matrix(
            admitted_truth,
            admitted_predictions,
            labels=np.asarray(classes, dtype=object),
        ).tolist(),
        "false_positive_hdr": int(
            np.sum((admitted_predictions == "hdr") & (admitted_truth != "hdr"))
        ),
        "abstention_rate": float(1.0 - np.mean(admitted)),
    }


def _find_threshold(
    *,
    confidence: np.ndarray,
    predicted: np.ndarray,
    truth: np.ndarray,
    classes: tuple[str, ...],
    majority_accuracy: float,
    minimum_precision: float,
    minimum_precision_lower_bound: float,
) -> tuple[float, dict[str, Any]] | None:
    for threshold in np.linspace(0.50, 0.99, 50):
        admitted = confidence >= threshold
        if float(np.mean(admitted)) < MIN_SELECTIVE_COVERAGE:
            continue
        metrics = _admission_metrics(
            admitted=admitted,
            predicted=predicted,
            truth=truth,
            classes=classes,
        )
        if metrics is None:
            continue
        per_class = metrics["per_class"].values()
        if (
            metrics["selective_accuracy"] >= minimum_precision
            and metrics["selective_accuracy"] > majority_accuracy + 0.02
            and all(
                item["predicted_count"] > 0
                and item["precision"] >= minimum_precision
                and item["precision_lower_bound"] >= minimum_precision_lower_bound
                for item in per_class
            )
        ):
            return float(threshold), metrics
    return None


def _validated_classifier(
    x: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    *,
    minimum_precision: float,
    minimum_precision_lower_bound: float,
) -> CentroidClassifier | None:
    classes, counts = np.unique(labels, return_counts=True)
    if len(classes) < 2 or int(np.min(counts)) < MIN_CLASS_EXAMPLES:
        return None
    validation_indices = _bounded_group_indices(
        groups, MAX_SELECTOR_VALIDATION_EXAMPLES
    )
    validation_x = x[validation_indices]
    validation_labels = labels[validation_indices]
    validation_groups = groups[validation_indices]
    validation_classes, validation_counts = np.unique(
        validation_labels, return_counts=True
    )
    if (
        tuple(str(value) for value in validation_classes)
        != tuple(str(value) for value in classes)
        or int(np.min(validation_counts)) < MIN_CLASS_EXAMPLES
    ):
        return None
    folds = min(5, len(np.unique(validation_groups)))
    if folds < 3:
        return None
    probabilities = np.zeros((len(validation_x), len(classes)), dtype=np.float64)
    fold_ids = np.full(len(validation_x), -1, dtype=int)
    fold_accuracies: list[float] = []
    class_order = tuple(str(value) for value in classes)
    for fold_index, (train, test) in enumerate(
        GroupKFold(n_splits=folds).split(
            validation_x, validation_labels, validation_groups
        )
    ):
        fold_classes, centroids = _fit_centroids(
            validation_x[train], validation_labels[train]
        )
        if fold_classes != class_order:
            return None
        probabilities[test] = _probabilities(validation_x[test], centroids)
        fold_ids[test] = fold_index
        fold_predictions = np.asarray(class_order, dtype=object)[
            np.argmax(probabilities[test], axis=1)
        ]
        fold_accuracies.append(
            float(np.mean(fold_predictions == validation_labels[test]))
        )
    predicted = np.asarray(class_order, dtype=object)[np.argmax(probabilities, axis=1)]
    confidence = np.max(probabilities, axis=1)
    labels_for_validation = validation_labels
    majority_accuracy = float(np.max(validation_counts) / np.sum(validation_counts))
    suggest_selection = _find_threshold(
        confidence=confidence,
        predicted=predicted,
        truth=labels_for_validation,
        classes=class_order,
        majority_accuracy=majority_accuracy,
        minimum_precision=SUGGEST_MIN_PRECISION,
        minimum_precision_lower_bound=SUGGEST_MIN_PRECISION_LOWER_BOUND,
    )
    if suggest_selection is None:
        return None
    selected_threshold, selected_metrics = suggest_selection

    # Cross-fit Auto admission: each held-out fold is evaluated using a
    # threshold selected only from the other folds' out-of-fold predictions.
    crossfit_admitted = np.zeros(len(validation_x), dtype=bool)
    crossfit_thresholds: list[float] = []
    for fold_index in range(folds):
        calibration = fold_ids != fold_index
        evaluation = fold_ids == fold_index
        selection = _find_threshold(
            confidence=confidence[calibration],
            predicted=predicted[calibration],
            truth=labels_for_validation[calibration],
            classes=class_order,
            majority_accuracy=float(
                np.max(
                    np.unique(labels_for_validation[calibration], return_counts=True)[1]
                )
                / np.sum(calibration)
            ),
            minimum_precision=minimum_precision,
            minimum_precision_lower_bound=0.0,
        )
        if selection is None:
            crossfit_thresholds = []
            break
        fold_threshold, _ = selection
        crossfit_thresholds.append(fold_threshold)
        crossfit_admitted[evaluation] = confidence[evaluation] >= fold_threshold
    auto_metrics = (
        _admission_metrics(
            admitted=crossfit_admitted,
            predicted=predicted,
            truth=labels_for_validation,
            classes=class_order,
        )
        if crossfit_thresholds
        else None
    )
    auto_threshold = None
    if auto_metrics is not None and (
        auto_metrics["coverage"] >= MIN_SELECTIVE_COVERAGE
        and auto_metrics["selective_accuracy"] >= minimum_precision
        and auto_metrics["selective_accuracy"] > majority_accuracy + 0.02
        and all(
            item["predicted_count"] > 0
            and item["precision"] >= minimum_precision
            and item["precision_lower_bound"] >= minimum_precision_lower_bound
            for item in auto_metrics["per_class"].values()
        )
    ):
        final_auto = _find_threshold(
            confidence=confidence,
            predicted=predicted,
            truth=labels_for_validation,
            classes=class_order,
            majority_accuracy=majority_accuracy,
            minimum_precision=minimum_precision,
            minimum_precision_lower_bound=minimum_precision_lower_bound,
        )
        if final_auto is not None:
            auto_threshold = final_auto[0]
    final_classes, final_centroids = _fit_centroids(x, labels)
    truth_indices = np.asarray(
        [class_order.index(str(value)) for value in labels_for_validation]
    )
    one_hot = np.eye(len(class_order), dtype=np.float64)[truth_indices]
    brier = float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))
    ece = 0.0
    for lower in np.linspace(0.0, 0.9, 10):
        in_bin = (confidence >= lower) & (confidence < lower + 0.1)
        if np.any(in_bin):
            ece += float(np.mean(in_bin)) * abs(
                float(np.mean(predicted[in_bin] == labels_for_validation[in_bin]))
                - float(np.mean(confidence[in_bin]))
            )
    return CentroidClassifier(
        labels=final_classes,
        centroids=final_centroids,
        threshold=selected_threshold,
        auto_threshold=auto_threshold,
        validation={
            **selected_metrics,
            "majority_accuracy": majority_accuracy,
            "class_counts": {str(k): int(v) for k, v in zip(classes, counts)},
            "grouped_folds": folds,
            "validation_sample_count": len(validation_indices),
            "final_fit_count": len(x),
            "auto_eligible": auto_threshold is not None,
            "auto_threshold": auto_threshold,
            "auto_crossfit": auto_metrics,
            "auto_crossfit_thresholds": crossfit_thresholds,
            "class_order": list(class_order),
            "brier_score": brier,
            "expected_calibration_error": ece,
            "fold_accuracy_mean": float(np.mean(fold_accuracies)),
            "fold_accuracy_std": float(np.std(fold_accuracies)),
        },
    )


def fit_rendering_selector(
    rows: list[dict[str, Any]], *, generation_id: str
) -> RenderingSelectorArtifact:
    safe = [
        row
        for row in rows
        if row.get("metadata", {}).get("source_provenance") == "raw_preview"
        and row.get("burst_group_id")
    ]
    profiles: dict[str, dict[str, Any]] = {}
    hdr_models: dict[str, CentroidClassifier] = {}
    profile_models: dict[str, CentroidClassifier] = {}
    validation: dict[str, Any] = {"safe_examples": len(safe), "hdr": {}, "profile": {}}
    compatibilities = sorted(
        {
            row["rendering_state"]["profile"].get("compatibility_key")
            for row in safe
            if row["rendering_state"]["profile"].get("compatibility_key")
        }
    )
    for compatibility in compatibilities:
        camera_rows = [
            row
            for row in safe
            if row["rendering_state"]["profile"].get("compatibility_key")
            == compatibility
        ]
        x = np.stack([row["normalized_embedding"] for row in camera_rows])
        labels = np.asarray(
            [
                "hdr" if row["rendering_state"]["is_hdr"] else "sdr"
                for row in camera_rows
            ],
            dtype=object,
        )
        groups = np.asarray(
            [row["burst_group_id"] for row in camera_rows], dtype=object
        )
        model = _validated_classifier(
            x,
            labels,
            groups,
            minimum_precision=HDR_MIN_PRECISION,
            minimum_precision_lower_bound=HDR_MIN_PRECISION_LOWER_BOUND,
        )
        if model is not None:
            hdr_models[compatibility] = model
            validation["hdr"][compatibility] = model.validation
        for is_hdr in (False, True):
            state_rows = [
                row for row in camera_rows if row["rendering_state"]["is_hdr"] == is_hdr
            ]
            if not state_rows:
                continue
            for row in state_rows:
                profile = row["rendering_state"]["profile"]
                if profile.get("profile_id"):
                    profiles[profile["profile_id"]] = profile
            px = np.stack([row["normalized_embedding"] for row in state_rows])
            plabels = np.asarray(
                [
                    row["rendering_state"]["profile"].get("profile_id", "")
                    for row in state_rows
                ],
                dtype=object,
            )
            pgroups = np.asarray(
                [row["burst_group_id"] for row in state_rows], dtype=object
            )
            pmodel = _validated_classifier(
                px,
                plabels,
                pgroups,
                minimum_precision=PROFILE_MIN_PRECISION,
                minimum_precision_lower_bound=PROFILE_MIN_PRECISION_LOWER_BOUND,
            )
            key = f"{compatibility}|{'hdr' if is_hdr else 'sdr'}"
            if pmodel is not None:
                profile_models[key] = pmodel
                validation["profile"][key] = pmodel.validation
    return RenderingSelectorArtifact(
        generation_id=generation_id,
        algorithm_version=RENDERING_SELECTOR_ALGORITHM_VERSION,
        feature_schema_version=RENDERING_SELECTOR_FEATURE_SCHEMA_VERSION,
        profiles=profiles,
        hdr_models=hdr_models,
        profile_models=profile_models,
        validation=validation,
    )
