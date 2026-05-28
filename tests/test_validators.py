"""
Unit tests for model.validators.validate_coherence.

Run with:
    python3 tests/test_validators.py
"""
import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from model.config import GentlyConfig, DiskConfig, PartitionConfig
import model.validators as validators


def test_percent_sum_over_100_fails():
    config = GentlyConfig(disks=[DiskConfig(
        device=None,
        partition_table="gpt",
        boot_mode="bios",
        partitions=[
            PartitionConfig(label="p1", size="60%", filesystem="ext4"),
            PartitionConfig(label="p2", size="50%", filesystem="ext4"),
        ],
    )])
    errors = validators.validate_coherence(config)
    assert any("exceeds 100%" in e for e in errors), errors
    print("PASS  percentage sizes >100% rejected")


def test_percent_sum_100_is_allowed():
    config = GentlyConfig(disks=[DiskConfig(
        device=None,
        partition_table="gpt",
        boot_mode="bios",
        partitions=[
            PartitionConfig(label="p1", size="50%", filesystem="ext4"),
            PartitionConfig(label="p2", size="50%", filesystem="ext4"),
        ],
    )])
    errors = validators.validate_coherence(config)
    assert not any("exceeds 100%" in e for e in errors), errors
    print("PASS  percentage sizes at 100% accepted")


def test_explicit_plus_percent_over_disk_fails():
    old = validators.disk_size_bytes
    try:
        validators.disk_size_bytes = lambda _dev: 1000  # bytes
        config = GentlyConfig(disks=[DiskConfig(
            device="/dev/test",
            partition_table="gpt",
            boot_mode="bios",
            partitions=[
                PartitionConfig(label="p1", size="800", filesystem="ext4"),
                PartitionConfig(label="p2", size="30%", filesystem="ext4"),
            ],
        )])
        errors = validators.validate_coherence(config)
        assert any("allocates" in e and "exceeds detected disk size" in e for e in errors), errors
        print("PASS  explicit + percentage overflow rejected")
    finally:
        validators.disk_size_bytes = old


if __name__ == "__main__":
    test_percent_sum_over_100_fails()
    test_percent_sum_100_is_allowed()
    test_explicit_plus_percent_over_disk_fails()
    print()
    print("All validator tests passed.")
