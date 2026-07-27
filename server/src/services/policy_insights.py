"""Open-vocabulary descriptions and empirical policy coverage diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Iterable

import numpy as np
from sklearn.cluster import KMeans


@dataclass(frozen=True)
class DescriptorObservation:
    descriptor_kind: str
    descriptor: str
    provenance: str


@dataclass(frozen=True)
class PolicyDescriptor:
    policy_index: int
    descriptor_kind: str
    descriptor: str
    score: float
    provenance: str
    effective_support: float


@dataclass(frozen=True)
class CoverageBucket:
    policy_index: int
    dimension_key: str
    bucket_key: str
    effective_count: float
    coverage_score: float


_WHITESPACE = re.compile(r"\s+")


def _normalized_descriptor(value: str) -> tuple[str, str] | None:
    display = _WHITESPACE.sub(
        " ",
        unicodedata.normalize("NFKC", str(value)).strip(),
    )
    if not display or len(display) > 160:
        return None
    return display.casefold(), display


def _validated_responsibilities(
    responsibilities: np.ndarray,
    n_examples: int,
) -> np.ndarray:
    probabilities = np.asarray(responsibilities, dtype=np.float64)
    if probabilities.ndim != 2 or len(probabilities) != n_examples:
        raise ValueError("responsibilities must be an examples-by-policies matrix")
    if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0):
        raise ValueError("responsibilities must be finite and non-negative")
    row_sums = probabilities.sum(axis=1, keepdims=True)
    if np.any(row_sums <= 0):
        raise ValueError("every responsibility row must have positive mass")
    return probabilities / row_sums


def discover_open_vocabulary_descriptors(
    observations: list[list[DescriptorObservation]],
    responsibilities: np.ndarray,
    *,
    sample_weight: np.ndarray | None = None,
    minimum_effective_support: float = 2.0,
    maximum_per_policy: int = 12,
    smoothing: float = 0.5,
) -> list[PolicyDescriptor]:
    """Find arbitrary observed terms enriched within each discovered policy.

    No vocabulary or genre mapping is embedded here.  Inputs can come from
    user keywords, local-model captions/tags, or other explicitly identified
    local evidence, and provenance remains attached to every result.
    """
    if minimum_effective_support <= 0 or maximum_per_policy <= 0 or smoothing <= 0:
        raise ValueError("descriptor thresholds must be positive")
    probabilities = _validated_responsibilities(
        responsibilities,
        len(observations),
    )
    if sample_weight is None:
        weights = np.ones(len(observations), dtype=np.float64)
    else:
        weights = np.asarray(sample_weight, dtype=np.float64)
        if weights.shape != (len(observations),):
            raise ValueError("sample_weight must contain one value per example")
        if not np.all(np.isfinite(weights)) or np.any(weights <= 0):
            raise ValueError("sample weights must be finite and positive")

    descriptor_rows: dict[tuple[str, str, str], tuple[str, np.ndarray]] = {}
    for example_index, example_observations in enumerate(observations):
        seen: set[tuple[str, str, str]] = set()
        for observation in example_observations:
            normalized = _normalized_descriptor(observation.descriptor)
            kind = str(observation.descriptor_kind).strip()
            provenance = str(observation.provenance).strip()
            if normalized is None or not kind or not provenance:
                continue
            canonical, display = normalized
            key = (kind, canonical, provenance)
            if key in seen:
                continue
            seen.add(key)
            if key not in descriptor_rows:
                descriptor_rows[key] = (
                    display,
                    np.zeros(len(observations), dtype=np.float64),
                )
            descriptor_rows[key][1][example_index] = 1.0

    total_weight = float(np.sum(weights))
    policy_masses = np.sum(probabilities * weights[:, np.newaxis], axis=0)
    results: list[PolicyDescriptor] = []
    for policy_index, policy_mass in enumerate(policy_masses):
        other_mass = max(total_weight - float(policy_mass), 0.0)
        candidates: list[PolicyDescriptor] = []
        for (kind, _, provenance), (display, presence) in descriptor_rows.items():
            policy_support = float(
                np.sum(presence * weights * probabilities[:, policy_index])
            )
            if policy_support < minimum_effective_support:
                continue
            total_support = float(np.sum(presence * weights))
            other_support = max(total_support - policy_support, 0.0)
            policy_rate = (policy_support + smoothing) / (
                float(policy_mass) + 2.0 * smoothing
            )
            other_rate = (other_support + smoothing) / (other_mass + 2.0 * smoothing)
            enrichment = math.log(policy_rate / other_rate)
            if enrichment <= 0:
                continue
            support_fraction = policy_support / max(float(policy_mass), 1e-9)
            score = enrichment * math.sqrt(support_fraction)
            candidates.append(
                PolicyDescriptor(
                    policy_index=policy_index,
                    descriptor_kind=kind,
                    descriptor=display,
                    score=float(score),
                    provenance=provenance,
                    effective_support=policy_support,
                )
            )
        candidates.sort(
            key=lambda item: (
                -item.score,
                -item.effective_support,
                item.descriptor.casefold(),
                item.provenance,
            )
        )
        results.extend(candidates[:maximum_per_policy])
    return results


class PolicyCoverageDiagnostics:
    """Learn dynamic coverage buckets and score marginal candidate value."""

    def __init__(
        self,
        *,
        visual_component_count: int = 6,
        desired_effective_count: float = 5.0,
        quantile_count: int = 4,
        seed: int = 17,
    ):
        if (
            visual_component_count <= 0
            or desired_effective_count <= 0
            or quantile_count < 2
        ):
            raise ValueError("coverage configuration must be positive")
        self.visual_component_count = int(visual_component_count)
        self.desired_effective_count = float(desired_effective_count)
        self.quantile_count = int(quantile_count)
        self.seed = int(seed)

    def fit(
        self,
        source_features: np.ndarray,
        feature_names: Iterable[str],
        responsibilities: np.ndarray,
        *,
        categories: list[dict[str, str]] | None = None,
        numeric_dimensions: Iterable[str] | None = None,
        category_dimensions: Iterable[str] = (
            "hdr_state",
            "camera_model",
            "camera_profile",
        ),
        sample_weight: np.ndarray | None = None,
    ) -> "PolicyCoverageDiagnostics":
        source = np.asarray(source_features, dtype=np.float64)
        if source.ndim != 2 or len(source) == 0 or not np.all(np.isfinite(source)):
            raise ValueError("source_features must be a non-empty finite matrix")
        names = tuple(feature_names)
        if len(names) != source.shape[1] or len(set(names)) != len(names):
            raise ValueError("feature_names must uniquely describe every column")
        probabilities = _validated_responsibilities(responsibilities, len(source))
        if sample_weight is None:
            weights = np.ones(len(source), dtype=np.float64)
        else:
            weights = np.asarray(sample_weight, dtype=np.float64)
            if (
                weights.shape != (len(source),)
                or not np.all(np.isfinite(weights))
                or np.any(weights <= 0)
            ):
                raise ValueError("sample weights must be finite, positive, and aligned")
        category_rows = categories or [{} for _ in range(len(source))]
        if len(category_rows) != len(source):
            raise ValueError("categories must contain one mapping per example")

        self.feature_names_ = names
        self.source_mean_ = np.average(source, axis=0, weights=weights)
        variance = np.average(
            np.square(source - self.source_mean_),
            axis=0,
            weights=weights,
        )
        self.source_scale_ = np.sqrt(np.maximum(variance, 1e-8))
        standardized = (source - self.source_mean_) / self.source_scale_
        component_count = min(self.visual_component_count, len(source))
        self.visual_model_ = KMeans(
            n_clusters=component_count,
            random_state=self.seed,
            n_init=10,
        ).fit(standardized, sample_weight=weights)

        if numeric_dimensions is None:
            numeric_names = tuple(
                name
                for name in names
                if not name.startswith(("image_embedding_", "semantic_embedding_"))
            )
        else:
            numeric_names = tuple(numeric_dimensions)
        name_to_index = {name: index for index, name in enumerate(names)}
        unknown_numeric = set(numeric_names) - set(name_to_index)
        if unknown_numeric:
            raise ValueError(f"unknown numeric dimensions: {sorted(unknown_numeric)}")
        self.numeric_edges_: dict[str, np.ndarray] = {}
        for name in numeric_names:
            values = source[:, name_to_index[name]]
            edges = np.unique(
                np.quantile(
                    values,
                    np.linspace(0.0, 1.0, self.quantile_count + 1)[1:-1],
                )
            )
            if len(edges):
                self.numeric_edges_[name] = edges

        self.category_dimensions_ = tuple(category_dimensions)
        self.category_values_ = {
            dimension: tuple(
                sorted({str(row.get(dimension) or "unknown") for row in category_rows})
            )
            for dimension in self.category_dimensions_
        }
        row_buckets = self._bucket_keys(source, category_rows)
        self.bucket_counts_: dict[tuple[int, str, str], float] = {}
        self.bucket_universe_: dict[str, set[str]] = {}
        for dimension, bucket in row_buckets[0]:
            self.bucket_universe_.setdefault(dimension, set()).add(bucket)
        for buckets in row_buckets:
            for dimension, bucket in buckets:
                self.bucket_universe_.setdefault(dimension, set()).add(bucket)
        for policy_index in range(probabilities.shape[1]):
            for dimension, bucket_values in self.bucket_universe_.items():
                for bucket in bucket_values:
                    self.bucket_counts_[(policy_index, dimension, bucket)] = 0.0
            for row_index, buckets in enumerate(row_buckets):
                contribution = float(
                    weights[row_index] * probabilities[row_index, policy_index]
                )
                for dimension, bucket in buckets:
                    self.bucket_counts_[(policy_index, dimension, bucket)] += (
                        contribution
                    )
        self.policy_count_ = probabilities.shape[1]
        return self

    def _bucket_keys(
        self,
        source: np.ndarray,
        categories: list[dict[str, str]],
    ) -> list[list[tuple[str, str]]]:
        standardized = (source - self.source_mean_) / self.source_scale_
        visual_labels = self.visual_model_.predict(standardized)
        name_to_index = {name: index for index, name in enumerate(self.feature_names_)}
        rows: list[list[tuple[str, str]]] = []
        for row_index, visual_label in enumerate(visual_labels):
            buckets = [("visual_component", f"component_{int(visual_label):03d}")]
            for name, edges in self.numeric_edges_.items():
                bucket_index = int(
                    np.searchsorted(
                        edges,
                        source[row_index, name_to_index[name]],
                        side="right",
                    )
                )
                buckets.append((f"numeric:{name}", f"quantile_{bucket_index:02d}"))
            for dimension in self.category_dimensions_:
                value = str(categories[row_index].get(dimension) or "unknown")
                buckets.append((f"category:{dimension}", value))
            rows.append(buckets)
        return rows

    def records(self) -> list[CoverageBucket]:
        records = []
        for (policy_index, dimension, bucket), count in sorted(
            self.bucket_counts_.items()
        ):
            records.append(
                CoverageBucket(
                    policy_index=policy_index,
                    dimension_key=dimension,
                    bucket_key=bucket,
                    effective_count=count,
                    coverage_score=min(
                        1.0,
                        count / self.desired_effective_count,
                    ),
                )
            )
        return records

    def score_candidate_gain(
        self,
        source_features: np.ndarray,
        responsibilities: np.ndarray,
        *,
        categories: list[dict[str, str]] | None = None,
    ) -> np.ndarray:
        source = np.asarray(source_features, dtype=np.float64)
        if source.ndim != 2 or source.shape[1] != len(self.feature_names_):
            raise ValueError("candidate source feature shape is incompatible")
        probabilities = _validated_responsibilities(responsibilities, len(source))
        if probabilities.shape[1] != self.policy_count_:
            raise ValueError("candidate policy count is incompatible")
        category_rows = categories or [{} for _ in range(len(source))]
        if len(category_rows) != len(source):
            raise ValueError("categories must contain one mapping per candidate")
        row_buckets = self._bucket_keys(source, category_rows)
        gains = np.zeros_like(probabilities)
        for row_index, buckets in enumerate(row_buckets):
            for policy_index in range(self.policy_count_):
                deficits = [
                    1.0
                    - min(
                        1.0,
                        self.bucket_counts_.get(
                            (policy_index, dimension, bucket),
                            0.0,
                        )
                        / self.desired_effective_count,
                    )
                    for dimension, bucket in buckets
                ]
                gains[row_index, policy_index] = probabilities[
                    row_index, policy_index
                ] * float(np.mean(deficits))
        return gains
