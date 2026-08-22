import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_v060_freeze_binds_paired_benchmark_release():
    freeze = json.loads((ROOT / "profiles/v0.6.0.freeze.json").read_text())
    assert (freeze["release"], freeze["predecessor"], freeze["issue"]) == ("v0.6.0", "v0.5.6", 134)
    paths = {
        "benchmark":"src/codex_local_harness/benchmark.py",
        "benchmark_cli":"src/codex_local_harness/benchmark_cli.py",
        "dashboard":"src/codex_local_harness/dashboard.py",
        "manifest":"qualification/paired-model/manifest.json",
        "paired_runner":"qualification/paired-model/run.py",
        "routes":"config/workers-vllm.json",
        "reviewer":"agents/reviewer.md",
        "worker":"bin/codex-local-worker-v4",
        "dashboard_unit":"deployment/dashboard-backend.service",
    }
    assert freeze["sha256"] == {name:digest(path) for name, path in paths.items()}
