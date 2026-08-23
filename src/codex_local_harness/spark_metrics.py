from __future__ import annotations

import subprocess
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


MAX_SAMPLES = 60
NVIDIA_QUERY = (
    "name,utilization.gpu,temperature.gpu,power.draw,clocks.current.sm"
)


def _number(value: str) -> float | None:
    cleaned = value.strip()
    if cleaned in {"", "N/A", "[N/A]", "Not Supported"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_meminfo(text: str) -> dict[str, int | None]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        pieces = remainder.strip().split()
        try:
            values[key] = int(pieces[0]) * 1024
        except (IndexError, ValueError):
            continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    swap_total = values.get("SwapTotal")
    swap_free = values.get("SwapFree")
    return {
        "memory_total_bytes": total,
        "memory_available_bytes": available,
        "memory_used_bytes": total - available if total is not None and available is not None else None,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": swap_total - swap_free if swap_total is not None and swap_free is not None else None,
    }


def parse_nvidia_csv(text: str) -> dict[str, Any]:
    line = next((item for item in text.splitlines() if item.strip()), "")
    fields = [field.strip() for field in line.split(",")]
    if len(fields) != 5:
        raise ValueError("unexpected nvidia-smi field count")
    return {
        "gpu_name": fields[0][:128] or None,
        "gpu_utilization_percent": _number(fields[1]),
        "gpu_temperature_c": _number(fields[2]),
        "gpu_power_watts": _number(fields[3]),
        "gpu_sm_clock_mhz": _number(fields[4]),
    }


class SparkMetricsSampler:
    """Thread-safe, bounded, read-only DGX Spark telemetry sampler."""

    def __init__(
        self, *, meminfo: Path = Path("/proc/meminfo"),
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        max_samples: int = MAX_SAMPLES,
    ) -> None:
        if not 1 <= max_samples <= MAX_SAMPLES:
            raise ValueError(f"max_samples must be between 1 and {MAX_SAMPLES}")
        self.meminfo = meminfo
        self.runner = runner
        self.samples: deque[dict[str, Any]] = deque(maxlen=max_samples)
        self.lock = threading.Lock()

    def sample(self, now: datetime | None = None) -> dict[str, Any]:
        observed_at = (now or datetime.now(timezone.utc)).isoformat()
        current: dict[str, Any] = {
            "observed_at": observed_at,
            "source_state": "ok",
            "memory_semantics": "unified-system-memory",
            "diagnostics": [],
        }
        try:
            current.update(parse_meminfo(self.meminfo.read_text(encoding="utf-8")))
        except OSError:
            current.update({
                "memory_total_bytes": None, "memory_available_bytes": None,
                "memory_used_bytes": None, "swap_total_bytes": None, "swap_used_bytes": None,
            })
            current["diagnostics"].append("system memory telemetry unavailable")
        try:
            result = self.runner(
                ["nvidia-smi", f"--query-gpu={NVIDIA_QUERY}", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if result.returncode != 0:
                raise ValueError("nvidia-smi returned non-zero status")
            current.update(parse_nvidia_csv(result.stdout))
        except (OSError, subprocess.SubprocessError, ValueError):
            current.update({
                "gpu_name": None, "gpu_utilization_percent": None,
                "gpu_temperature_c": None, "gpu_power_watts": None,
                "gpu_sm_clock_mhz": None,
            })
            current["diagnostics"].append("GPU telemetry unavailable")
        if current["diagnostics"]:
            current["source_state"] = "degraded" if len(current["diagnostics"]) == 1 else "unavailable"
        with self.lock:
            self.samples.append(current)
            history = list(self.samples)
        return {**current, "samples": history, "sample_limit": self.samples.maxlen}
