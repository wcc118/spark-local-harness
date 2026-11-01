"""Durable assignment ledger for issue-bound worker dispatch."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PLAN_VERSION = 1


def validate_repository(repo: str) -> tuple[str, str]:
    """Validate repository format and return (owner, name)."""
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("repository must be in OWNER/REPO format")
    return tuple(repo.split("/"))


def validate_positive_int(value: Any) -> int:
    """Validate that value is a positive integer (not bool)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("issue must be a positive integer")
    if value <= 0:
        raise ValueError("issue must be positive")
    return value


def validate_nonempty_string(value: Any, name: str) -> str:
    """Validate that value is a non-empty string."""
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _count_prior_eligible(records: tuple[dict[str, Any], ...]) -> int:
    """Count prior eligible assignments globally in the ledger."""
    count = 0
    for record in records:
        if record.get("decision") == "eligible":
            count += 1
    return count


def _get_models(config: dict[str, Any]) -> tuple[str, str]:
    """Get standard and deepseek model names from config."""
    if not isinstance(config, dict):
        raise ValueError("invalid route configuration")
    routes = config.get("routes", {})
    if not isinstance(routes, dict) or any(not isinstance(routes.get(k, {}), dict) for k in ("coder/standard", "coder/deepseek")):
        raise ValueError("invalid route configuration")
    standard_model = routes.get("coder/standard", {}).get("model")
    deepseek_model = routes.get("coder/deepseek", {}).get("model")

    if not standard_model or not isinstance(standard_model, str) or not standard_model.strip():
        raise ValueError("config.routes['coder/standard'].model must be a non-empty string")
    if not deepseek_model or not isinstance(deepseek_model, str) or not deepseek_model.strip():
        raise ValueError("config.routes['coder/deepseek'].model must be a non-empty string")
    if standard_model.strip() == deepseek_model.strip():
        raise ValueError("standard and deepseek models must be distinct")

    return standard_model.strip(), deepseek_model.strip()


def _build_role_env(sequence, standard_model, deepseek_model, is_exception):
    if is_exception:
        return None
    return {
        "coder": {"CODEX_LOCAL_TIER": "deepseek" if sequence % 2 else "standard",
                  "CODEX_LOCAL_MODEL": deepseek_model if sequence % 2 else standard_model},
        "reviewer": {"CODEX_LOCAL_TIER": "standard",
                     "CODEX_LOCAL_MODEL": standard_model if sequence % 2 else deepseek_model},
    }


def _build_assignment_record(
    repository: str,
    issue: int,
    task_class: str,
    decision: str,
    reason: str,
    sequence: int,
    standard_model: str,
    deepseek_model: str,
    is_exception: bool,
) -> dict[str, Any]:
    """Build the assignment record dict."""
    now = datetime.now(timezone.utc).isoformat()

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_version": PLAN_VERSION,
        "repository": repository,
        "issue": issue,
        "task_class": task_class,
        "decision": decision,
        "reason": reason,
        "observed_at": now,
    }

    if is_exception:
        record["sequence"] = None
        record["coder_model"] = None
        record["reviewer_model"] = None
        record["role_env"] = None
    else:
        record["sequence"] = sequence
        record["coder_model"] = deepseek_model if sequence % 2 == 1 else standard_model
        record["reviewer_model"] = deepseek_model if sequence % 2 == 0 else standard_model
        record["role_env"] = _build_role_env(sequence, standard_model, deepseek_model, False)

    return record


