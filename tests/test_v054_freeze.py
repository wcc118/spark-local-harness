import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v054_freeze_binds_bridged_tool_accounting():
    freeze = json.loads((ROOT / "profiles/v0.5.4.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.4", "v0.5.3")
    assert freeze["sha256"] == {
        "runtime_parser": "d9f767c02c49084e5c27dce2607706e268516b18ff6b91c89f4e27667df04223",
        "dashboard_unit": "1fa43a1450020b9d88668a5c7144a5d5a18c2b79595d64846ae63b07de2f0d5c",
        "worker": "e38ac6c8a42aaf278f6d788616a1ae8470588c2b066af2ebd3486881b968f663",
        "bridge": "54c5bc9269933072e46d844ae6ef35db968a452d0d8ef31688b8d398a7f89e1e",
    }
