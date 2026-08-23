from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from codex_local_harness.spark_metrics import SparkMetricsSampler, parse_meminfo, parse_nvidia_csv


NOW = datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc)


def test_parses_unified_memory_and_gpu_metrics() -> None:
    memory = parse_meminfo("MemTotal: 1000 kB\nMemAvailable: 250 kB\nSwapTotal: 100 kB\nSwapFree: 40 kB\n")
    assert memory == {
        "memory_total_bytes": 1_024_000, "memory_available_bytes": 256_000,
        "memory_used_bytes": 768_000, "swap_total_bytes": 102_400,
        "swap_used_bytes": 61_440,
    }
    assert parse_nvidia_csv("NVIDIA GB10, 87, 54, 42.5, 2411\n") == {
        "gpu_name": "NVIDIA GB10", "gpu_utilization_percent": 87.0,
        "gpu_temperature_c": 54.0, "gpu_power_watts": 42.5,
        "gpu_sm_clock_mhz": 2411.0,
    }


def test_sampler_is_bounded_and_contains_no_command_or_process_details(tmp_path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "NVIDIA GB10, 10, 43, 12, 2400\n", "secret stderr")

    sampler = SparkMetricsSampler(meminfo=meminfo, runner=runner, max_samples=2)
    for _ in range(3):
        snapshot = sampler.sample(NOW)
    assert len(snapshot["samples"]) == 2
    assert snapshot["memory_semantics"] == "unified-system-memory"
    encoded = json.dumps(snapshot)
    assert "secret stderr" not in encoded
    assert "nvidia-smi" not in encoded


def test_sampler_degrades_safely_when_sources_fail(tmp_path) -> None:
    def runner(*args, **kwargs):
        raise subprocess.TimeoutExpired("nvidia-smi", 2)

    snapshot = SparkMetricsSampler(meminfo=tmp_path / "missing", runner=runner).sample(NOW)
    assert snapshot["source_state"] == "unavailable"
    assert snapshot["gpu_utilization_percent"] is None
    assert snapshot["memory_total_bytes"] is None
