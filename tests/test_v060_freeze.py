import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v060_freeze_binds_paired_benchmark_release():
    freeze = json.loads((ROOT / "profiles/v0.6.0.freeze.json").read_text())
    assert (freeze["release"], freeze["predecessor"], freeze["issue"]) == ("v0.6.0", "v0.5.6", 134)
    # Historical release evidence stays immutable as current code evolves.
    # Current runtime integrity is checked by test_portable_install.
    assert freeze["sha256"] == {
        "benchmark": "0324b05589fc6360808e591a98c9a7505faba187d7f7cd7a2235e5ab0ed8c15d",
        "benchmark_cli": "ec8968f79899133be6729a6fc7b1d7f3439380135cd9e1468024561cd36f0724",
        "dashboard": "155af4ad67e1609f227d828265f6a0a82b218ce8505843eec9054606b78792da",
        "manifest": "cce6769c9917cf2cec3a30c020c8f19c9eb074d43cde3ff5957cb08b9852a0b3",
        "paired_runner": "c2aef42265e193b8a23967f85b5df4eaa58805f7feb39d620cd6724f66c2ee1e",
        "routes": "49e706380975934b1230bd7fce800cbd5774e66830bca82b28307e0fad5460d4",
        "reviewer": "fbd641ffe4eaf707b4d61fcafc42cebc0d84b31958a22d84915cac6edf89f3dd",
        "worker": "da85d2f34d0bbcd583320ee38a93bc41d646b892cd30b7d8235f61b95f4dfddb",
        "dashboard_unit": "9baa7c77a7cd554fb62a4333f778b8fd58e762cba6d09e94a44a2bdd8e7ea819"
    }
