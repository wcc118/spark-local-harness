from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
        raise ValueError("unsupported benchmark manifest")
    ids: set[str] = set()
    source = (path.parent / str(data.get("source") or "")).resolve()
    if path.parent.resolve() not in source.parents or not source.is_dir():
        raise ValueError("invalid fixture source path")
    if any(item.is_symlink() for item in source.rglob("*")):
        raise ValueError("fixture source may not contain symlinks")
    for case in data["cases"]:
        required = {"id", "category", "role", "task", "timeout", "check"}
        if (not required.issubset(case) or case["id"] in ids
                or not isinstance(case["id"], str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", case["id"])):
            raise ValueError("malformed or duplicate benchmark case")
        ids.add(case["id"])
        task = (path.parent / case["task"]).resolve()
        if path.parent.resolve() not in task.parents or not task.is_file():
            raise ValueError(f"invalid task path for {case['id']}")
    if len(data["cases"]) != 10:
        raise ValueError("paired-model-se-v1 must contain exactly 10 cases")
    return data


def structured_issue(case: dict[str, Any], task: str) -> str:
    """Render the public task as the GitHub-style packet used in production."""
    deliverable = "a read-only report" if case["id"] in {"investigation", "review"} else "the minimal verified repository change"
    return f"""# Structured issue: {case['id']}

## Objective

{task.strip()}

## Context

Work only in the supplied isolated fixture. The issue has already been bounded for the `{case['category']}` operation.

## Scope

Implement or investigate only the objective above. Inspect the smallest relevant file set and preserve unrelated behavior.

## Acceptance criteria

- The public behavior stated in the objective is satisfied.
- No unrelated behavior or files are changed.
- Claims in the final response are supported by observed evidence.

## Constraints and exclusions

- Do not access benchmark evaluators, hidden tests, repository history, external services, or GitHub.
- Do not weaken tests or invent requirements.

## Required verification

Run the smallest focused verification available from the public fixture and report only what actually ran.

## Durable deliverable

Produce {deliverable} in the supplied workspace.

## Escalation conditions

Stop and report evidence if requirements conflict, required evidence is absent, or a safe bounded change cannot be established.
"""


def effective_prompt(case: dict[str, Any], task: str, contract: str) -> str:
    prefix = contract.strip()
    packet = structured_issue(case, task)
    return f"{prefix}\n\n{packet}" if prefix else packet


def identity(
    manifest_path: Path,
    manifest: dict[str, Any],
    case: dict[str, Any],
    contract: str = "",
) -> dict[str, str]:
    source = (manifest_path.parent / manifest["source"]).resolve()
    task = (manifest_path.parent / case["task"]).resolve()
    check_hash = hashlib.sha256(json.dumps(case["check"], separators=(",", ":")).encode()).hexdigest()
    task_text = task.read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(effective_prompt(case, task_text, contract).encode()).hexdigest()
    values = {
        "source_hash": digest_tree(source),
        "task_hash": digest_file(task),
        "check_hash": check_hash,
        "policy_hash": hashlib.sha256(manifest["permission_profile"].encode()).hexdigest(),
        "prompt_hash": prompt_hash,
    }
    values["identity_hash"] = hashlib.sha256(
        json.dumps({"case": case["id"], **values}, sort_keys=True).encode()
    ).hexdigest()
    return values


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def normalize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    calls: dict[str, dict[str, Any]] = {}
    permissions: set[str] = set()
    questions: set[str] = set()
    replies: set[str] = set()
    tokens: int | None = None
    for event in events:
        kind = str(event.get("type") or "")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
        if kind in {"tool_use", "tool.part"} or part.get("type") == "tool":
            call = str(part.get("callID") or part.get("call_id") or part.get("id") or "")
            if call:
                calls[call] = part
        request_id = str(properties.get("id") or event.get("id") or "")
        if kind == "permission.asked" and request_id: permissions.add(request_id)
        if kind == "question.asked" and request_id: questions.add(request_id)
        if kind in {"permission.replied", "question.replied", "question.rejected"} and request_id: replies.add(request_id)
        if kind == "step_finish":
            value = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            observed = value.get("total")
            if isinstance(observed, int): tokens = (tokens or 0) + observed
    failures = 0
    completed = 0
    timeline = []
    for call, part in calls.items():
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        status = str(state.get("status") or "unknown")
        completed += status == "completed"
        failures += status in {"error", "failed"}
        timeline.append({"call_id": call, "tool": part.get("tool"), "status": status})
    return {
        "tool_calls": len(calls), "completed_tool_calls": completed, "tool_errors": failures,
        "permissions_requested": len(permissions), "questions_asked": len(questions),
        "interventions": len(replies), "tokens_total": tokens, "timeline": timeline[-100:],
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_case(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    case: dict[str, Any],
    repeat: int = 1,
) -> dict[str, Any]:
    contract = args.instruction_prefix.read_text(encoding="utf-8") if args.instruction_prefix else ""
    ids = identity(manifest_path, manifest, case, contract)
    variant = args.variant
    model_slug = hashlib.sha256(f"{args.model}:{variant}:r{repeat}".encode()).hexdigest()[:12]
    attempt_dir = args.output / "attempts" / model_slug / case["id"]
    result_path = attempt_dir / "attempt.json"
    if args.resume and result_path.exists():
        old = json.loads(result_path.read_text(encoding="utf-8"))
        if (old.get("identity_hash") == ids["identity_hash"] and old.get("model") == args.model
                and old.get("policy_variant", "baseline") == variant and old.get("terminal")):
            return old
        raise RuntimeError(f"refusing mismatched resume for {case['id']}")
    if attempt_dir.exists():
        shutil.rmtree(attempt_dir)
    workspace = attempt_dir / "workspace"
    shutil.copytree((manifest_path.parent / manifest["source"]).resolve(), workspace)
    before = digest_tree((manifest_path.parent / manifest["source"]).resolve())
    public_task = (manifest_path.parent / case["task"]).read_text(encoding="utf-8")
    task = effective_prompt(case, public_task, contract)
    run_id = f"bench-{case['id']}-{model_slug}-{uuid.uuid4().hex[:8]}"
    events_path = attempt_dir / "events.jsonl"
    command = [
        args.opencode, "run", "--format", "json", "--model", args.model,
        "--agent", args.agent, "--dir", str(workspace), task,
    ]
    started = datetime.now(timezone.utc).isoformat()
    began = time.monotonic()
    state = "completed"
    return_code: int | None = None
    stderr = ""
    try:
        allowed_environment = {
            key: os.environ[key] for key in (
                "HOME", "PATH", "LANG", "LC_ALL", "TERM", "XDG_CONFIG_HOME",
                "XDG_DATA_HOME", "XDG_CACHE_HOME", "OPENCODE_CONFIG",
            ) if key in os.environ
        }
        allowed_environment["CODEX_BENCHMARK_RUN_ID"] = run_id
        completed = subprocess.run(command, text=True, capture_output=True,
                                   timeout=getattr(args, "timeout", None) or case["timeout"],
                                   env=allowed_environment)
        return_code = completed.returncode
        events_path.write_text(completed.stdout, encoding="utf-8")
        stderr = completed.stderr[-4096:]
        if return_code != 0: state = "failed"
    except subprocess.TimeoutExpired as exc:
        state = "timed-out"
        output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        events_path.write_text(output, encoding="utf-8")
        stderr = "worker timed out"
    elapsed = time.monotonic() - began
    check_command = [str(workspace) if item == "{workspace}" else item for item in case["check"]]
    check = subprocess.run(check_command, cwd=manifest_path.parent, text=True, capture_output=True, timeout=30)
    correct = check.returncode == 0
    if digest_tree((manifest_path.parent / manifest["source"]).resolve()) != before:
        raise RuntimeError("immutable fixture source was modified")
    metrics = normalize_events(read_json_lines(events_path))
    result = {
        "schema_version": 1, "attempt_id": run_id, "run_id": run_id, "case_id": case["id"],
        "fixture_id": case["id"], "category": case["category"], "role": case["role"],
        "model": args.model, "route": args.route, "policy_variant": variant, "repeat": repeat,
        **ids, "started_at": started,
        "observed_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": elapsed,
        "state": state, "terminal": True, "worker_exit_code": return_code,
        "deterministic_correct": correct, "check": {"command": check_command, "exit_code": check.returncode,
        "stdout": check.stdout[-2048:], "stderr": check.stderr[-2048:]},
        "corrections": None, "reviewer_findings": None, "stderr": stderr, **metrics,
        "workspace": str(workspace), "raw_events": str(events_path),
    }
    atomic_write(result_path, result)
    return result


def command_run(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    selected = [case for case in manifest["cases"] if not args.case or case["id"] == args.case]
    if not selected: raise ValueError("unknown benchmark case")
    results = [
        run_case(args, manifest_path, manifest, case, repeat)
        for case in selected for repeat in range(1, getattr(args, "repeat", 1) + 1)
    ]
    print(json.dumps({"model": args.model, "attempts": len(results), "correct": sum(r["deterministic_correct"] for r in results)}, sort_keys=True))
    return 0 if all(result["terminal"] for result in results) else 1


def command_compare(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest.resolve())
    attempts = [json.loads(path.read_text(encoding="utf-8")) for path in args.output.glob("attempts/*/*/attempt.json")]
    summaries = []
    for case in manifest["cases"]:
        pair = [item for item in attempts if item["case_id"] == case["id"] and item["model"] in {args.left, args.right}]
        left = next((item for item in pair if item["model"] == args.left), None)
        right = next((item for item in pair if item["model"] == args.right), None)
        inconclusive = not left or not right or left["identity_hash"] != right["identity_hash"]
        winner = None
        if not inconclusive:
            if left["deterministic_correct"] != right["deterministic_correct"]:
                winner = args.left if left["deterministic_correct"] else args.right
            elif left["deterministic_correct"] and right["deterministic_correct"]:
                winner = "tie"
        summaries.append({"schema_version":1,"fixture_id":case["id"],"category":case["category"],
                          "left_model":args.left,"right_model":args.right,"left":left,"right":right,
                          "winner":winner,"tie":winner=="tie","inconclusive":inconclusive,
                          "notes":"correctness first; efficiency reported only after correctness"})
    complete = len(summaries) == 10 and all(not item["inconclusive"] for item in summaries)
    wins = {args.left:0, args.right:0}
    for item in summaries:
        if item["winner"] in wins: wins[item["winner"]] += 1
    recommendation = {"schema_version":1,"evidence_complete":complete,"minimum_pairs":10,
                      "current_routes_preserved":not complete or wins[args.left] == wins[args.right],
                      "wins":wins,"recommended_default":None,"rollback":"config/workers-vllm.json"}
    if complete and wins[args.left] != wins[args.right]:
        recommendation["recommended_default"] = max(wins, key=wins.get)
        recommendation["current_routes_preserved"] = True
    atomic_write(args.output / "comparison.json", {"schema_version":1,"summaries":summaries,"recommendation":recommendation})
    dashboard_manifest = {
        "manifest_id": manifest["benchmark_id"], "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": "Ten-case controlled Qwen 3.8 versus Coder Next software-engineering benchmark",
        "fixtures_count": len(manifest["cases"]),
    }
    atomic_write(args.output / "benchmark-manifest.json", dashboard_manifest)
    with (args.output / "benchmark-attempts.jsonl").open("w", encoding="utf-8") as handle:
        for item in attempts:
            handle.write(json.dumps({
                "attempt_id": item["attempt_id"], "identity_hash": item["identity_hash"],
                "model": item["model"], "route": item["route"], "fixture_id": item["fixture_id"],
                "category": item["category"], "elapsed_seconds": item["elapsed_seconds"],
                "state": item["state"], "tokens": None, "tool_calls": item["tool_calls"],
                "tool_errors": item["tool_errors"], "corrections": item.get("corrections"),
                "permissions_requested": item["permissions_requested"], "questions_asked": item["questions_asked"],
                "interventions": item["interventions"], "finding_refs": [], "run_id": item["run_id"],
                "observed_at": item["observed_at"], "deterministic_correct": item["deterministic_correct"],
                "raw_events": item["raw_events"],
            }, separators=(",", ":")) + "\n")
    with (args.output / "benchmark-summaries.jsonl").open("w", encoding="utf-8") as handle:
        for item in summaries:
            left, right = item.get("left"), item.get("right")
            handle.write(json.dumps({
                "summary_id": "pair-" + item["fixture_id"], "fixture_id": item["fixture_id"],
                "category": item["category"], "left_attempt_id": left["attempt_id"] if left else "",
                "right_attempt_id": right["attempt_id"] if right else "", "left_model": args.left,
                "right_model": args.right, "left_route": left["route"] if left else "",
                "right_route": right["route"] if right else "", "left_tokens": left.get("tokens_total") if left else None,
                "right_tokens": right.get("tokens_total") if right else None,
                "left_elapsed": left["elapsed_seconds"] if left else None, "right_elapsed": right["elapsed_seconds"] if right else None,
                "left_correct": left["deterministic_correct"] if left else None,
                "right_correct": right["deterministic_correct"] if right else None,
                "correct_first": bool(left and right and left["deterministic_correct"] and not right["deterministic_correct"]),
                "tie": item["tie"], "inconclusive": item["inconclusive"], "token_reduction": None,
                "time_reduction": None, "notes": item["notes"],
            }, separators=(",", ":")) + "\n")
    print(json.dumps(recommendation, sort_keys=True))
    return 0 if complete else 2


def command_select(args: argparse.Namespace) -> int:
    attempts = [json.loads(path.read_text(encoding="utf-8")) for path in args.output.glob("attempts/*/*/attempt.json")]
    cohorts = {}
    for name in (args.baseline, args.candidate):
        values = [
            item for item in attempts
            if item.get("policy_variant", "baseline") == name and item.get("model") == args.model
        ]
        totals = {
            "variant": name, "runs": len(values),
            "correct": sum(
                item.get("deterministic_correct") is True and item.get("state", "completed") == "completed"
                for item in values
            ),
            "elapsed_seconds": sum(float(item.get("elapsed_seconds") or 0) for item in values),
            "tool_calls": sum(int(item.get("tool_calls") or 0) for item in values),
            "tool_errors": sum(int(item.get("tool_errors") or 0) for item in values),
            "tokens": sum(int(item.get("tokens_total") or 0) for item in values),
            "corrections": sum(int(item.get("corrections") or 0) for item in values),
        }
        cohorts[name] = totals
    baseline, candidate = cohorts[args.baseline], cohorts[args.candidate]
    metrics = ("elapsed_seconds", "tool_calls", "tool_errors", "tokens", "corrections")
    correctness_retained = baseline["runs"] == candidate["runs"] == 10 and baseline["correct"] == candidate["correct"] == 10
    non_worse = all(candidate[name] <= baseline[name] for name in metrics)
    improved = [name for name in metrics if candidate[name] < baseline[name]]
    promoted = correctness_retained and non_worse and bool(improved)
    result = {
        "schema_version": 1, "baseline": baseline, "candidate": candidate,
        "correctness_retained": correctness_retained, "non_worse": non_worse,
        "improved_metrics": improved, "promoted": promoted,
        "decision": args.candidate if promoted else args.baseline,
        "rule": "promote only 10/10 candidates that are non-worse on every measured cost and improve at least one",
    }
    atomic_write(args.output / "policy-selection.json", result)
    by_variant_case = {
        (item.get("policy_variant", "baseline"), item.get("case_id")): item
        for item in attempts if item.get("model") == args.model and isinstance(item.get("case_id"), str)
    }
    summary_path = args.output / "benchmark-summaries.jsonl"
    retained = [item for item in read_json_lines(summary_path) if not str(item.get("summary_id", "")).startswith("policy-")]
    variant_summaries = []
    for case_id in sorted({key[1] for key in by_variant_case if key[0] == args.baseline}):
        left = by_variant_case.get((args.baseline, case_id))
        right = by_variant_case.get((args.candidate, case_id))
        if not left or not right:
            continue
        left_ok = left.get("deterministic_correct") is True and left.get("state") == "completed"
        right_ok = right.get("deterministic_correct") is True and right.get("state") == "completed"
        variant_summaries.append({
            "summary_id": f"policy-{case_id}", "fixture_id": case_id,
            "category": left.get("category", "focused_fix"),
            "left_attempt_id": left["attempt_id"], "right_attempt_id": right["attempt_id"],
            "left_model": f"{args.model} [{args.baseline}]", "right_model": f"{args.model} [{args.candidate}]",
            "left_route": left["route"], "right_route": right["route"],
            "left_tokens": left.get("tokens_total"), "right_tokens": right.get("tokens_total"),
            "left_elapsed": left.get("elapsed_seconds"), "right_elapsed": right.get("elapsed_seconds"),
            "left_correct": left_ok, "right_correct": right_ok,
            "correct_first": left_ok and not right_ok, "tie": left_ok and right_ok,
            "inconclusive": not left_ok and not right_ok, "token_reduction": None, "time_reduction": None,
            "notes": f"policy candidate {'promoted' if promoted else 'rejected'} by correctness-first gate",
        })
    with summary_path.open("w", encoding="utf-8") as handle:
        for item in [*retained, *variant_summaries]:
            handle.write(json.dumps(item, separators=(",", ":")) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if correctness_retained else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.json")
    parser.add_argument("--output", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--model", required=True); run.add_argument("--route", required=True)
    run.add_argument("--agent", default="coder"); run.add_argument("--opencode", default="opencode")
    run.add_argument("--case"); run.add_argument("--resume", action="store_true")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--timeout", type=int, help="enforced wall-time override per case")
    run.add_argument("--variant", default="baseline"); run.add_argument("--instruction-prefix", type=Path)
    compare = sub.add_parser("compare"); compare.add_argument("--left", required=True); compare.add_argument("--right", required=True)
    select = sub.add_parser("select"); select.add_argument("--baseline", required=True); select.add_argument("--candidate", required=True); select.add_argument("--model", required=True)
    args = parser.parse_args()
    if args.command == "run": return command_run(args)
    if args.command == "compare": return command_compare(args)
    return command_select(args)


if __name__ == "__main__":
    raise SystemExit(main())