def assign(
    path: Path,
    config: dict[str, Any],
    repository: str,
    issue: int,
    task_class: str,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    """Assign a worker to an issue and record it in the ledger.

    Returns the recorded assignment dict.
    """
    # Validate inputs
    validate_nonempty_string(repository, "repository")
    validate_nonempty_string(task_class, "task_class")
    validate_nonempty_string(reason, "reason")
    validate_positive_int(issue)
    validate_repository(repository)

    if decision not in ("eligible", "exception"):
        raise ValueError("decision must be exactly 'eligible' or 'exception'")

    is_exception = decision == "exception"

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        os.fchmod(handle.fileno(), 0o600)
        records = _read_records(handle)
        for record in records:
            if (record["repository"], record["issue"]) != (repository, issue):
                continue
            if all(record.get(key) == value for key, value in {
                "task_class": task_class, "decision": decision, "reason": reason,
            }.items()):
                return record
            raise ValueError(f"conflicting assignment exists for {repository}#{issue}")
        # Sequence, identity lookup and append are one locked transaction.
        models = (None, None) if is_exception else _get_models(config)
        record = _build_assignment_record(
            repository, issue, task_class, decision, reason,
            _count_prior_eligible(tuple(records)), *models, is_exception,
        )
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        return record


def _read_records(handle):
    records = []
    identities = set()
    sequence = 0
    for number, line in enumerate(handle, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at line {number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"invalid record at line {number}")
        required = {"schema_version", "plan_version", "repository", "issue", "task_class",
                    "decision", "reason", "observed_at", "sequence", "coder_model", "reviewer_model", "role_env"}
        if (set(record) != required or any(type(record.get(key)) is not int
                or record[key] != 1 for key in ("schema_version", "plan_version"))):
            raise ValueError(f"invalid record schema at line {number}")
        for key in ("task_class", "reason", "observed_at"):
            validate_nonempty_string(record[key], key)
        validate_repository(record.get("repository"))
        validate_positive_int(record.get("issue"))
        identity = (record["repository"], record["issue"])
        if identity in identities or record.get("decision") not in {"eligible", "exception"}:
            raise ValueError(f"invalid or duplicate assignment at line {number}")
        identities.add(identity)
        expected = sequence if record["decision"] == "eligible" else None
        if record.get("sequence") != expected or isinstance(record.get("sequence"), bool):
            raise ValueError(f"invalid sequence at line {number}")
        if expected is not None:
            for role in ("coder", "reviewer"):
                validate_nonempty_string(record[role + "_model"], role + " model")
            if record["coder_model"] == record["reviewer_model"]:
                raise ValueError(f"models must be distinct at line {number}")
            expected_env = {
                "coder": {"CODEX_LOCAL_TIER": "deepseek" if expected % 2 else "standard",
                          "CODEX_LOCAL_MODEL": record["coder_model"]},
                "reviewer": {"CODEX_LOCAL_TIER": "standard",
                             "CODEX_LOCAL_MODEL": record["reviewer_model"]},
            }
            if record["role_env"] != expected_env:
                raise ValueError(f"role environment mismatch at line {number}")
            sequence += 1
        elif any(record[key] is not None for key in ("coder_model", "reviewer_model", "role_env")):
            raise ValueError(f"exception contains route at line {number}")
        records.append(record)
    return records


def list_assignments(path: Path) -> tuple[dict[str, Any], ...]:
    """List all assignment records from the ledger."""
    if not path.exists():
        return ()

    with path.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        return tuple(_read_records(handle))


def digest(assignments, views, workflow, annotations=()):
    """Join existing evidence by exact repository/issue; never infer reviewer merit."""
    identities = {}
    for event in workflow:
        if event.run_id and event.repository:
            identities.setdefault(event.run_id, set()).add((event.repository, event.issue))
    rows = []
    for assignment in assignments[-64:]:
        key = (assignment["repository"], assignment["issue"])
        runs = []
        for view in views:
            if identities.get(view.run_id) != {key}:
                continue
            planned = assignment.get(f"{view.role}_model")
            runs.append({
                "run_id": view.run_id, "role": view.role, "actual_model": view.model,
                "planned_model": planned,
                "identity_match": None if not planned or not view.model else planned == view.model,
                "delivery_ready": view.delivery_ready,
                "acceptance": view.effective_outcome if view.adjudicated else None,
                "corrections": view.correction_cycle, "intervention": view.intervention_depth,
                "failure_class": view.failure_class, "lifecycle": view.lifecycle_state,
                "local_tokens": view.local_tokens, "frontier_tokens": view.frontier_tokens,
                "decision": view.codex_decision, "evidence_refs": list(view.evidence_refs),
            })
        ids = {run["run_id"] for run in runs}
        judgments = [a for a in annotations if a.get("source") in {"codex", "operator"}
                     and a.get("kind") in {"reviewer-catch", "reviewer-miss", "reviewer-false-alarm",
                                            "learning-promote", "learning-reject", "learning-hold"}
                     and ids.intersection(a.get("run_ids", []))]
        rows.append({"assignment": assignment, "runs": runs[-32:],
                     "adjudicated_observations": judgments[-16:]})
    return {"schema_version": 1, "issues": rows,
            "unjoined_runs": sum(len(identities.get(v.run_id, ())) != 1 for v in views),
            "limits": "Observational evidence, not a controlled model comparison. Missing evidence is unknown; no automatic promotion."}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Manage issue-bound worker assignments in a durable ledger"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # assign command
    assign_parser = subparsers.add_parser("assign", help="Record a new assignment")
    assign_parser.add_argument("--ledger", type=Path, required=True, help="Path to JSONL ledger file")
    assign_parser.add_argument("--config", type=Path, required=True, help="Path to worker config JSON file")
    assign_parser.add_argument("--repository", required=True, help="Repository in OWNER/REPO format")
    assign_parser.add_argument("--issue", type=int, required=True, help="Issue number")
    assign_parser.add_argument("--task-class", required=True, help="Task classification string")
    assign_parser.add_argument("--decision", required=True, choices=["eligible", "exception"], help="Assignment decision")
    assign_parser.add_argument("--reason", required=True, help="Reason for the assignment")

    # list command
    list_parser = subparsers.add_parser("list", help="List all assignment records")
    list_parser.add_argument("--ledger", type=Path, required=True, help="Path to JSONL ledger file")

    digest_parser = subparsers.add_parser("digest", help="Join existing outcome evidence")
    for name in ("ledger", "runs", "adjudications", "lifecycle", "workflow", "annotations"):
        digest_parser.add_argument("--" + name, type=Path, required=True)

    args = parser.parse_args(argv)

    try:
        if args.command == "assign":
            # Load config
            if not args.config.exists():
                raise ValueError(f"config file not found: {args.config}")
            config_content = args.config.read_text(encoding="utf-8")
            config = json.loads(config_content)

            result = assign(
                path=args.ledger,
                config=config,
                repository=args.repository,
                issue=args.issue,
                task_class=args.task_class,
                decision=args.decision,
                reason=args.reason,
            )
            print(json.dumps(result, sort_keys=True))
            return 0

        elif args.command == "digest":
            from .health import read_jsonl
            from .diagnostics import read_records, Adjudication, LifecycleEvent
            from .read_model import build_run_views
            from .workflow import read_workflow_records
            from .strategy import read_annotations
            views = build_run_views(read_jsonl(args.runs),
                                    read_records(args.adjudications, Adjudication),
                                    read_records(args.lifecycle, LifecycleEvent))
            print(json.dumps(digest(list_assignments(args.ledger), views,
                                   read_workflow_records(args.workflow),
                                   read_annotations(args.annotations)), sort_keys=True))
            return 0

        elif args.command == "list":
            records = list_assignments(args.ledger)
            for record in records:
                print(json.dumps(record, sort_keys=True))
            return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
