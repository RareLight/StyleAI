"""Tests for bounded hardware-aware ingestion recommendations."""

from config import get_index_resource_limits


def test_apple_silicon_memory_tiers_are_bounded():
    assert get_index_resource_limits(16, "darwin") == {
        "gpu_batch_size": 8,
        "queue_capacity": 32,
        "http_threads": 8,
    }
    assert get_index_resource_limits(32, "darwin") == {
        "gpu_batch_size": 12,
        "queue_capacity": 48,
        "http_threads": 12,
    }
    assert get_index_resource_limits(64, "darwin") == {
        "gpu_batch_size": 16,
        "queue_capacity": 64,
        "http_threads": 16,
    }


def test_non_macos_hosts_keep_conservative_defaults():
    assert get_index_resource_limits(128, "linux") == {
        "gpu_batch_size": 12,
        "queue_capacity": 48,
        "http_threads": 12,
    }
