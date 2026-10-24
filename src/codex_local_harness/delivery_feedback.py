"""Advisory native Stop feedback; final host postflight remains authoritative.

This stdlib-only module is copied into a disposable worker home. It reads git
state but never repairs, commits, publishes, or authorizes delivery.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Match host coder postflight's generated-artifact exception. This local copy
# is feedback only; changing or bypassing it cannot authorize publication.
GENERATED = re.compile(r"(^|/)(?:__pycache__|\.pytest_cache)(?:/|$)|\.pyc$|(^|/)\.coverage(?:\.|$)")


def git(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(checkout), *args],
        capture_output=True, text=True, timeout=30,
    )


def inspect(checkout: Path, base: str) -> list[str]:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", base):
        raise ValueError("invalid comparison base")
    root = git(checkout, "rev-parse", "--show-toplevel")
    if root.returncode or Path(root.stdout.strip()).resolve() != checkout.resolve():
        raise ValueError("configured checkout is not a Git root")
    status = git(checkout, "status", "--porcelain=v1", "-z")
    if status.returncode:
        raise ValueError("git status failed")
    paths = []
    entries = iter(status.stdout.split("\0"))
    for entry in entries:
        if not entry:
            continue
        path = entry[3:]
        if "R" in entry[:2] or "C" in entry[:2]:
            next(entries, None)  # porcelain -z includes the original name too
        if not GENERATED.search(path):
            paths.append(path)
    failures = []
    if paths:
        failures.append(f"Uncommitted checkout changes ({len(paths)}): "
                        + ", ".join(json.dumps(path) for path in paths[:20]))
    diff = git(checkout, "diff", "--no-ext-diff", "--check", f"{base}..HEAD")
    if diff.returncode:
        # Do not echo added source lines or arbitrary stderr into the prompt.
        locations = [line for line in diff.stdout.splitlines()
                     if not line.startswith("+") and
                     re.match(r"^.+:\d+: (trailing whitespace|space before tab|new blank line|leftover conflict marker)", line)]
        if not locations:
            raise ValueError("committed diff check could not be completed")
        failures.append("Committed diff check failed: " + "; ".join(locations[:20]))
    return failures


def check(checkout: Path, base: str, payload: dict) -> dict:
    if payload.get("hook_event_name") != "Stop":
        return {}
    message = payload.get("last_assistant_message", "")
    if isinstance(message, str) and message.lstrip().startswith("ESCALATE:"):
        return {"systemMessage": "Coder reported escalation; final host postflight still runs."}
    try:
        failures = inspect(checkout, base)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"systemMessage": "Local delivery feedback unavailable; final host postflight remains authoritative."}
    if not failures:
        return {}
    return {"decision": "block", "reason": (
        "LOCAL DELIVERY FEEDBACK (advisory; host postflight will independently recheck):\n"
        + "\n".join(failures)
        + "\nInspect and correct your intended work, run relevant verification, and commit it "
          "before finishing. Preserve unrelated work. Do not push or publish. If a genuine "
          "blocker prevents completion, start the final response with ESCALATE: and explain "
          "the concrete blocker; this does not bypass host verification."
    )}


def install(runtime_home: Path, checkout: Path, base: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", base):
        raise ValueError("invalid comparison base")
    target = runtime_home / "delivery-feedback.py"
    target.write_bytes(Path(__file__).read_bytes())
    target.chmod(0o600)
    settings_path = runtime_home / ".qwen/settings.json"
    settings = json.loads(settings_path.read_text())
    command = shlex.join(["python3", str(target), "check", "--checkout", str(checkout), "--base", base])
    settings.setdefault("hooks", {}).setdefault("Stop", []).append(
        {"hooks": [{"type": "command", "command": command}]}
    )
    settings_path.write_text(json.dumps(settings) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "check"))
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--runtime-home", type=Path)
    args = parser.parse_args()
    if args.operation == "install":
        if args.runtime_home is None:
            parser.error("install requires --runtime-home")
        install(args.runtime_home, args.checkout, args.base)
    else:
        try:
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise ValueError("hook input must be an object")
            result = check(args.checkout, args.base, payload)
        except (ValueError, OSError):
            result = {"systemMessage": "Invalid local delivery hook input; host postflight still runs."}
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
