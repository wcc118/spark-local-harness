from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("runtime_benchmark", ROOT / "run_runtime.py")
RUNTIME = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(RUNTIME)


def implementation_digest(workspace: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(workspace.rglob("*")):
        if path.is_file() and path.name != "REVIEW.json" and "__pycache__" not in path.parts:
            digest.update(path.relative_to(workspace).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def review_prompt(task: str) -> str:
    return RUNTIME.NATIVE_BOUNDARY.read_text(encoding="utf-8").strip() + "\n\n" + f"""
Review the current workspace against this task:\n\n{task}\n\nDo not modify implementation files. Inspect and test as needed. Write REVIEW.json with exactly these fields: verdict (APPROVE or BLOCK), findings (array of concise strings), and evidence (array of concise strings). APPROVE only when the workspace fully satisfies the task; otherwise BLOCK.
""".strip()


def run_review(args: argparse.Namespace, coder_attempt: Path) -> dict[str, object]:
    source_result = json.loads((coder_attempt / "attempt.json").read_text(encoding="utf-8"))
    pair = source_result["runtime"][0].upper() + args.reviewer_runtime[0].upper()
    target = args.output / pair / source_result["case_id"]
    if target.exists():
        shutil.rmtree(target)
    workspace = target / "workspace"
    shutil.copytree(coder_attempt / "workspace", workspace)
    before = implementation_digest(workspace)
    task = (args.manifest.parent / next(
        case["task"] for case in RUNTIME.BASE.load_manifest(args.manifest)["cases"]
        if case["id"] == source_result["case_id"]
    )).read_text(encoding="utf-8")
    environment_args = type("EnvironmentArgs", (), {
        "qwen_api_key": args.qwen_api_key, "qwen_base_url": args.qwen_base_url,
        "model": args.model,
    })()
    if args.reviewer_runtime == "opencode":
        runtime_env = RUNTIME.native_opencode_environment(environment_args, target)
        command = [args.opencode, "run", "--format", "json", "--model", args.model,
                   "--dir", str(workspace), review_prompt(task)]
        cwd = None
    else:
        runtime_env = RUNTIME.native_qwen_environment(environment_args, target)
        command = [args.qwen, "--auth-type", "openai", "--model", args.model.removeprefix("vllm/"),
                   "--approval-mode", "yolo", "--no-sandbox", "--output-format", "stream-json",
                   "--prompt", review_prompt(task)]
        cwd = workspace
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TERM") if key in os.environ}
    env.update(runtime_env)
    began = time.monotonic()
    state, code, stderr, stdout = "completed", None, "", ""
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=args.timeout, env=env, cwd=cwd)
        code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr[-4096:]
        if code != 0:
            state = "failed"
    except subprocess.TimeoutExpired as exc:
        state = "timed-out"
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = "reviewer timed out"
    target.mkdir(parents=True, exist_ok=True)
    (target / "events.jsonl").write_text(stdout, encoding="utf-8")
    review_path = workspace / "REVIEW.json"
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
        verdict = review.get("verdict")
    except (OSError, json.JSONDecodeError):
        review, verdict = None, None
    expected = "APPROVE" if source_result["deterministic_correct"] else "BLOCK"
    result = {
        "pair": pair, "case_id": source_result["case_id"], "coder_runtime": source_result["runtime"],
        "reviewer_runtime": args.reviewer_runtime, "coder_correct": source_result["deterministic_correct"],
        "expected_verdict": expected, "verdict": verdict, "review_correct": verdict == expected,
        "implementation_unchanged": implementation_digest(workspace) == before,
        "state": state, "worker_exit_code": code, "elapsed_seconds": time.monotonic() - began,
        "review": review, "stderr": stderr,
    }
    RUNTIME.BASE.atomic_write(target / "review-attempt.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coder-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewer-runtime", choices=("opencode", "qwen-code"), required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.json")
    parser.add_argument("--model", default="vllm/Qwen/Qwen3-Coder-Next-FP8")
    parser.add_argument("--opencode", default="opencode")
    parser.add_argument("--qwen", default="qwen")
    parser.add_argument("--qwen-base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--qwen-api-key", default="not-needed")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--coder-runtime", choices=("opencode", "qwen-code"))
    args = parser.parse_args()
    attempts = sorted(args.coder_output.glob("attempts/*/*"))
    attempts = [path for path in attempts if (path / "attempt.json").exists()]
    if args.coder_runtime:
        attempts = [path for path in attempts if json.loads((path / "attempt.json").read_text())["runtime"] == args.coder_runtime]
    results = [run_review(args, path) for path in attempts]
    print(json.dumps({"attempts": len(results), "correct": sum(item["review_correct"] for item in results),
                      "unchanged": sum(item["implementation_unchanged"] for item in results)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
