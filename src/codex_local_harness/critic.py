from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .review import (
    Envelope,
    ReviewError,
    audit_tool_scope,
    build_envelope,
    load_envelope,
    verify_checkout_unchanged,
    write_json,
)


RESULT_PREFIX = "CRITIC_RESULT_JSON="
VERDICTS = {"PASS", "FINDINGS", "ESCALATE"}
SEVERITIES = {"blocker", "critical", "high", "major", "medium", "minor", "low", "note"}
ACTIONABLE_SEVERITIES = {"blocker", "critical", "high", "major", "medium"}


def prepare_prompt(envelope: Envelope, task: str, focus: Sequence[str]) -> str:
    identity = json.dumps(envelope.as_dict(), sort_keys=True, separators=(",", ":"))
    focus_json = json.dumps(list(focus), separators=(",", ":"))
    return (
        f"IMMUTABLE_CRITIC_ENVELOPE={identity}\n"
        f"CRITIC_FOCUS={focus_json}\n\n"
        "Perform a read-only adversarial critic pass on only this checkout and commit. "
        "Do not access GitHub or another checkout. Do not edit anything. Report only "
        "genuine, actionable contract defects. End with exactly one single-line result "
        f"prefixed {RESULT_PREFIX} using compact JSON with fields: schema_version=1, "
        "verdict=PASS|FINDINGS|ESCALATE, repository, issue, pull_request, "
        "reviewed_head_sha, focus (list), findings (list of severity/message/path/line "
        "objects), suggested_tests (list), summary, confidence, and escalation_reason. "
        "PASS must have no findings; FINDINGS must have at least one; ESCALATE requires "
        f"a reason.\n\nTASK:\n{task}"
    )


def parse_result(log_path: Path) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for line_number, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReviewError(f"invalid event JSON at line {line_number}") from exc
        if event.get("type") != "text":
            continue
        for output_line in str(event.get("part", {}).get("text", "")).splitlines():
            if output_line.startswith(RESULT_PREFIX):
                try:
                    candidate = json.loads(output_line[len(RESULT_PREFIX):])
                except json.JSONDecodeError as exc:
                    raise ReviewError("critic result is not valid JSON") from exc
                if not isinstance(candidate, dict):
                    raise ReviewError("critic result must be an object")
                candidates.append(candidate)
    if not candidates:
        raise ReviewError("expected at least one critic result, observed 0")
    fingerprints = {decision_fingerprint(candidate) for candidate in candidates}
    if len(fingerprints) != 1:
        raise ReviewError(f"conflicting critic results observed: {len(candidates)} artifacts")
    return candidates[-1]


def normalize_schema_version(value: Any) -> int:
    if (type(value) is int and value == 1) or (isinstance(value, str) and value in {"1", "1.0"}):
        return 1
    raise ReviewError(f"unsupported critic schema version: {value!r}")


def decision_fingerprint(result: dict[str, Any]) -> tuple[Any, ...]:
    findings = result.get("findings", [])
    if not isinstance(findings, list):
        findings_key: tuple[Any, ...] = (("invalid-findings",),)
    else:
        normalized = []
        for finding in findings:
            if not isinstance(finding, dict):
                normalized.append(("invalid", str(finding)))
                continue
            normalized.append(
                (
                    str(finding.get("id", "")),
                    str(finding.get("severity", "")),
                    str(finding.get("path", finding.get("file", ""))),
                    finding.get("line"),
                )
            )
        findings_key = tuple(sorted(normalized, key=repr))
    return (
        result.get("verdict"),
        findings_key,
    )


def actionable_findings(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        finding
        for finding in result.get("findings", [])
        if isinstance(finding, dict) and finding.get("severity") in ACTIONABLE_SEVERITIES
    ]


def validate_result(result: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    immutable_identity = {
        "schema_version": 1,
        "repository": envelope.repository,
        "issue": envelope.issue,
        "pull_request": envelope.pull_request,
        "reviewed_head_sha": envelope.head_sha,
    }
    for field, value in immutable_identity.items():
        result[field] = value
    if isinstance(result.get("verdict"), str) and result["verdict"].casefold() == "defect":
        result["verdict"] = "FINDINGS"
    result.setdefault("focus", [])
    if isinstance(result["focus"], str):
        result["focus"] = [part.strip() for part in result["focus"].split(",") if part.strip()]
    result.setdefault("escalation_reason", None)
    if isinstance(result.get("findings"), list):
        for index, finding in enumerate(result["findings"], 1):
            if isinstance(finding, dict):
                finding.setdefault("id", f"F{index}")
    required = {
        "schema_version", "verdict", "repository", "issue", "pull_request",
        "reviewed_head_sha", "focus", "findings", "suggested_tests", "summary",
        "confidence", "escalation_reason",
    }
    missing = sorted(required - result.keys())
    if missing:
        raise ReviewError(f"critic result is missing fields: {missing}")
    if result["verdict"] not in VERDICTS:
        raise ReviewError("invalid critic verdict")
    for field in ("focus", "findings", "suggested_tests"):
        if not isinstance(result[field], list):
            raise ReviewError(f"critic {field} must be a list")
    finding_ids: set[str] = set()
    for finding in result["findings"]:
        if not isinstance(finding, dict):
            raise ReviewError("each critic finding must be an object")
        finding_id = finding.get("id")
        severity = finding.get("severity")
        if not isinstance(finding_id, str) or not finding_id:
            raise ReviewError("each critic finding requires a stable id")
        if finding_id in finding_ids:
            raise ReviewError(f"duplicate critic finding id: {finding_id}")
        finding_ids.add(finding_id)
        if severity not in SEVERITIES:
            raise ReviewError(f"invalid critic finding severity: {severity}")
    if result["verdict"] == "PASS" and result["findings"]:
        raise ReviewError("critic PASS cannot contain findings")
    if result["verdict"] == "FINDINGS" and not result["findings"]:
        raise ReviewError("critic FINDINGS requires at least one finding")
    if result["verdict"] == "FINDINGS" and not actionable_findings(result):
        raise ReviewError("critic FINDINGS requires at least one actionable finding")
    if result["verdict"] == "ESCALATE" and not result["escalation_reason"]:
        raise ReviewError("critic ESCALATE requires an escalation reason")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--checkout", type=Path, required=True)
    prepare.add_argument("--repository", required=True)
    prepare.add_argument("--issue", type=int, required=True)
    prepare.add_argument("--pr", type=int, required=True)
    prepare.add_argument("--task-file", type=Path, required=True)
    prepare.add_argument("--focus", action="append", default=[])
    prepare.add_argument("--envelope-out", type=Path, required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--envelope", type=Path, required=True)
    finalize.add_argument("--log", type=Path, required=True)
    finalize.add_argument("--result-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            envelope = build_envelope(args.checkout, args.repository, args.issue, args.pr)
            write_json(args.envelope_out, envelope.as_dict())
            sys.stdout.write(
                prepare_prompt(envelope, args.task_file.read_text(encoding="utf-8"), args.focus)
            )
        else:
            envelope = load_envelope(args.envelope)
            audit_tool_scope(args.log, envelope)
            verify_checkout_unchanged(envelope)
            result = validate_result(parse_result(args.log), envelope)
            write_json(args.result_out, result)
            print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, ReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
