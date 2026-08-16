from __future__ import annotations

import json
from pathlib import Path

from codex_local_harness.evidence_packet import detect_advisory_risk_signals


def main() -> None:
    fixture_path = Path(__file__).with_name("fixtures.json")
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))["cases"]
    results = []
    overhead_bytes = 0
    for case in cases:
        signals = detect_advisory_risk_signals(case["patch"], case["changed_files"])
        encoded = json.dumps({"advisory_risk_signals": signals}, separators=(",", ":"))
        overhead_bytes += len(encoded.encode("utf-8"))
        results.append({
            "name": case["name"],
            "expected_signal_count": case["expected_signal_count"],
            "observed_signal_count": len(signals),
            "matched": len(signals) == case["expected_signal_count"],
        })
    print(json.dumps({
        "cases": results,
        "all_matched": all(item["matched"] for item in results),
        "serialized_overhead_bytes": overhead_bytes,
        "estimated_overhead_tokens_upper_bound": overhead_bytes,
    }, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
