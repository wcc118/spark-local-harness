from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
POLICY = ROOT / "policies" / "runtime-v1.md"
SPEC = importlib.util.spec_from_file_location("paired_model_base", ROOT / "run.py")
BASE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(BASE)


def normalize_qwen(events: list[dict[str, Any]]) -> dict[str, Any]:
    calls: dict[str, dict[str, Any]] = {}
    tokens: int | None = None
    denials = 0
    for event in events:
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), list) else []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                call_id = str(block.get("id") or "")
                if call_id:
                    calls[call_id] = {"call_id": call_id, "tool": block.get("name"), "status": "requested"}
            if block.get("type") == "tool_result":
                call_id = str(block.get("tool_use_id") or "")
                if call_id in calls:
                    calls[call_id]["status"] = "error" if block.get("is_error") else "completed"
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else message.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            tokens = usage["total_tokens"]
        if isinstance(event.get("permission_denials"), list):
            denials = max(denials, len(event["permission_denials"]))
    return {
        "tool_calls": len(calls),
        "completed_tool_calls": sum(item["status"] == "completed" for item in calls.values()),
        "tool_errors": sum(item["status"] == "error" for item in calls.values()),
        "permissions_requested": denials, "questions_asked": None,
        "interventions": denials, "tokens_total": tokens,
        "timeline": list(calls.values())[-100:],
    }


