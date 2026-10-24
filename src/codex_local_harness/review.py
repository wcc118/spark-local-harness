from __future__ import annotations

import argparse
import json
import os
import re
import posixpath
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .evidence_packet import (
    MAX_REPORTED_TASK_CHARS,
    MAX_REVIEW_PROMPT_CHARS,
    ReviewerEvidencePacket,
    build_reviewer_evidence_packet,
    discover_test_executables,
)


RESULT_PREFIX = "REVIEW_RESULT_JSON="
VERDICTS = {"APPROVE", "REQUEST_CHANGES", "ESCALATE"}
RESULT_SCHEMA = (
    '{"schema_version":1,"verdict":"APPROVE|REQUEST_CHANGES|ESCALATE",'
    '"repository":"OWNER/REPO","issue":1,"pull_request":2,"reviewed_head_sha":"40-char SHA",'
    '"criteria":[],"findings":[],"tests":[],"confidence":"high|medium|low",'
    '"summary":"...","escalation_reason":null}'
)
INCOMPLETE_REASONS = {"wall-time-budget", "token-budget", "step-budget", "malformed-result"}
WORKFLOW_LABELS = {
    "status/backlog",
    "status/in-progress",
    "status/review",
    "status/review-passed",
    "status/blocked",
    "status/done",
}


class ReviewError(RuntimeError):
    """A deterministic review-transaction failure."""


Run = Callable[[Sequence[str], Path | None], str]


