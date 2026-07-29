import numpy as np
import pytest

from benchmark import (
    fidelity_summary,
    latency_summary,
    parse_batch_sizes,
    parse_compute_units,
)


def test_parse_batch_sizes_requires_unique_positive_values():
    assert parse_batch_sizes("1,8,12,16") == (1, 8, 12, 16)
    with pytest.raises(Exception, match="unique positive"):
        parse_batch_sizes("1,0,1")


def test_parse_compute_units_normalizes_and_validates():
    assert parse_compute_units("all,cpu_and_ne") == ("ALL", "CPU_AND_NE")
    with pytest.raises(Exception, match="selected from"):
        parse_compute_units("CPU_ONLY")


def test_latency_summary_reports_batch_throughput_and_tail():
    result = latency_summary([0.10, 0.20, 0.15, 0.12], batch_size=8)

    assert result["median_ms"] == pytest.approx(135.0)
    assert result["p95_ms"] == pytest.approx(200.0)
    assert result["images_per_second"] == pytest.approx(8 / 0.135)


def test_fidelity_summary_uses_cosine_not_output_magnitude():
    reference = np.asarray([[1.0, 0.0], [0.0, 2.0]])
    candidate = np.asarray([[2.0, 0.0], [0.0, 1.0]])

    result = fidelity_summary(reference, candidate, minimum_cosine=0.9999)

    assert result["minimum_cosine"] == pytest.approx(1.0)
    assert result["passed"] is True


def test_fidelity_summary_rejects_directional_drift():
    reference = np.asarray([[1.0, 0.0]])
    candidate = np.asarray([[0.9, 0.2]])

    result = fidelity_summary(reference, candidate, minimum_cosine=0.9999)

    assert result["passed"] is False