def qwen_environment(args: argparse.Namespace, attempt_dir: Path) -> dict[str, str]:
    runtime_home = attempt_dir / "runtime-home"
    config_dir = runtime_home / ".qwen"
    config_dir.mkdir(parents=True, mode=0o700)
    (config_dir / "settings.json").write_text(json.dumps({
        "privacy": {"usageStatisticsEnabled": False},
        "telemetry": {"enabled": False},
        "tools": {
            "core": ["read_file", "grep_search", "glob", "edit", "write_file", "run_shell_command"],
            "approvalMode": "yolo",
        },
        "permissions": {"deny": [
            "Agent", "skill", "web_fetch", "computer_use__*", "create_sub_session",
            "enter_worktree", "exit_worktree", "send_message", "list_agents",
        ]},
    }, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "HOME": str(runtime_home), "QWEN_CONFIG_DIR": str(config_dir),
        "OPENAI_API_KEY": args.qwen_api_key, "OPENAI_BASE_URL": args.qwen_base_url,
        "OPENAI_MODEL": args.model.removeprefix("vllm/"),
        "QWEN_TELEMETRY_ENABLED": "0", "QWEN_CODE_SUPPRESS_YOLO_WARNING": "1",
    }


def opencode_environment(args: argparse.Namespace, attempt_dir: Path) -> dict[str, str]:
    runtime_home = attempt_dir / "runtime-home"
    config_dir = runtime_home / ".config" / "opencode"
    agents_dir = config_dir / "agents"
    agents_dir.mkdir(parents=True, mode=0o700)
    config = json.loads(args.opencode_config.read_text(encoding="utf-8"))
    config.setdefault("mcp", {}).setdefault("github", {})["enabled"] = False
    config.setdefault("permission", {})["external_directory"] = "deny"
    coder = config.setdefault("agent", {}).setdefault("coder", {})
    coder.setdefault("permission", {})["task"] = "deny"
    (config_dir / "opencode.json").write_text(
        json.dumps(config, separators=(",", ":")) + "\n", encoding="utf-8",
    )
    if args.opencode_agents.is_dir():
        for source in args.opencode_agents.iterdir():
            if source.is_file():
                shutil.copy2(source, agents_dir / source.name)
    return {
        "HOME": str(runtime_home), "XDG_CONFIG_HOME": str(runtime_home / ".config"),
        "XDG_DATA_HOME": str(runtime_home / ".local" / "share"),
        "XDG_CACHE_HOME": str(runtime_home / ".cache"),
    }


def run_case(args: argparse.Namespace, manifest_path: Path, manifest: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    ids = BASE.identity(manifest_path, manifest, case)
    runtime_policy_hash = BASE.digest_file(POLICY)
    slug = hashlib.sha256(f"{args.runtime}:{args.model}".encode()).hexdigest()[:12]
    attempt_dir = args.output / "attempts" / slug / case["id"]
    result_path = attempt_dir / "attempt.json"
    if args.resume and result_path.exists():
        old = json.loads(result_path.read_text(encoding="utf-8"))
        if (old.get("identity_hash") == ids["identity_hash"] and old.get("model") == args.model
                and old.get("runtime") == args.runtime
                and old.get("runtime_policy_hash") == runtime_policy_hash and old.get("terminal")):
            return old
        raise RuntimeError(f"refusing mismatched resume for {case['id']}")
    if attempt_dir.exists():
        shutil.rmtree(attempt_dir)
    workspace = attempt_dir / "workspace"
    source = (manifest_path.parent / manifest["source"]).resolve()
    shutil.copytree(source, workspace)
    before = BASE.digest_tree(source)
    task = POLICY.read_text(encoding="utf-8").strip() + "\n\n" + (manifest_path.parent / case["task"]).read_text(encoding="utf-8")
    events_path = attempt_dir / "events.jsonl"
    if args.runtime == "opencode":
        command = [args.opencode, "run", "--format", "json", "--model", args.model,
                   "--agent", "coder", "--dir", str(workspace), task]
    else:
        command = [args.qwen, "--auth-type", "openai", "--model", args.model.removeprefix("vllm/"),
                   "--approval-mode", "yolo", "--no-sandbox", "--output-format", "stream-json",
                   "--prompt", task]
    allowed = {key: os.environ[key] for key in (
        "HOME", "PATH", "LANG", "LC_ALL", "TERM", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    ) if key in os.environ}
    run_id = f"runtime-bench-{case['id']}-{slug}-{uuid.uuid4().hex[:8]}"
    allowed["CODEX_BENCHMARK_RUN_ID"] = run_id
    cwd = None
    if args.runtime == "qwen-code":
        allowed.update(qwen_environment(args, attempt_dir))
        cwd = workspace
    else:
        allowed.update(opencode_environment(args, attempt_dir))
    started = datetime.now(timezone.utc).isoformat()
    began = time.monotonic()
    state, return_code, stderr = "completed", None, ""
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=case["timeout"], env=allowed, cwd=cwd)
        return_code = completed.returncode
        events_path.write_text(completed.stdout, encoding="utf-8")
        stderr = completed.stderr[-4096:]
        if return_code != 0:
            state = "failed"
    except subprocess.TimeoutExpired as exc:
        state = "timed-out"
        output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        events_path.write_text(output, encoding="utf-8")
        stderr = "worker timed out"
    elapsed = time.monotonic() - began
    check_command = [str(workspace) if item == "{workspace}" else item for item in case["check"]]
    check = subprocess.run(check_command, cwd=manifest_path.parent, text=True, capture_output=True, timeout=30)
    if BASE.digest_tree(source) != before:
        raise RuntimeError("immutable fixture source was modified")
    events = BASE.read_json_lines(events_path)
    metrics = BASE.normalize_events(events) if args.runtime == "opencode" else normalize_qwen(events)
    result = {
        "schema_version": 1, "attempt_id": run_id, "run_id": run_id, "case_id": case["id"],
        "fixture_id": case["id"], "category": case["category"], "role": case["role"],
        "model": args.model, "runtime": args.runtime, "runtime_version": args.runtime_version,
        "runtime_policy_hash": runtime_policy_hash,
        "route": args.route, "policy_variant": "baseline", **ids, "started_at": started,
        "observed_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": elapsed,
        "state": state, "terminal": True, "worker_exit_code": return_code,
        "deterministic_correct": check.returncode == 0,
        "check": {"command": check_command, "exit_code": check.returncode,
                  "stdout": check.stdout[-2048:], "stderr": check.stderr[-2048:]},
        "corrections": None, "reviewer_findings": None, "stderr": stderr, **metrics,
        "workspace": str(workspace), "raw_events": str(events_path),
    }
    BASE.atomic_write(result_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime", choices=("opencode", "qwen-code"), required=True)
    parser.add_argument("--runtime-version", required=True)
    parser.add_argument("--model", required=True); parser.add_argument("--route", required=True)
    parser.add_argument("--opencode", default="opencode"); parser.add_argument("--qwen", default="qwen")
    parser.add_argument("--opencode-config", type=Path, default=Path.home() / ".config/opencode/opencode.json")
    parser.add_argument("--opencode-agents", type=Path, default=Path.home() / ".config/opencode/agents")
    parser.add_argument("--qwen-base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--qwen-api-key", default="not-needed")
    parser.add_argument("--case"); parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = BASE.load_manifest(manifest_path)
    selected = [case for case in manifest["cases"] if not args.case or case["id"] == args.case]
    if not selected:
        raise ValueError("unknown benchmark case")
    results = [run_case(args, manifest_path, manifest, case) for case in selected]
    print(json.dumps({"model": args.model, "runtime": args.runtime, "attempts": len(results),
                      "correct": sum(item["deterministic_correct"] for item in results)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
