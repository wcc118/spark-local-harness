import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v055_freeze_binds_dual_model_runtime():
    freeze = json.loads((ROOT / "profiles/v0.5.5.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.5", "v0.5.4")
    assert freeze["sha256"] == {
        "worker": "1fa94387c247d565fc8d7bba5ecac4e12d81213d4ac64e689b31c487ec4a4796",
        "model_manager": "1a85bac8bc3b6c7049b6b2a4a54bb9d4217ce6a21403d91b74c2934d4b4536ac",
        "routes": "6af0ded9c02c996dca2b4b2c3d315fe3698d3283e2ad4d6bfaa63cb3091c6c8e",
        "promoter": "22aa29249be6ff25cafc17e11be958d61a2abc4032ff40e486e030bfbf04e79e",
        "rollback": "8f634eb22ecc1a095903fea1f0e24f00172938a08e212c9a765c43759d3e2d88",
    }