def run_command(argv: Sequence[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReviewError(f"command failed ({completed.returncode}): {' '.join(argv)}: {detail}")
    return completed.stdout


def normalize_remote(remote: str) -> str:
    value = remote.strip()
    patterns = (
        r"https://github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
        r"git@github\.com:(?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
        r"ssh://git@github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, value)
        if match:
            return match.group("repo")
    raise ReviewError(f"unsupported GitHub remote: {value}")


@dataclass(frozen=True)
class Envelope:
    schema_version: int
    repository: str
    issue: int
    pull_request: int
    checkout: str
    branch: str
    head_sha: str
    base_sha: str
    base_branch: str
    publication_mode: str = "comment"
    tracked_generated_artifacts: tuple[str, ...] = ()
    tracked_generated_artifact_count: int = 0
    scratch_dir: str | None = None
    runtime_home: str | None = None
    investigative: bool = False
    container_tmpfs: bool = False

    def __post_init__(self):
        if type(self.container_tmpfs) is not bool or (self.container_tmpfs and self.investigative is not True):
            raise ReviewError('Container tmpfs requires an explicitly qualified investigative review')

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _json_command(run: Run, argv: Sequence[str], cwd: Path | None = None) -> Any:
    try:
        return json.loads(run(argv, cwd))
    except json.JSONDecodeError as exc:
        raise ReviewError(f"invalid JSON from {' '.join(argv)}") from exc


def build_envelope(
    checkout: Path,
    repository: str,
    issue: int,
    pull_request: int,
    run: Run = run_command,
    scratch_dir: Path | None = None,
    runtime_home: Path | None = None,
) -> Envelope:
    canonical = checkout.resolve(strict=True)
    root = Path(run(["git", "rev-parse", "--show-toplevel"], canonical).strip()).resolve()
    if root != canonical:
        raise ReviewError(f"checkout must be the Git root: expected {canonical}, observed {root}")
    if run(["git", "status", "--porcelain=v1"], canonical).strip():
        raise ReviewError("review checkout is not clean")

    remote = normalize_remote(run(["git", "remote", "get-url", "origin"], canonical))
    if remote.casefold() != repository.casefold():
        raise ReviewError(f"remote mismatch: expected {repository}, observed {remote}")

    branch = run(["git", "branch", "--show-current"], canonical).strip()
    head_sha = run(["git", "rev-parse", "HEAD"], canonical).strip()
    pr = _json_command(
        run,
        [
            "gh", "pr", "view", str(pull_request), "--repo", repository,
            "--json", "number,state,headRefName,headRefOid,baseRefName",
        ],
        canonical,
    )
    issue_data = _json_command(
        run,
        ["gh", "issue", "view", str(issue), "--repo", repository, "--json", "number,state,labels"],
        canonical,
    )
    if pr.get("number") != pull_request or pr.get("state") != "OPEN":
        raise ReviewError("pull request is missing or not open")
    if issue_data.get("number") != issue or issue_data.get("state") != "OPEN":
        raise ReviewError("issue is missing or not open")
    if branch != pr.get("headRefName"):
        raise ReviewError(f"branch mismatch: checkout={branch}, PR={pr.get('headRefName')}")
    if head_sha != pr.get("headRefOid"):
        raise ReviewError(f"head SHA mismatch: checkout={head_sha}, PR={pr.get('headRefOid')}")
    base_branch = str(pr.get("baseRefName") or "")
    if not base_branch:
        raise ReviewError("pull request has no base branch")
    base_sha = run(
        ["gh", "api", f"repos/{repository}/git/ref/heads/{base_branch}", "--jq", ".object.sha"],
        canonical,
    ).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", base_sha):
        raise ReviewError("GitHub returned an invalid base SHA")
    labels = {item["name"] for item in issue_data.get("labels", [])}
    workflow = labels & WORKFLOW_LABELS
    if workflow != {"status/review"}:
        raise ReviewError(f"issue must have exactly status/review; observed {sorted(workflow)}")
    tracked_files = run(["git", "ls-files"], canonical).splitlines()
    generated = tuple(
        path
        for path in tracked_files
        if re.search(r"(^|/)(?:__pycache__|\.pytest_cache)/|\.pyc$", path)
    )

    return Envelope(
        schema_version=1,
        repository=repository,
        issue=issue,
        pull_request=pull_request,
        checkout=str(canonical),
        branch=branch,
        head_sha=head_sha,
        base_sha=base_sha,
        base_branch=base_branch,
        tracked_generated_artifacts=generated[:25],
        tracked_generated_artifact_count=len(generated),
        scratch_dir=str(scratch_dir.resolve(strict=True)) if scratch_dir else None,
        runtime_home=str(runtime_home.resolve(strict=True)) if runtime_home else None,
    )


def prepare_prompt(envelope: Envelope, task: str, packet: ReviewerEvidencePacket | None = None) -> str:
    if not task.strip() or len(task) > MAX_REPORTED_TASK_CHARS:
        raise ReviewError("reported review task is empty or exceeds its size bound")
    identity = json.dumps(envelope.as_dict(), sort_keys=True, separators=(",", ":"))
    evidence = f"HARNESS_OBSERVED_EVIDENCE={packet.compact_json()}\n\n" if packet else ""
    boundary = (
        "Investigate the exact read-only checkout using repository inspection and focused tests. "
        "The bounded patch is an index, not the complete evidence boundary: inspect omitted code "
        "in the checkout when needed. Use the supplied scratch directory for diagnostic tests and "
        "writable test copies. Preserve implementation and acceptance criteria. "
        "Continue while useful investigation remains; report an explicit blocker if unable to progress. "
        if envelope.investigative else
        "Inspect the harness-observed exact-head diff and deterministic git-diff-check result in this "
        "packet. The repository evidence boundary is packet-only: do not inspect more repository "
        "files. Configured shared-memory tools may be used according to the supplied memory guidance; historical lessons do not replace current patch evidence. If the bounded patch is truncated or insufficient, return ESCALATE. Serialize the "
        "complete result immediately. "
    )
    prompt = (
        "IMMUTABLE_REVIEW_ENVELOPE=" + identity + "\n\n"
        + evidence
        + "Review only the checkout and commit identified above. Do not access another checkout. "
        + boundary
        +
        "Do not change GitHub state. End with exactly one single-line machine result prefixed "
        f"{RESULT_PREFIX}. Tracked generated artifacts listed in the envelope are potential "
        "coder findings; never delete, regenerate, or repair them. Test execution is configured "
        "not to write bytecode or pytest caches. If temporary scripts or files are needed, write "
        f"them below the harness-owned scratch directory {envelope.scratch_dir}. "
        + ("This qualified container also provides isolated /tmp tmpfs for temporary data; "
           "it is noexec, so use the supplied scratch directory for executable fixtures. "
           if envelope.container_tmpfs else "Do not use another /tmp or external path. ")
        + f"The disposable runtime home {envelope.runtime_home} is "
        "also harness-owned and may contain interpreter or package-manager runtime files only.\n\n"
        f"The required complete result schema is {RESULT_SCHEMA}. Copy every field exactly once, "
        "substitute the immutable identity, and do not rename fields.\n\n"
        "The evidence packet contains harness-observed facts, not findings or a verdict. "
        "Any advisory_risk_signals are deterministic search cues only: independently assess them, "
        "do not treat their presence or absence as a verdict, and do not assume they are exhaustive. "
        "In every tests entry, provenance must be exactly one of the JSON strings "
        "\"harness-observed\", \"reviewer-observed\", or \"task-reported\". "
        "Put explanations in a separate outcome field, never inside provenance. "
        "Every reviewer-observed test requires an integer exit_code from 0 through 255; "
        "never invent an exit code or describe a task-reported test as executed by you. "
        "Use harness-observed for supplied deterministic evidence "
        "and task-reported for claims. A test you cannot run is not reviewer-observed. "
        "Example: {\"command\":\"pytest\",\"provenance\":\"task-reported\","
        "\"outcome\":\"Reported passing; not independently executed\"}. "
        "The task below contains reported claims and requirements that still require independent judgment.\n\n"
        f"REPORTED_TASK_CLAIMS:\n{task}"
    )
    if len(prompt) > MAX_REVIEW_PROMPT_CHARS:
        raise ReviewError("review prompt exceeds its total size bound")
    return prompt


def parse_result(log_path: Path) -> dict[str, Any]:
    texts: list[str] = []
    for line_number, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReviewError(f"invalid event JSON at line {line_number}") from exc
        if event.get("type") != "text":
            continue
        texts.append(str(event.get("part", {}).get("text", "")))
    output = "\n".join(texts)
    candidates: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for fragment in output.split(RESULT_PREFIX)[1:]:
        fragment = fragment.lstrip()
        if not fragment:
            continue
        try:
            value, _ = decoder.raw_decode(fragment)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    if len(candidates) != 1:
        raise ReviewError(f"expected exactly one structured result, observed {len(candidates)}")
    result = candidates[0]
    return result


def canonicalize_result(result: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    """Convert a small, documented reviewer compatibility vocabulary to schema v1.

    This boundary changes representation only.  It rejects unknown fields and
    verifies every model-supplied identity before filling canonical identity
    fields from the immutable harness envelope.
    """
    allowed = {
        "schema_version", "verdict", "identity", "repository", "repo", "issue", "pull_request", "pr",
        "reviewed_head_sha", "head", "head_sha", "criteria", "findings", "tests",
        "confidence", "summary", "escalation_reason", "escalation",
    }
    unknown = sorted(set(result) - allowed)
    if unknown:
        raise ReviewError(f"structured result has unsupported fields: {unknown}")

    identity = result.get("identity", {})
    if isinstance(identity, str):
        match = re.fullmatch(r"(?P<repo>[^#\s]+)#(?P<pr>\d+)", identity.strip())
        if not match or match.group("repo") != envelope.repository or int(match.group("pr")) != envelope.pull_request:
            raise ReviewError("result identity mismatch for pull_request")
        identity = {"repository": match.group("repo"), "pull_request": int(match.group("pr"))}
    if not isinstance(identity, dict) or set(identity) - {
        "repository", "issue", "pull_request", "reviewed_head_sha", "exact_head"
    }:
        raise ReviewError("identity alias must contain only canonical identity fields")

    def supplied(*names: str) -> list[Any]:
        values = [result[name] for name in names if name in result]
        values.extend(identity[name] for name in names if name in identity)
        return values

    expected_aliases = {
        "repository": (envelope.repository, supplied("repository", "repo")),
        "issue": (envelope.issue, supplied("issue")),
        "pull_request": (envelope.pull_request, supplied("pull_request", "pr")),
        "reviewed_head_sha": (envelope.head_sha, supplied("reviewed_head_sha", "head", "head_sha", "exact_head")),
    }
    for field, (expected, values) in expected_aliases.items():
        if not values:
            continue
        normalized = []
        for value in values:
            if field in {"issue", "pull_request"} and isinstance(value, str):
                match = re.fullmatch(r"(?:(?P<repo>[^#\s]+)?#)?(?P<number>\d+)", value.strip())
                if match and match.group("repo") not in {None, envelope.repository}:
                    raise ReviewError(f"result identity mismatch for {field}")
                value = int(match.group("number")) if match else value
            normalized.append(value)
        if any(value != expected for value in normalized):
            raise ReviewError(f"result identity mismatch for {field}")

    verdict_aliases = {"PASS": "APPROVE", "FAIL": "REQUEST_CHANGES"}
    verdict = verdict_aliases.get(result.get("verdict"), result.get("verdict"))

    criteria = result.get("criteria", [])
    if isinstance(criteria, dict):
        criteria = [
            {"criterion": str(name), "status": str(status), "evidence": "reviewer assertion"}
            for name, status in criteria.items()
        ]
    findings = result.get("findings", [])
    if all(isinstance(item, str) for item in findings) if isinstance(findings, list) else False:
        findings = [{"severity": "note", "message": item} for item in findings]
    tests = result.get("tests", [])
    if isinstance(tests, dict):
        raise ReviewError("tests mapping cannot prove execution provenance")

    confidence = result.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        if not 0 <= confidence <= 1:
            raise ReviewError("numeric confidence must be between zero and one")
        confidence = "high" if confidence >= 0.8 else "medium" if confidence >= 0.5 else "low"

    return {
        "schema_version": result.get("schema_version", 1),
        "verdict": verdict,
        "repository": envelope.repository,
        "issue": envelope.issue,
        "pull_request": envelope.pull_request,
        "reviewed_head_sha": envelope.head_sha,
        "criteria": criteria,
        "findings": findings,
        "tests": tests,
        "confidence": confidence,
        "summary": result.get("summary"),
        "escalation_reason": result.get("escalation_reason", result.get("escalation")),
    }


def audit_tool_scope(log_path: Path, envelope: Envelope) -> None:
    checkout = Path(envelope.checkout).resolve()
    trusted_tool_roots = (Path.home() / ".local/bin", Path.home() / "bin")
    forbidden_commands = re.compile(
        r"(^|[;&|]\s*)(gh|git\s+(?:push|commit|checkout|switch|reset|clean|rm)|"
        r"rm|rmdir|unlink|shred|sed\s+-i)(?:\s|$)"
    )
    absolute_path = re.compile(r"/(?:home|tmp|var|opt|srv)/[^\s'\";|&<>\\]+")
    violations: list[str] = []
    for line_number, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReviewError(f"invalid event JSON at line {line_number}") from exc
        if event.get("type") != "tool_use":
            continue
        part = event.get("part", {})
        tool = str(part.get("tool", ""))
        state = part.get("state", {})
        if state.get("status") not in {None, "completed"}:
            continue
        inputs = state.get("input", {})
        if not isinstance(inputs, dict):
            continue
        if tool in {"github", "gitea"} or tool.startswith(("github_", "gitea_")):
            violations.append(f"line {line_number}: prohibited GitHub/Gitea tool {tool}")
        # These authenticated memory tools write evidence records, never files or
        # repository state. Source references can legitimately name other projects.
        from .memory import WORKER_TOOLS
        if tool in {"memory_" + name for name in WORKER_TOOLS}:
            continue
        if tool.startswith("memory_"):
            violations.append(f"line {line_number}: prohibited memory tool {tool}")
        command = str(inputs.get("command", inputs.get("cmd", "")))
        if command and forbidden_commands.search(command):
            violations.append(f"line {line_number}: prohibited command")
        values = [str(value) for value in inputs.values() if isinstance(value, str)]
        for value in values:
            for match in absolute_path.findall(value):
                raw_path = match.rstrip(".,:)]}" )
                # Container /tmp is a distinct tmpfs. Host symlink resolution does
                # not describe it. Normalize traversal lexically before allowance.
                lexical = posixpath.normpath(raw_path)
                if envelope.container_tmpfs and (lexical == '/tmp' or lexical.startswith('/tmp/')):
                    continue
                candidate = Path(raw_path).resolve(strict=False)
                harness_roots = [
                    Path(path).resolve()
                    for path in (envelope.scratch_dir, envelope.runtime_home)
                    if path
                ]
                allowed_roots = [*harness_roots, *trusted_tool_roots]
                if any(candidate == root or root in candidate.parents for root in allowed_roots):
                    continue
                try:
                    candidate.relative_to(checkout)
                except ValueError:
                    violations.append(f"line {line_number}: path outside checkout: {candidate}")
    if violations:
        raise ReviewError("tool-scope audit failed: " + "; ".join(violations[:10]))


def verify_checkout_unchanged(envelope: Envelope, run: Run = run_command) -> None:
    checkout = Path(envelope.checkout)
    root = Path(run(["git", "rev-parse", "--show-toplevel"], checkout).strip()).resolve()
    if root != checkout.resolve():
        raise ReviewError("checkout root changed during review")
    if run(["git", "rev-parse", "HEAD"], checkout).strip() != envelope.head_sha:
        raise ReviewError("checkout HEAD changed during review")
    if run(["git", "branch", "--show-current"], checkout).strip() != envelope.branch:
        raise ReviewError("checkout branch changed during review")
    if run(["git", "status", "--porcelain=v1"], checkout).strip():
        raise ReviewError("checkout was mutated during review")


def validate_result(result: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    result.setdefault("escalation_reason", None)
    required = {
        "schema_version", "verdict", "repository", "issue", "pull_request",
        "reviewed_head_sha", "criteria", "findings", "tests", "confidence",
        "summary", "escalation_reason",
    }
    missing = sorted(required - result.keys())
    if missing:
        raise ReviewError(f"structured result is missing fields: {missing}")
    if result["schema_version"] != 1:
        raise ReviewError("unsupported result schema version")
    verdict = result["verdict"]
    if verdict not in VERDICTS:
        raise ReviewError(f"invalid verdict: {verdict}")
    identities = {
        "repository": envelope.repository,
        "issue": envelope.issue,
        "pull_request": envelope.pull_request,
        "reviewed_head_sha": envelope.head_sha,
    }
    for field, expected in identities.items():
        if result[field] != expected:
            raise ReviewError(f"result identity mismatch for {field}")
    for field in ("criteria", "findings", "tests"):
        if not isinstance(result[field], list):
            raise ReviewError(f"{field} must be a list")
    for test in result["tests"]:
        if not isinstance(test, dict) or test.get("provenance") not in {"harness-observed", "reviewer-observed", "task-reported"}:
            raise ReviewError("every test requires harness-observed, reviewer-observed, or task-reported provenance")
        if test["provenance"] == "reviewer-observed" and not isinstance(test.get("exit_code"), int):
            raise ReviewError("reviewer-observed tests require an integer exit_code")
        if "exit_code" in test and (
            not isinstance(test["exit_code"], int) or isinstance(test["exit_code"], bool)
            or not 0 <= test["exit_code"] <= 255
        ):
            raise ReviewError("test exit_code must be an integer from zero through 255")
    blockers = [f for f in result["findings"] if isinstance(f, dict) and f.get("severity") == "blocker"]
    if result["confidence"] not in {"high", "medium", "low"}:
        raise ReviewError("confidence must be high, medium, or low")
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        raise ReviewError("summary must be non-empty text")
    if verdict == "APPROVE" and blockers:
        raise ReviewError("APPROVE cannot contain blocker findings")
    if verdict == "ESCALATE" and not result["escalation_reason"]:
        raise ReviewError("ESCALATE requires an escalation reason")
    return result


def render_comment(result: dict[str, Any], envelope: Envelope, run_id: str) -> str:
    marker = f"<!-- codex-review:{run_id}:{envelope.head_sha} -->"
    lines = [
        marker,
        "## Automated independent review",
        "",
        f"**Verdict:** `{result['verdict']}`",
        f"**Reviewed commit:** `{envelope.head_sha}`",
        f"**Confidence:** `{result['confidence']}`",
        "",
        str(result["summary"]),
    ]
    findings = result.get("findings", [])
    if findings:
        lines.extend(["", "### Findings", ""])
        for finding in findings:
            if isinstance(finding, dict):
                severity = finding.get("severity", "unspecified")
                message = finding.get("message", finding.get("summary", "unspecified finding"))
                lines.append(f"- **{severity}:** {message}")
            else:
                lines.append(f"- {finding}")
    lines.extend(["", "_Published deterministically from a validated reviewer artifact._", ""])
    return "\n".join(lines)


def _current_pr_sha(envelope: Envelope, run: Run) -> str:
    data = _json_command(
        run,
        ["gh", "pr", "view", str(envelope.pull_request), "--repo", envelope.repository, "--json", "headRefOid,state"],
    )
    if data.get("state") != "OPEN":
        raise ReviewError("pull request is no longer open")
    return str(data.get("headRefOid") or "")


def publish_result(
    result: dict[str, Any],
    envelope: Envelope,
    run_id: str,
    run: Run = run_command,
) -> dict[str, Any]:
    if _current_pr_sha(envelope, run) != envelope.head_sha:
        raise ReviewError("stale review: pull-request head changed")
    existing = _json_command(
        run,
        ["gh", "pr", "view", str(envelope.pull_request), "--repo", envelope.repository, "--json", "comments"],
    )
    marker = f"<!-- codex-review:{run_id}:{envelope.head_sha} -->"
    matches = [c for c in existing.get("comments", []) if marker in str(c.get("body", ""))]
    if len(matches) > 1:
        raise ReviewError("multiple comments already use this review marker")
    comment_url = matches[0].get("url") if matches else None
    if not matches:
        body = render_comment(result, envelope, run_id)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(body)
            body_path = Path(handle.name)
        try:
            comment_url = run(
                ["gh", "pr", "comment", str(envelope.pull_request), "--repo", envelope.repository, "--body-file", str(body_path)],
            ).strip()
        finally:
            body_path.unlink(missing_ok=True)

    target = "status/review-passed" if result["verdict"] == "APPROVE" else "status/blocked"
    run(
        [
            "gh", "issue", "edit", str(envelope.issue), "--repo", envelope.repository,
            "--remove-label", "status/review", "--add-label", target,
        ],
    )
    issue_data = _json_command(
        run,
        ["gh", "issue", "view", str(envelope.issue), "--repo", envelope.repository, "--json", "labels"],
    )
    labels = {item["name"] for item in issue_data.get("labels", [])}
    workflow = labels & WORKFLOW_LABELS
    if workflow != {target}:
        raise ReviewError(f"label postcondition failed: expected {target}, observed {sorted(workflow)}")
    return {"comment_url": comment_url, "label": target, "head_sha": envelope.head_sha}


def load_envelope(path: Path) -> Envelope:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Envelope(**data)


def incomplete_result(envelope: Envelope, reason: str) -> dict[str, Any]:
    if reason not in INCOMPLETE_REASONS:
        raise ReviewError(f"invalid incomplete reason: {reason}")
    return {
        "schema_version": 1,
        "status": "incomplete",
        "reason": reason,
        "repository": envelope.repository,
        "issue": envelope.issue,
        "pull_request": envelope.pull_request,
        "reviewed_head_sha": envelope.head_sha,
        "published": False,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--checkout", type=Path, required=True)
    prepare.add_argument("--repository", required=True)
    prepare.add_argument("--issue", type=int, required=True)
    prepare.add_argument("--pr", type=int, required=True)
    prepare.add_argument("--task-file", type=Path, required=True)
    prepare.add_argument("--envelope-out", type=Path, required=True)
    prepare.add_argument("--scratch-dir", type=Path)
    prepare.add_argument("--runtime-home", type=Path)
    prepare.add_argument("--investigative", action="store_true")
    prepare.add_argument("--container-tmpfs", action="store_true")

    finalize = sub.add_parser("finalize")
    finalize.add_argument("--envelope", type=Path, required=True)
    finalize.add_argument("--log", type=Path, required=True)
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--result-out", type=Path, required=True)
    finalize.add_argument("--no-publish", action="store_true")

    incomplete = sub.add_parser("incomplete")
    incomplete.add_argument("--envelope", type=Path, required=True)
    incomplete.add_argument("--reason", required=True)
    incomplete.add_argument("--result-out", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            if args.container_tmpfs and not args.investigative:
                raise ReviewError('--container-tmpfs requires --investigative')
            envelope = build_envelope(
                args.checkout, args.repository, args.issue, args.pr,
                scratch_dir=args.scratch_dir, runtime_home=args.runtime_home,
            )
            if args.investigative:
                from dataclasses import replace
                envelope = replace(envelope, investigative=True, container_tmpfs=args.container_tmpfs)
            packet = build_reviewer_evidence_packet(
                Path(envelope.checkout), envelope.repository, envelope.issue,
                envelope.pull_request, envelope.base_sha, envelope.head_sha,
                run_command, {} if args.investigative else dict(discover_test_executables()),
            )
            write_json(args.envelope_out, envelope.as_dict())
            sys.stdout.write(prepare_prompt(envelope, args.task_file.read_text(encoding="utf-8"), packet))
        elif args.command == "finalize":
            envelope = load_envelope(args.envelope)
            audit_tool_scope(args.log, envelope)
            verify_checkout_unchanged(envelope)
            result = validate_result(canonicalize_result(parse_result(args.log), envelope), envelope)
            publication = ({"published": False, "head_sha": envelope.head_sha}
                           if args.no_publish else publish_result(result, envelope, args.run_id))
            write_json(args.result_out, {"result": result, "publication": publication})
            print(json.dumps(publication, sort_keys=True))
        else:
            result = incomplete_result(load_envelope(args.envelope), args.reason)
            write_json(args.result_out, result)
            print("REVIEW_INCOMPLETE_JSON=" + json.dumps(result, sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError, ReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
