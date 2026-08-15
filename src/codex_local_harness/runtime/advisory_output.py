from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..roles import normalize_single_json_fence


def extract_advisory_object(path: Path) -> str:
    texts: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid OpenCode event at line {line_number}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"invalid OpenCode event at line {line_number}")
            if event.get("type") == "text":
                part = event.get("part") or {}
                if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                    raise ValueError(f"invalid text event at line {line_number}")
                texts.append(part["text"].strip())
    for text in reversed(texts):
        candidates = [text]
        try:
            candidates.append(normalize_single_json_fence(text))
        except ValueError:
            pass
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    raise ValueError("OpenCode log contains no schema-shaped advisory JSON object")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(extract_advisory_object(args.log))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

