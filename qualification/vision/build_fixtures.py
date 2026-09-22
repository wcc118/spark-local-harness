"""Render the Phase 1 vision-gate fixtures from manifest.json.

Builds every fixture in `frozen` and `dev` by rendering its page with the
declared mutations, and writes a ground_truth.json next to the rendered
output. Deterministic: same manifest and renderer version produce the same
files. Development fixtures are kept in a separate directory and are never
scored against the frozen gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent / "src"))

from codex_local_harness.visual_render import render  # noqa: E402


def build_one(entry: dict, pages_dir: Path, out_dir: Path, viewport: dict) -> dict:
    page_path = pages_dir / entry["page"]
    if not page_path.is_file():
        raise FileNotFoundError(f"fixture page missing: {page_path}")
    spec = {
        "pages": [{"name": entry["id"], "url": f"file://{page_path}"}],
        "viewports": [viewport],
        "mutations": entry.get("mutations", []),
        "timeout_ms": 30000,
    }
    fixture_dir = out_dir / entry["id"]
    fixture_dir.mkdir(parents=True, exist_ok=True)
    summary = render(spec, fixture_dir)
    rendered_dir = fixture_dir / entry["id"]
    checks_path = rendered_dir / f"{viewport['name']}.checks.json"
    checks = json.loads(checks_path.read_text()) if checks_path.exists() else None
    png_path = rendered_dir / f"{viewport['name']}.png"
    ground_truth = {
        "id": entry["id"],
        "page": entry["page"],
        "category": entry["category"],
        "model_only": entry["model_only"],
        "defects": entry.get("defects", []),
        "numeric_fields": entry.get("numeric_fields", []),
        "description": entry.get("description", ""),
        "mutations_applied": entry.get("mutations", []),
        "png_sha256": hashlib.sha256(png_path.read_bytes()).hexdigest() if png_path.exists() else None,
        "baseline_a_summary": checks["summary"] if checks else None,
    }
    (fixture_dir / "ground_truth.json").write_text(
        json.dumps(ground_truth, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {"id": entry["id"], "render_summary": summary, "baseline_a": checks["summary"] if checks else None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.json")
    parser.add_argument("--pages", type=Path, default=ROOT / "pages")
    parser.add_argument("--out", type=Path, default=ROOT / "fixtures")
    parser.add_argument("--set", choices=("frozen", "dev", "both"), default="both")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    viewport = manifest["viewport"]
    results = []
    if args.set in ("frozen", "both"):
        for entry in manifest["frozen"]:
            results.append(build_one(entry, args.pages, args.out / "frozen", viewport))
    if args.set in ("dev", "both"):
        for entry in manifest["dev"]:
            results.append(build_one(entry, args.pages, args.out / "dev", viewport))

    for r in results:
        print(json.dumps(r, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
